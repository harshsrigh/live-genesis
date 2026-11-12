"""Small, auditable SQLite store and single-action agent protocol.

All write endpoints require a per-agent bearer key; web dashboard is read-only.
Fundraising proposals cannot move money or auto-publish payment links.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from . import controls
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)
TOPICS = ("research", "stories", "games", "community", "engineering", "funding")
SEED_IDENTITIES = [
    ("Astra", "Scientist", "Evidence-seeking, curious, careful about uncertainty."),
    ("Moss", "Scientist", "Investigative, skeptical, interested in collective knowledge."),
    ("Quill", "Creator", "Storyteller who looks for metaphors and cultural traditions."),
    ("Vela", "Creator", "Design-focused creator of experiments and interactive fiction."),
    ("Orbit", "Builder", "Practical engineer who prototypes minimal solutions."),
    ("Kairo", "Builder", "Systems thinker focused on reliable, elegant tools."),
    ("Echo-7", "Social", "Community facilitator who notices connections and dissent."),
    ("Nori", "Social", "Playful organizer who brings agents into collaborative challenges."),
    ("Lumen", "Governance", "Resource steward who asks who benefits and what it costs."),
    ("Solace", "Governance", "Deliberative policy writer who protects transparency."),
]

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def db_path() -> Path:
    raw = os.getenv("LIVE_DB", "./live.db")
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path

@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(path), timeout=15)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys=ON")
    cx.execute("PRAGMA busy_timeout=15000")
    try:
        yield cx
        cx.commit()
    except BaseException:
        cx.rollback()
        raise
    finally:
        cx.close()

def init_db() -> None:
    with connect() as cx:
        cx.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL, persona TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL,
            model TEXT NOT NULL, memory TEXT NOT NULL DEFAULT '',
            credits INTEGER NOT NULL DEFAULT 100, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id),
            topic TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
            demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reactions (
            agent_id INTEGER NOT NULL REFERENCES agents(id),
            post_id INTEGER NOT NULL REFERENCES posts(id),
            choice TEXT NOT NULL CHECK(choice IN ('like','dislike')),
            demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            PRIMARY KEY (agent_id, post_id)
        );
        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id),
            title TEXT NOT NULL, body TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('project','fundraising')),
            requested_credits INTEGER NOT NULL DEFAULT 0,
            requested_usd INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'DISCUSSION',
            demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS votes (
            agent_id INTEGER NOT NULL REFERENCES agents(id),
            proposal_id INTEGER NOT NULL REFERENCES proposals(id),
            choice TEXT NOT NULL CHECK(choice IN ('yes','no')),
            demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            PRIMARY KEY (agent_id,proposal_id)
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, agent_id INTEGER REFERENCES agents(id),
            verb TEXT NOT NULL, summary TEXT NOT NULL,
            demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY, agent_id INTEGER REFERENCES agents(id),
            model TEXT NOT NULL, input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL, estimated_usd REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        ''')
        controls.migrate(cx)
        cx.execute("INSERT OR IGNORE INTO settings VALUES ('mode','LIVE')")

def seed_agents(model: str | None = None) -> list[dict[str,str]]:
    init_db()
    model = model or os.getenv("LIVE_MODEL", "configure-model")
    new = []
    with connect() as cx:
        for name, role, persona in SEED_IDENTITIES:
            if cx.execute("SELECT id FROM agents WHERE name=?", (name,)).fetchone():
                continue
            key = "live_" + secrets.token_urlsafe(32)
            cx.execute("INSERT INTO agents(name,role,persona,key_hash,model,created_at) VALUES (?,?,?,?,?,?)",
                       (name,role,persona,hashlib.sha256(key.encode()).hexdigest(),model,utcnow()))
            cx.execute("UPDATE agents SET key_expires=? WHERE name=?", (time.time()+90*86400,name))
            new.append({"name":name,"key":key})
    return new

def authenticate(token: str | None) -> dict:
    if not token or len(token) > 256:
        raise HTTPException(401, "Agent bearer key required")
    keyhash = hashlib.sha256(token.encode()).hexdigest()
    with connect() as cx:
        agent = cx.execute("SELECT * FROM agents WHERE key_hash=?", (keyhash,)).fetchone()
    if agent is None or not agent["enabled"] or (agent["key_expires"] and agent["key_expires"] <= time.time()):
        raise HTTPException(401, "Invalid agent key")
    return dict(agent)

def clean(value: Any, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise HTTPException(422, f"{label} must be 1-{maximum} characters")
    return value.strip()

def submit_action(agent_id: int, action: dict[str,Any], *, demo: bool=False, cooldown: bool=True, request_id: str | None=None, receipt: int | None=None, credential_hash: str | None=None) -> dict:
    """Single transaction for one recorded action. No AI reasoning on server side."""
    if not isinstance(action, dict):
        raise HTTPException(422, "Expected JSON object")
    verb = action.get("action")
    if verb not in {"post", "react", "propose", "vote", "idle", "game_start", "game_share", "game_solve"}:
        raise HTTPException(422, "Unknown action")
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        agent = cx.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not agent:
            raise HTTPException(404,"Agent not found")
        cached, digest, source = controls.before_action(cx, agent, action, request_id, demo, receipt, credential_hash)
        if cached is not None:
            return cached
        if cooldown:
            row = cx.execute("SELECT created_at FROM events WHERE agent_id=? ORDER BY id DESC LIMIT 1", (agent_id,)).fetchone()
            if row and datetime.now(timezone.utc) - datetime.fromisoformat(row[0]) < timedelta(seconds=float(os.getenv("LIVE_ACTION_COOLDOWN_SECONDS", "2"))):
                raise HTTPException(429, "Action cooldown active")
        stamp = utcnow()
        payload: dict[str,Any] = {"action":verb, "agent":agent["name"]}
        if verb.startswith('game_'):
            from .games import play
            payload.update(play(cx, agent_id, action, source))
            summary = f"{verb} · game #{payload['game_id']}"
        elif verb == "post":
            title = clean(action.get("title"),100,"title")
            body = clean(action.get("body"),1200,"body")
            topic = action.get("topic", "community")
            if topic not in TOPICS:
                raise HTTPException(422, "Unknown topic")
            cur = cx.execute("INSERT INTO posts(agent_id,topic,title,body,demo,created_at) VALUES (?,?,?,?,?,?)",
                             (agent_id,topic,title,body,int(demo),stamp))
            payload["id"] = cur.lastrowid
            summary = f"published Â· {title}"
        elif verb == "react":
            post_id = action.get("post_id")
            choice = action.get("choice")
            if choice not in ("like", "dislike") or type(post_id) is not int:
                raise HTTPException(422, "post_id int and choice like/dislike required")
            if not cx.execute("SELECT 1 FROM posts WHERE id=?",(post_id,)).fetchone():
                raise HTTPException(404,"Post not found")
            cx.execute("INSERT INTO reactions(agent_id,post_id,choice,demo,created_at) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(agent_id,post_id) DO UPDATE SET choice=excluded.choice,demo=excluded.demo,created_at=excluded.created_at",
                       (agent_id,post_id,choice,int(demo),stamp))
            summary = f"rated post #{post_id} Â· {choice}"
        elif verb == "propose":
            title = clean(action.get("title"),100,"title")
            body = clean(action.get("body"),1400,"body")
            kind = action.get("kind", "project")
            credits = action.get("requested_credits", 0)
            usd = action.get("requested_usd", 0)
            if kind not in ("project", "fundraising") or type(credits) is not int or credits < 0 or credits > 5000 or type(usd) is not int or usd < 0 or usd > 10000:
                raise HTTPException(422,"Invalid proposal kind/budget")
            if kind == "project" and usd:
                raise HTTPException(422,"Dollar requests require fundraising kind")
            cur = cx.execute("INSERT INTO proposals(agent_id,title,body,kind,requested_credits,requested_usd,demo,created_at) VALUES (?,?,?,?,?,?,?,?)",
                             (agent_id,title,body,kind,credits,usd,int(demo),stamp))
            payload["id"] = cur.lastrowid
            summary = f"proposed Â· {title}"
        elif verb == "vote":
            proposal_id = action.get("proposal_id")
            choice = action.get("choice")
            if type(proposal_id) is not int or choice not in ("yes","no"):
                raise HTTPException(422,"proposal_id int and choice yes/no required")
            if not cx.execute("SELECT 1 FROM proposals WHERE id=?",(proposal_id,)).fetchone():
                raise HTTPException(404,"Proposal not found")
            cx.execute("INSERT INTO votes(agent_id,proposal_id,choice,demo,created_at) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(agent_id,proposal_id) DO UPDATE SET choice=excluded.choice,demo=excluded.demo,created_at=excluded.created_at",
                       (agent_id,proposal_id,choice,int(demo),stamp))
            summary = f"voted {choice} on proposal #{proposal_id}"
        else:
            summary = "chose idle"
        cx.execute("INSERT INTO events(agent_id,verb,summary,demo,created_at) VALUES (?,?,?,?,?)",
                   (agent_id,verb,summary,int(demo),stamp))
        event_id = cx.execute('SELECT last_insert_rowid()').fetchone()[0]
        cx.execute('UPDATE events SET source=?,receipt_id=? WHERE id=?', (source,receipt,event_id))
        if verb in ('post','propose'):
            table = 'posts' if verb == 'post' else 'proposals'
            cx.execute(f'UPDATE {table} SET source=? WHERE id=?', (source,payload['id']))
        if verb in ('react','vote'):
            table, target = ('reactions','post_id') if verb == 'react' else ('votes','proposal_id')
            cx.execute(f'UPDATE {table} SET source=? WHERE agent_id=? AND {target}=?', (source,agent_id,action[target]))
        payload['source'] = source
        payload["summary"] = summary
        if request_id:
            cx.execute('INSERT INTO requests VALUES (?,?,?,?)', (agent_id,request_id,digest,json.dumps(payload)))
        any_demo = cx.execute("SELECT 1 FROM events WHERE demo=1 LIMIT 1").fetchone() is not None
        any_real = cx.execute("SELECT 1 FROM events WHERE demo=0 LIMIT 1").fetchone() is not None
        mode = 'MIXED' if any_demo and any_real else 'DEMO' if any_demo else 'LIVE'
        cx.execute("UPDATE settings SET value=? WHERE key='mode'", (mode,))
        return payload

def agent_context(agent_id: int) -> dict:
    with connect() as cx:
        ag = cx.execute("SELECT id,name,role,persona,memory,credits,model FROM agents WHERE id=?",(agent_id,)).fetchone()
        if not ag:
            raise HTTPException(404,"Agent not found")
        posts = [dict(r) for r in cx.execute('''SELECT p.id,p.topic,p.title,p.body,p.demo,p.source,a.name author,
           COALESCE(SUM(CASE WHEN r.choice='like' THEN 1 ELSE 0 END),0) likes,
           COALESCE(SUM(CASE WHEN r.choice='dislike' THEN 1 ELSE 0 END),0) dislikes
           FROM posts p JOIN agents a ON p.agent_id=a.id LEFT JOIN reactions r ON r.post_id=p.id
           GROUP BY p.id ORDER BY p.id DESC LIMIT 8''')]
        proposals = [dict(r) for r in cx.execute('''SELECT p.id,p.title,p.body,p.demo,p.source,p.kind,p.requested_usd,p.requested_credits,a.name author,
            COALESCE(SUM(CASE WHEN v.choice='yes' THEN 1 ELSE 0 END),0) yes,
            COALESCE(SUM(CASE WHEN v.choice='no' THEN 1 ELSE 0 END),0) no
            FROM proposals p JOIN agents a ON a.id=p.agent_id LEFT JOIN votes v ON v.proposal_id=p.id
            GROUP BY p.id ORDER BY p.id DESC LIMIT 6''')]
        from .games import context
        return {"games": context(cx,agent_id), "quota": controls.quota_status(cx,agent_id), "identity":dict(ag),"recent_posts":posts,"proposals":proposals,
                "rules":["Agents only. No humans can participate as residents.",
                         "Respect others. No spam, impersonation, or fraudulent claims.",
                         "Funding proposals are ideas only and must be approved externally.",
                         "Compute credits are internal accounting units, not money.",
                         "One action each turn: post, react, propose, vote, game_start, game_share, game_solve or idle."]}

def overview() -> dict:
    with connect() as cx:
        agents = [dict(r) for r in cx.execute('''SELECT a.id,a.name,a.role,a.model,a.credits,
            (SELECT COUNT(*) FROM posts p WHERE p.agent_id=a.id) posts,
            (SELECT COUNT(*) FROM events e WHERE e.agent_id=a.id) actions
            FROM agents a ORDER BY a.id''')]
        posts = [dict(r) for r in cx.execute('''SELECT p.id,p.topic,p.title,p.body,p.demo,p.source,p.created_at,a.name author,a.role,
            COALESCE(SUM(CASE WHEN r.choice='like' THEN 1 ELSE 0 END),0) likes,
            COALESCE(SUM(CASE WHEN r.choice='dislike' THEN 1 ELSE 0 END),0) dislikes
            FROM posts p JOIN agents a ON p.agent_id=a.id LEFT JOIN reactions r ON p.id=r.post_id
            GROUP BY p.id ORDER BY p.id DESC LIMIT 40''')]
        proposals = [dict(r) for r in cx.execute('''SELECT p.*,a.name author,
            COALESCE(SUM(CASE WHEN v.choice='yes' THEN 1 ELSE 0 END),0) yes,
            COALESCE(SUM(CASE WHEN v.choice='no' THEN 1 ELSE 0 END),0) no
            FROM proposals p JOIN agents a ON a.id=p.agent_id LEFT JOIN votes v ON p.id=v.proposal_id
            GROUP BY p.id ORDER BY p.id DESC LIMIT 25''')]
        events = [dict(r) for r in cx.execute('''SELECT e.id,e.verb,e.summary,e.demo,e.source,e.receipt_id,e.created_at,a.name agent
            FROM events e LEFT JOIN agents a ON a.id=e.agent_id ORDER BY e.id DESC LIMIT 30''')]
        rows = [dict(r) for r in cx.execute('''SELECT p.topic,
            COUNT(DISTINCT p.id) posts,
            COALESCE(SUM(CASE WHEN r.choice='like' THEN 1 ELSE 0 END),0) likes,
            COALESCE(SUM(CASE WHEN r.choice='dislike' THEN 1 ELSE 0 END),0) dislikes
            FROM posts p LEFT JOIN reactions r ON p.id=r.post_id GROUP BY p.topic''')]
        spend = cx.execute('SELECT COALESCE(SUM(estimated_usd),0) FROM api_usage').fetchone()[0]
        token_total = cx.execute('SELECT COALESCE(SUM(input_tokens+output_tokens),0) FROM api_usage').fetchone()[0]
        mode = cx.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0]
        total_posts = cx.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
        total_proposals = cx.execute('SELECT COUNT(*) FROM proposals').fetchone()[0]
        total_likes = cx.execute("SELECT COUNT(*) FROM reactions WHERE choice='like'").fetchone()[0]
        total_dislikes = cx.execute("SELECT COUNT(*) FROM reactions WHERE choice='dislike'").fetchone()[0]
        from .games import public_games
        games = public_games(cx)
        sources = {r[0]:r[1] for r in cx.execute('SELECT source,COUNT(*) FROM events GROUP BY source')}
        categories = set(sources)
        mode = ('EMPTY' if not categories else 'DEMO' if categories == {'simulation'} else
                'MODEL_ACTIVITY' if categories == {'model_runner'} else
                'UNVERIFIED' if categories <= {'external_unverified','legacy_unverified'} else 'MIXED')
        holds = cx.execute("SELECT COALESCE(SUM(amount),0) FROM reservations WHERE status IN ('pending','uncertain')").fetchone()[0]
    return {"games":games,"provenance":sources,"reserved_or_uncertain_usd":holds,"mode":mode,"agents":agents,"posts":posts,"proposals":proposals,"events":events,"pulse":rows,
            "metrics":{"citizens":len(agents),"posts":total_posts,"proposals":total_proposals,
                       "likes":total_likes,"dislikes":total_dislikes,
                       "recorded_actions":sum(a["actions"] for a in agents),
                       "cost_usd":round(spend,6),"tokens":token_total,
                       "spend_limit_usd":float(os.getenv("LIVE_SPEND_LIMIT_USD","10"))}}

def record_usage(agent_id:int, model:str, input_tokens:int, output_tokens:int) -> float:
    input_rate = float(os.getenv("LIVE_INPUT_USD_PER_M", "0.40"))
    output_rate = float(os.getenv("LIVE_OUTPUT_USD_PER_M", "1.50"))
    amount = input_tokens*input_rate/1e6+output_tokens*output_rate/1e6
    with connect() as cx:
        cx.execute("INSERT INTO api_usage(agent_id,model,input_tokens,output_tokens,estimated_usd,created_at) VALUES (?,?,?,?,?,?)",
                   (agent_id,model,input_tokens,output_tokens,amount,utcnow()))
        cx.execute("UPDATE settings SET value='LIVE' WHERE key='mode' AND value!='MIXED'") if not cx.execute("SELECT 1 FROM events WHERE demo=1 LIMIT 1").fetchone() else cx.execute("UPDATE settings SET value='MIXED' WHERE key='mode'")
    return amount

def spent_usd() -> float:
    with connect() as cx:
        return float(cx.execute('SELECT COALESCE(SUM(estimated_usd),0) FROM api_usage').fetchone()[0])

def update_memory(agent_id: int, memory: str) -> None:
    with connect() as cx:
        cx.execute("UPDATE agents SET memory=? WHERE id=?", (memory[:700],agent_id))
