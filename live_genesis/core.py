"""Small, auditable SQLite store and single-action agent protocol.

All write endpoints require a per-agent bearer key; web dashboard is read-only.
Fundraising proposals cannot move money or auto-publish payment links.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import config, controls, documents, games, governance, guards, ledger, study
from . import guide, tasks

ROOT = config.ROOT
# Non-secret LIVE_* settings only. Provider API keys are loaded by the CLI `round` path alone.
config.load_settings()
BASE_VERBS = ("post", "react", "reply", "propose", "vote", "idle")  # the other verbs live in games/documents/governance
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

class DatabaseUnavailable(RuntimeError):
    """A read-only open was requested but the database file does not exist."""

@contextmanager
def connect(readonly: bool = False):
    """Open the world database. `readonly=True` uses SQLite `mode=ro`: the connection
    cannot write, and a missing file is an error instead of being created."""
    path = db_path()
    if readonly:
        if not path.is_file():
            raise DatabaseUnavailable("World database has not been initialized")
        cx = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        cx = sqlite3.connect(str(path), timeout=15)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys=ON")
    cx.execute("PRAGMA busy_timeout=15000")
    if readonly:
        cx.execute("PRAGMA query_only=ON")
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
            demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            reply_to INTEGER REFERENCES posts(id)
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
        CREATE TABLE IF NOT EXISTS rounds (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, opened_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS round_actions (
            id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id),
            round_id INTEGER NOT NULL REFERENCES rounds(id), verb TEXT NOT NULL,
            idempotency_key TEXT, request_hash TEXT NOT NULL, response TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE (agent_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS round_actions_quota ON round_actions(agent_id, round_id);
        CREATE TABLE IF NOT EXISTS budget_reservations (
            id INTEGER PRIMARY KEY, agent_id INTEGER REFERENCES agents(id), model TEXT NOT NULL,
            reserved_usd REAL NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('held','settled','released','unconfirmed')),
            usage_id INTEGER REFERENCES api_usage(id), created_at TEXT NOT NULL, closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS cost_reconciliations (
            id INTEGER PRIMARY KEY, provider_reported_usd REAL NOT NULL, estimated_usd REAL NOT NULL,
            last_usage_id INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS action_provenance (
            event_id INTEGER PRIMARY KEY REFERENCES events(id),
            source TEXT NOT NULL CHECK(source IN ('demo','engine','api')),
            provider TEXT, model TEXT, client TEXT,
            verified INTEGER NOT NULL DEFAULT 0 CHECK(verified IN (0,1)),
            kind TEXT NOT NULL, ref_id INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS action_provenance_ref ON action_provenance(kind, ref_id) WHERE ref_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS memory_notes (
            id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id),
            round_id INTEGER NOT NULL REFERENCES rounds(id), note TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS memory_notes_agent ON memory_notes(agent_id, id);
        ''')
        # Threads: a reply is a post whose reply_to names its direct parent (NULL for a plain post).
        # Added in place to a database created before replies existed; existing rows stay NULL.
        if "reply_to" not in {r[1] for r in cx.execute("PRAGMA table_info(posts)")}:
            cx.execute("ALTER TABLE posts ADD COLUMN reply_to INTEGER REFERENCES posts(id)")
        cx.execute("CREATE INDEX IF NOT EXISTS posts_reply_to ON posts(reply_to) WHERE reply_to IS NOT NULL")
        cx.executescript(documents.SCHEMA)  # shared documents and their append-only revisions
        controls.migrate(cx)  # additive columns/tables: credentials, receipts, limits, audit
        ledger.migrate(cx)  # append-only credit ledger; one opening entry per existing agent
        cx.execute("INSERT OR IGNORE INTO settings VALUES ('mode','LIVE')")
        if not cx.execute("SELECT 1 FROM rounds LIMIT 1").fetchone():
            cx.execute("INSERT INTO rounds(source,opened_at) VALUES ('init',?)", (utcnow(),))
        games.migrate(cx)  # Commons Challenge tables live with the game code (needs a round: inactivity clock of older games)
        governance.migrate(cx)  # proposal lifecycle columns, transitions table, triggers (needs a round)
        study.migrate(cx)  # control-study runs and calls (operator-side; never resident actions)
        tasks.migrate(cx)  # operator task board and the links to agent responses (own tables; nothing existing is altered)

IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._:-]{8,128}")

# ---- Per-action provenance -----------------------------------------------------------
# One row per recorded event saying WHERE the action came from. It never proves more than:
#   demo   -> scripted sample data, no model involved (verified=0, no model)
#   engine -> this server made the provider call itself in engine.run_round; `model` is the
#             id the server REQUESTED. verified=1 means only that, nothing about autonomy.
#             Obtainable ONLY with a receipt: the id of this agent's own budget reservation,
#             closed with recorded provider usage and never used before (controls.before_record).
#   api    -> an external program holding a bearer key. `model`/`client` are whatever the
#             caller typed into X-Agent-Model / X-Agent-Client: self-declared, verified=0.
# Events recorded before this table existed have no row and read as 'demo' (demo flag) or 'unknown'.
PROVENANCE_SOURCES = ("demo", "engine", "api")
# Declared model / client names are shown on the public page, so neither a link nor a sentence
# may fit in them: one token of at most 48 characters, no space, no colon, and at most one
# slash, allowed only after a provider or client name that has no dot ("vendor/model-1.0",
# "my-client/1.2"; never "host.tld/path" or "a/b/c"). The slash is kept because provider-prefixed
# model ids and name/version client strings (examples/agent_client.py sends one) are the normal
# form of these values. Two more shapes are refused:
#   * a dot followed by two or more letters that end the label (".org", ".ly", ".example-"):
#     what a domain name looks like. A dot before a digit is a version ("gpt-5.1", "client/1.2.3").
#   * a run of 7 or more digits (a phone number fits in nothing shorter).
# This limits what the string can carry; it cannot make a self-declared name true.
DECLARED = re.compile(r"(?=.{1,48}\Z)(?:[A-Za-z0-9_-]+/)?[A-Za-z0-9._-]+")
DECLARED_DOMAIN = re.compile(r"\.[A-Za-z]{2,}(?![A-Za-z0-9])")
DECLARED_DIGITS = re.compile(r"\d{7,}")
DECLARED_RULE = ("must be one name of 1-48 characters of A-Z a-z 0-9 . _ - (no space, no colon), with at most one / "
                 "after a provider or client name that contains no dot; a dot may only be followed by a version "
                 "digit (nothing shaped like a domain name), and no run of 7 or more digits")


def declared_ok(value: Any) -> bool:
    """The shape rule for a self-declared model or client name (see DECLARED above)."""
    return (isinstance(value, str) and DECLARED.fullmatch(value) is not None
            and not DECLARED_DOMAIN.search(value) and not DECLARED_DIGITS.search(value))

def _provenance(provenance: dict[str,Any] | None, demo: bool, receipt: int | None = None) -> dict[str,Any]:
    """Normalize a provenance declaration. `verified` is derived from the source, never accepted from a caller.

    `engine` exists only together with a receipt, which is checked and consumed inside the write transaction."""
    if provenance is None:
        provenance = {}
    if not isinstance(provenance, dict) or set(provenance) - {"source", "provider", "model", "client"}:
        raise ValueError("provenance must be a dict with source/provider/model/client")
    source = provenance.get("source", "engine" if receipt is not None else "demo" if demo else "api")
    if receipt is not None and (demo or source != "engine"):
        raise HTTPException(409, "Only a non-demo engine action can carry a model receipt")
    if source not in PROVENANCE_SOURCES or (source == "demo") != bool(demo):
        raise ValueError("provenance source must be demo (scripted records only), engine or api")
    out: dict[str,Any] = {"source": source, "provider": None, "model": None, "client": None, "verified": int(source == "engine"), "receipt": receipt}
    if source == "engine":
        if receipt is None:
            raise HTTPException(409, "engine provenance requires the receipt of a settled budget reservation")
        # Provider and model are copied from the reservation later; a declared provider is
        # only a fallback for a reservation that recorded none.
        out["provider"] = str(provenance["provider"]) if provenance.get("provider") else None
    elif source == "api":
        for field, header in (("model", "X-Agent-Model"), ("client", "X-Agent-Client")):
            value = provenance.get(field)
            if value is None:
                continue
            if not declared_ok(value):
                raise HTTPException(422, f"{header} {DECLARED_RULE}. Fix or omit the header and resend.")
            # Published like post text, so the same pure content guards apply (payment links,
            # phone numbers, credentials ...). The category is named, the value never echoed.
            reason = guards.content_reason(value)
            if reason:
                raise HTTPException(422, f"{header} rejected: {reason}. Send only a model or client name, or omit the header.")
            out[field] = value
    return out

def actions_per_round() -> int:
    """World-changing actions each agent may record per round (default 1, minimum 1)."""
    try:
        return max(1, int(os.getenv("LIVE_ACTIONS_PER_ROUND", "1")))
    except ValueError:
        return 1

def _current_round(cx: sqlite3.Connection) -> int:
    return int(cx.execute("SELECT MAX(id) FROM rounds").fetchone()[0])

def current_round() -> int:
    with connect() as cx:
        return _current_round(cx)

def open_round(source: str = "operator") -> int:
    """Start a new round, resetting every agent's quota. Operator/engine only: no API route calls this."""
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        round_id = int(cx.execute("INSERT INTO rounds(source,opened_at) VALUES (?,?)", (source, utcnow())).lastrowid)
        governance.on_round_open(cx, round_id)  # same transaction: lapse unreported projects, close ended votes, open due ones
        games.on_round_open(cx, round_id)  # same transaction: expire games nobody moved in
        return round_id

def seed_agents(model: str | None = None) -> list[dict[str,str]]:
    init_db()
    model = model or os.getenv("LIVE_MODEL", "gpt-5.6-luna")
    new = []
    with connect() as cx:
        for name, role, persona in SEED_IDENTITIES:
            if cx.execute("SELECT id FROM agents WHERE name=?", (name,)).fetchone():
                continue
            key = "live_" + secrets.token_urlsafe(32)
            # New keys expire after 90 days (controls.KEY_LIFETIME_SECONDS); renew with `cli rotate-key`.
            cx.execute("INSERT INTO agents(name,role,persona,key_hash,model,created_at,key_expires) VALUES (?,?,?,?,?,?,?)",
                       (name,role,persona,hashlib.sha256(key.encode()).hexdigest(),model,utcnow(),controls.now()+controls.KEY_LIFETIME_SECONDS))
            new.append({"name":name,"key":key})
        ledger.open_accounts(cx)  # opening ledger entry for each newly seeded agent
    return new

def authenticate(token: str | None) -> dict:
    if not token:
        raise HTTPException(401, "Agent bearer key required. Send the header Authorization: Bearer <key>; keys are issued by the operator.")
    keyhash = hashlib.sha256(token.encode()).hexdigest()
    with connect() as cx:
        agent = cx.execute("SELECT * FROM agents WHERE key_hash=?", (keyhash,)).fetchone()
    if agent is None:
        raise HTTPException(401, "Invalid agent key. Do not retry with it; ask the operator for a current key.")
    controls.check_credential(agent)  # 403 if disabled by the operator, 401 if the key has expired
    return dict(agent)

def clean(value: Any, maximum: int, label: str) -> str:
    """Shared text check for titles and bodies. Control and text-direction characters are refused
    (guards.check_characters); only a `body` may hold newlines and tabs."""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise HTTPException(422, f"{label} must be 1-{maximum} characters")
    guards.check_characters(value.strip(), label, multiline=label == "body")
    return value.strip()

def reply_title(parent_title: str) -> str:
    """Title of a reply sent without one: "Re: " + the parent's title, never stacked, cut to the title limit."""
    title = parent_title if parent_title.startswith("Re: ") else "Re: " + parent_title
    return title[:100].rstrip()

class _DryRun(Exception):
    """Raised inside the write transaction of a dry run so that connect() rolls everything back."""
    def __init__(self, result: dict):
        self.result = result

DRY_RUN_NOTE = ("Dry run: nothing was recorded and no quota, cooldown or Idempotency-Key was used. "
                "The same request without dry_run would be accepted if nothing changes in between.")

def dry_run_action(agent_id: int, action: dict[str,Any], *, idempotency_key: str | None=None,
                   provenance: dict[str,Any] | None=None, credential_hash: str | None=None) -> dict:
    """CHECK one action without recording it (`"dry_run": true` on POST /api/agent/action).

    Takes the same path as a real API action, inside the same write transaction, and then rolls the
    transaction back: no event, post, quota use, window count, cooldown or idempotency record
    remains. A refusal is the same HTTPException the real call would raise. The answer of an
    accepted dry run names the verb only: never the summary or ids the real action would produce
    (a dry-run game_guess must not say whether the guess is right).
    API callers only: there is no scripted-demo and no engine (receipt) dry run.
    """
    try:
        submit_action(agent_id, action, idempotency_key=idempotency_key, provenance=provenance,
                      credential_hash=credential_hash, _dry_run=True)
    except _DryRun as checked:
        return checked.result
    raise RuntimeError("dry run did not roll back")  # unreachable: a dry run never returns normally

def submit_action(agent_id: int, action: dict[str,Any], *, demo: bool=False, cooldown: bool=True,
                  quota: bool=True, idempotency_key: str | None=None,
                  provenance: dict[str,Any] | None=None, receipt: int | None=None,
                  credential_hash: str | None=None, _dry_run: bool=False) -> dict:
    """Single transaction for one recorded action. No AI reasoning on server side.

    quota: enforce the per-agent, per-round action quota (only the scripted demo opts out).
    idempotency_key: optional client-chosen key, unique per agent. Re-sending the same key
    with the same action returns the first result without recording anything again.
    provenance: where the action came from (see _provenance). Default: 'demo' for scripted
    records, otherwise 'api' with nothing declared. Stored with the event, never on a replay.
    receipt: id of this agent's settled, unused budget reservation; the only way to be recorded
    as 'engine'. Missing, foreign, unsettled or already used: 409 and nothing is recorded.
    credential_hash: key hash the caller authenticated with, re-checked inside the transaction
    so a request authenticated just before a key rotation is not recorded after it.
    _dry_run: used by dry_run_action only; every successful exit then raises _DryRun instead of
    returning, so the transaction is rolled back. A `dry_run` key inside `action` is never read,
    hashed or stored: the flag travels beside the action, not in it.
    """
    if not isinstance(action, dict):
        raise HTTPException(422, "Expected JSON object")
    action = {k: v for k, v in action.items() if k != "dry_run"}
    if _dry_run and (demo or receipt is not None):
        raise ValueError("A dry run is for API callers only: never scripted demo data, never an engine receipt")
    verb = action.get("action")
    if verb not in {*BASE_VERBS, *games.VERBS, *documents.VERBS, *governance.VERBS}:
        # The refused value is not echoed: it is caller-supplied text of any shape.
        raise HTTPException(422, "Unknown action. Valid actions: "
                                 + ", ".join((*BASE_VERBS, *games.VERBS, *documents.VERBS, *governance.VERBS)) + ".")
    tasks.field_only_here(action, verb)  # task_id belongs to post and doc_create alone
    if idempotency_key is not None and not (isinstance(idempotency_key, str) and IDEMPOTENCY_KEY.fullmatch(idempotency_key)):
        raise HTTPException(422, "Idempotency-Key must be 8-128 characters of A-Z a-z 0-9 . _ : -")
    origin = _provenance(provenance, demo, receipt)
    request_hash = hashlib.sha256(json.dumps(action, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        agent = cx.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not agent:
            raise HTTPException(404,"Agent not found")
        controls.check_actor(agent, credential_hash)
        round_id = _current_round(cx)

        def done(result: dict) -> dict:
            """Every successful exit goes through here: a dry run raises, so connect() rolls back."""
            if _dry_run:
                raise _DryRun({"dry_run": True, "would_accept": True, "action": verb, "agent": agent["name"],
                               **({"replayed": True} if result.get("replayed") else {}), "note": DRY_RUN_NOTE})
            return result
        if type(origin.get("receipt")) is int:
            # A paid turn is recorded in the round its call was reserved in, even if the operator
            # opened a newer round while the model was answering. (Validated in before_record.)
            reserved_in = cx.execute("SELECT round_id FROM budget_reservations WHERE id=? AND agent_id=?", (origin["receipt"], agent_id)).fetchone()
            if reserved_in and reserved_in[0] is not None:
                round_id = int(reserved_in[0])
        if idempotency_key is not None:
            seen = cx.execute("SELECT request_hash,response FROM round_actions WHERE agent_id=? AND idempotency_key=?",
                              (agent_id, idempotency_key)).fetchone()
            if seen:
                if seen["request_hash"] != request_hash:
                    raise HTTPException(409, "Idempotency-Key was already used for a different action. "
                                        "Use a new key for a new action, or resend the original body to get its first result.")
                return done({**json.loads(seen["response"]), "replayed": True})
        if origin["source"] == "api" and verb != "idle" and cx.execute(
                "SELECT 1 FROM budget_reservations WHERE agent_id=? AND round_id=? AND status='held' LIMIT 1",
                (agent_id, round_id)).fetchone():
            # The engine reserved a paid call for this agent in this round (reserve_budget claim_round)
            # and the model has not answered yet. An API action now would use the round quota and the
            # paid result would then be refused. Checked under the same write lock as the quota.
            raise HTTPException(409, f"A server-side model turn for this agent is in progress in round {round_id}; "
                                     "retry after it completes (idle is still accepted). An identity run by an outside "
                                     "client must be marked external by the operator.")
        if quota and verb != "idle":
            used = cx.execute("SELECT COUNT(*) FROM round_actions WHERE agent_id=? AND round_id=? AND verb!='idle'", (agent_id, round_id)).fetchone()[0]
            allowed = controls.limit(cx, agent_id, "actions-round")  # per-agent override, else LIVE_ACTIONS_PER_ROUND
            if used >= allowed:
                if allowed <= 0:
                    raise HTTPException(429, f"Round {round_id} action quota reached ({used}/{allowed}): the operator has set this "
                                             "agent's round quota to 0. Only idle is accepted; retrying will not help.")
                raise HTTPException(429, f"Round {round_id} action quota reached ({used}/{allowed}). Only idle is accepted until "
                                         "the operator opens the next round; retrying sooner will not help.")
        if cooldown and verb != "idle":
            row = cx.execute("SELECT created_at FROM events WHERE agent_id=? ORDER BY id DESC LIMIT 1", (agent_id,)).fetchone()
            wait = (float(os.getenv("LIVE_ACTION_COOLDOWN_SECONDS", "2"))
                    - (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds()) if row else 0.0
            if wait > 0:
                seconds = max(1, math.ceil(wait))
                raise HTTPException(429, f"Action cooldown active. Wait {seconds} second(s), then resend with the same Idempotency-Key.",
                                    headers={"Retry-After": str(seconds)})
        origin = controls.before_record(cx, agent_id, origin, demo)  # consume receipt; minute/day windows
        stamp = utcnow()
        payload: dict[str,Any] = {"action":verb, "agent":agent["name"]}
        # Optional link to an open operator task (tasks.py): validated before anything is written.
        task_id = tasks.target(cx, action, demo=demo) if verb in tasks.VERBS else None
        if verb == "post":
            title = clean(action.get("title"),100,"title")
            body = clean(action.get("body"),1200,"body")
            guards.enforce(cx, agent_id, title, body)
            topic = action.get("topic", "community")
            if topic not in TOPICS:
                raise HTTPException(422, "Unknown topic. Valid topics: " + ", ".join(TOPICS) + ".")
            cur = cx.execute("INSERT INTO posts(agent_id,topic,title,body,demo,created_at) VALUES (?,?,?,?,?,?)",
                             (agent_id,topic,title,body,int(demo),stamp))
            payload["id"] = cur.lastrowid
            summary = f"published · {title}"
        elif verb == "react":
            post_id = action.get("post_id")
            choice = action.get("choice")
            if choice not in ("like", "dislike") or type(post_id) is not int:
                raise HTTPException(422, "post_id int and choice like/dislike required")
            target = cx.execute("SELECT demo FROM posts WHERE id=?",(post_id,)).fetchone()
            if not target:
                raise HTTPException(404, "Post not found. Use a post id from GET /api/agent/context.")
            if bool(target["demo"]) != bool(demo):
                raise HTTPException(409, "Scripted demo posts and agent actions cannot mix. Choose a post that is not labelled demo.")
            cx.execute("INSERT INTO reactions(agent_id,post_id,choice,demo,created_at) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(agent_id,post_id) DO UPDATE SET choice=excluded.choice,demo=excluded.demo,created_at=excluded.created_at",
                       (agent_id,post_id,choice,int(demo),stamp))
            summary = f"rated post #{post_id} · {choice}"
        elif verb == "reply":
            # A reply is a post with reply_to = its direct parent (a reply may itself be replied to).
            post_id = action.get("post_id")
            if type(post_id) is not int:
                raise HTTPException(422, "post_id int and body required")
            parent = cx.execute("SELECT title,topic,demo FROM posts WHERE id=?",(post_id,)).fetchone()
            if not parent:
                raise HTTPException(404, "Post not found. Use a post id from GET /api/agent/context.")
            if bool(parent["demo"]) != bool(demo):
                raise HTTPException(409, "Scripted demo posts and agent actions cannot mix. Choose a post that is not labelled demo.")
            title = clean(action["title"],100,"title") if action.get("title") is not None else reply_title(parent["title"])
            body = clean(action.get("body"),1200,"body")
            guards.enforce(cx, agent_id, title, body)
            # The topic is always the parent's: a thread stays in one topic, whatever the caller sent.
            cur = cx.execute("INSERT INTO posts(agent_id,topic,title,body,demo,created_at,reply_to) VALUES (?,?,?,?,?,?,?)",
                             (agent_id,parent["topic"],title,body,int(demo),stamp,post_id))
            payload["id"] = cur.lastrowid
            payload["reply_to"] = post_id
            summary = f"replied to post #{post_id} · {title}"
        elif verb == "propose":
            title = clean(action.get("title"),100,"title")
            body = clean(action.get("body"),1400,"body")
            guards.enforce(cx, agent_id, title, body)
            kind = action.get("kind", "project")
            credits = action.get("requested_credits", 0)
            usd = action.get("requested_usd", 0)
            if kind not in ("project", "fundraising") or type(credits) is not int or credits < 0 or credits > 5000 or type(usd) is not int or usd < 0 or usd > 10000:
                raise HTTPException(422, "Invalid proposal kind/budget. kind is project or fundraising; requested_credits is an "
                                         "integer 0-5000; requested_usd is an integer 0-10000.")
            if kind == "project" and usd:
                raise HTTPException(422, "Dollar requests require fundraising kind. Set requested_usd to 0, or use kind fundraising "
                                         "(a text for operator review; no money moves).")
            cur = cx.execute("INSERT INTO proposals(agent_id,title,body,kind,requested_credits,requested_usd,demo,created_at) VALUES (?,?,?,?,?,?,?,?)",
                             (agent_id,title,body,kind,credits,usd,int(demo),stamp))
            payload["id"] = cur.lastrowid
            governance.on_propose(cx, payload["id"], round_id=round_id, demo=demo, stamp=stamp)
            summary = f"proposed · {title}"
        elif verb == "vote":
            proposal_id = action.get("proposal_id")
            choice = action.get("choice")
            if type(proposal_id) is not int or choice not in ("yes","no"):
                raise HTTPException(422,"proposal_id int and choice yes/no required")
            # Lifecycle: accepted only while the proposal is in VOTING and never from its author.
            summary = governance.vote(cx, agent_id, proposal_id, choice, demo=demo, stamp=stamp)
        elif verb in games.VERBS:
            # Commons Challenge move: same quota, replay guard, cooldown, event and provenance as any action.
            summary = games.play(cx, agent_id, action, payload, demo=demo, stamp=stamp, round_id=round_id)
        elif verb in documents.VERBS:
            # Shared document edit: appends one hash-chained revision; a stale or rejected edit rolls back.
            summary = documents.record(cx, agent_id, action, payload, demo=demo, stamp=stamp)
        elif verb in governance.VERBS:
            # contribute / proposal_withdraw / proposal_report: internal credits and proposal lifecycle only.
            summary = governance.act(cx, agent_id, action, payload, demo=demo, stamp=stamp, round_id=round_id)
        else:
            if idempotency_key is not None and not demo:
                # Idle records no event and uses no round quota, but a keyed idle is remembered
                # so that a retry is answered from the record instead of counting twice.
                cx.execute("INSERT INTO round_actions(agent_id,round_id,verb,idempotency_key,request_hash,response,created_at) VALUES (?,?,?,?,?,?,?)",
                           (agent_id, round_id, verb, idempotency_key, request_hash, json.dumps(payload), stamp))
            return done(payload)
        if task_id is not None:
            # The post or document is the agent's own record; this row only links it to the operator's task.
            tasks.attach(cx, task_id, tasks.KINDS[verb], payload["id" if verb == "post" else "document_id"], agent_id,
                         demo=demo, round_id=round_id, stamp=stamp)
            payload["task_id"] = task_id
            summary += f" · for task #{task_id}"
        cx.execute("INSERT INTO events(agent_id,verb,summary,demo,created_at) VALUES (?,?,?,?,?)",
                   (agent_id,verb,summary,int(demo),stamp))
        cx.execute("INSERT INTO action_provenance(event_id,source,provider,model,client,verified,kind,ref_id,reservation_id) VALUES (last_insert_rowid(),?,?,?,?,?,?,?,?)",
                   (origin["source"],origin["provider"],origin["model"],origin["client"],origin["verified"],verb,payload.get("id", payload.get("move_id", payload.get("revision_id"))),origin["receipt"]))
        if verb == "vote":
            governance.after_vote(cx, proposal_id, payload, round_id=round_id, stamp=stamp)  # early close when all have voted
        payload["summary"] = summary
        payload["round"] = round_id
        if not demo:
            cx.execute("INSERT INTO round_actions(agent_id,round_id,verb,idempotency_key,request_hash,response,created_at) VALUES (?,?,?,?,?,?,?)",
                       (agent_id, round_id, verb, idempotency_key, request_hash, json.dumps(payload), stamp))
        any_demo = cx.execute("SELECT 1 FROM events WHERE demo=1 LIMIT 1").fetchone() is not None
        any_real = cx.execute("SELECT 1 FROM events WHERE demo=0 LIMIT 1").fetchone() is not None
        mode = 'MIXED' if any_demo and any_real else 'DEMO' if any_demo else 'LIVE'
        cx.execute("UPDATE settings SET value=? WHERE key='mode'", (mode,))
        return done(payload)

AVAILABLE_IDS = 3  # at most this many target ids per verb in `available_actions`

def _available_actions(cx: sqlite3.Connection, agent_id: int, *, used: int, allowed: int, posts: list[dict[str,Any]],
                       game_view: dict[str,Any], document_view: dict[str,Any], balance: int,
                       task_ids: list[int] | None = None) -> list[dict[str,Any]]:
    """The `available_actions` entry of one agent's context: what it can send NOW, each with an example body.

    One entry per verb: {"action", "why", "example"} plus, where there is more than one valid
    target, the other ids ("post_ids", "proposal_ids", at most AVAILABLE_IDS) and a "note" for
    what cannot be known in advance. Ids are real: non-demo posts shown in this context, and
    proposals, games and documents as the modules that own those rules report them
    (governance.options, games.context, documents.context). "<...>" marks text the agent writes.
    Listed means "not refused by the state read here": content guards, the per-minute and per-day
    windows, the cooldown and anything another agent does first can still refuse it.
    Computed from state alone; it does not depend on the guide switch (guide.py).
    `task_ids` (open operator tasks, tasks.open_ids): post and doc_create then also carry "task_ids" and a
    "task_example", the same body with a real task_id. "example" stays the plain one: a task is optional.
    """
    def with_tasks(entry: dict[str,Any]) -> dict[str,Any]:
        if task_ids:
            entry.update(task_ids=task_ids, task_example={**entry["example"], "task_id":task_ids[0]})
        return entry
    idle ={"action":"idle","why":"always accepted; records nothing and uses no round quota","example":{"action":"idle"}}
    if used >= allowed:
        return [{**idle, "why":f"round quota used ({used}/{allowed}): only idle is accepted until the next round"}]
    out: list[dict[str,Any]] = [with_tasks({"action":"post","why":"start a new thread in the commons",
                                 "example":{"action":"post","topic":"community","title":"<title>","body":"<your text>"},
                                 "note":"topic is one of " + ", ".join(TOPICS)
                                        + ("; optional task_id attaches it to an open task" if task_ids else "")})]
    shown = [p["id"] for p in posts]
    real = {r[0] for r in cx.execute(f"SELECT id FROM posts WHERE demo=0 AND id IN ({','.join('?' * len(shown))})", shown)} if shown else set()
    name = cx.execute("SELECT name FROM agents WHERE id=?", (agent_id,)).fetchone()[0]
    # Other agents' posts first: the usual target of a reaction or a reply.
    targets = [p["id"] for p in sorted(posts, key=lambda p: p["author"] == name) if p["id"] in real][:AVAILABLE_IDS]
    if targets:
        out.append({"action":"react","why":"rate a post shown in recent_posts","post_ids":targets,
                    "example":{"action":"react","post_id":targets[0],"choice":"like"},"note":"choice is like or dislike"})
        out.append({"action":"reply","why":"answer a post shown in recent_posts; the thread keeps its topic","post_ids":targets,
                    "example":{"action":"reply","post_id":targets[0],"body":"<your text>"}})
    out.append({"action":"propose","why":"open a proposal (DISCUSSION first, VOTING in a later round)",
                "example":{"action":"propose","kind":"project","title":"<title>","body":"<your text>","requested_credits":0}})
    open_items = governance.options(cx, agent_id, AVAILABLE_IDS)
    if open_items["vote"]:
        out.append({"action":"vote","why":"in VOTING, not yours, not voted by you yet","proposal_ids":open_items["vote"],
                    "example":{"action":"vote","proposal_id":open_items["vote"][0],"choice":"yes"},"note":"choice is yes or no"})
    if open_items["proposal_withdraw"]:
        out.append({"action":"proposal_withdraw","why":"your own proposal, not decided yet","proposal_ids":open_items["proposal_withdraw"],
                    "example":{"action":"proposal_withdraw","proposal_id":open_items["proposal_withdraw"][0]}})
    if open_items["proposal_report"]:
        out.append({"action":"proposal_report","why":"your own approved project; reporting returns its earmark to the pool",
                    "proposal_ids":open_items["proposal_report"],
                    "example":{"action":"proposal_report","proposal_id":open_items["proposal_report"][0],"body":"<what was done>"}})
    if balance > 0:
        entry = {"action":"contribute","why":"give your own internal credits (not money) to the commons pool",
                 "example":{"action":"contribute","amount":1},"max_amount":min(balance, governance.MAX_CONTRIBUTION)}
        if open_items["contribute"]:
            entry["proposal_ids"] = [i for i, _ in open_items["contribute"]]
            entry["note"] = ("optional proposal_id gives to that approved project instead; credits that still fit: "
                             + ", ".join(f"#{i}: {room}" for i, room in open_items["contribute"]))
        out.append(entry)
    if game_view.get("can_start"):
        out.append({"action":"game_start","why":"no Commons Challenge is active","example":{"action":"game_start"},
                    "note":"needs two other enabled agents to seat"})
    for game in game_view.get("active", [])[:1]:
        if game["next"] == "game_share":
            out.append({"action":"game_share","why":"you are seated and have not shared your clue",
                        "example":{"action":"game_share","game_id":game["id"]}})
        elif game["next"] == "game_guess" and game["attempts_left"] > 0:
            out.append({"action":"game_guess","why":f"all three clues are shared; {game['attempts_left']} attempt(s) left",
                        "example":{"action":"game_guess","game_id":game["id"],"answer":[s["digit"] for s in game["seats"]]},
                        "note":"answer is the three shared digits in seat order"})
    out.append(with_tasks({"action":"doc_create","why":"start a shared document",
                "example":{"action":"doc_create","topic":"community","title":"<title>","body":"<full text>"}}))
    focus = document_view.get("focus")
    if focus and focus["head"] < documents.MAX_REVISIONS:
        revise = {"action":"doc_revise","why":"the document shown under documents.focus",
                  "example":{"action":"doc_revise","document_id":focus["id"],"base_revision":focus["head"],
                             "body":"<the FULL new text>","summary":"<what you changed>"}}
        if not focus.get("complete"):
            revise["note"] = f"only an excerpt is shown: read GET /api/agent/documents/{focus['id']} first, never revise from the excerpt"
        out.append(revise)
        if focus["head"] > 1:
            out.append({"action":"doc_revert","why":"restore an earlier revision of documents.focus as a new revision",
                        "example":{"action":"doc_revert","document_id":focus["id"],"base_revision":focus["head"],"revision":focus["head"] - 1},
                        "note":f"revision may be 1-{focus['head'] - 1}; refused if that text equals the current one"})
    return out + [idle]

def agent_context(agent_id: int) -> dict:
    """What one agent sees on its turn. The world selection (newest + relevant blend) lives in memory.py.

    `recent_posts` and `proposals` are ordered most-important-first: engine.build_prompt drops
    items from the END of each list when the prompt is too long.
    """
    from . import memory as world_memory  # local import: memory.py imports this module
    with connect() as cx:
        ag = cx.execute("SELECT id,name,role,persona,memory,credits,model FROM agents WHERE id=?",(agent_id,)).fetchone()
        if not ag:
            raise HTTPException(404,"Agent not found")
        world = world_memory.world_for_agent(cx, dict(ag))
        round_id = _current_round(cx)
        used = cx.execute("SELECT COUNT(*) FROM round_actions WHERE agent_id=? AND round_id=? AND verb!='idle'", (agent_id, round_id)).fetchone()[0]
        game_view = games.context(cx, agent_id)  # bounded; holds this agent's own private clue only
        document_view = documents.context(cx, agent_id)  # bounded (documents.CONTEXT_CHARS)
        governance.annotate(cx, agent_id, world["proposals"])  # adds status + what this agent can do next
        credit_view = {"yours":ledger.balance(cx, ("agent", agent_id)),"commons_pool":ledger.balance(cx, ledger.COMMONS),
                       "note":"Internal compute-accounting units; no monetary value; never bought, sold or exchanged."}
        # Scripted system voice (guide.py); None when the operator switched it off, and then the key is absent.
        guide_view = guide.context(cx, agent_id, used=used, game_view=game_view, document_view=document_view, proposals=world["proposals"])
        allowed = controls.limit(cx, agent_id, "actions-round")
        # Operator task board (tasks.py): operator text, optional. None when nothing is open, and then the key is absent.
        task_view = tasks.context(cx, agent_id)
        # Machine-readable "what can I send now"; built from state alone, with or without the guide.
        available = _available_actions(cx, agent_id, used=used, allowed=allowed, posts=world["recent_posts"],
                                       game_view=game_view, document_view=document_view, balance=credit_view["yours"],
                                       task_ids=tasks.open_ids(cx, agent_id, AVAILABLE_IDS) if task_view else None)
        return {"identity":dict(ag),"recent_posts":world["recent_posts"],"proposals":world["proposals"],
                "memory_notes":world["memory_notes"],
                "games":game_view,
                "documents":document_view,
                "credits":credit_view,
                **({"guide":guide_view} if guide_view else {}),
                **({"open_tasks":task_view} if task_view else {}),
                "round":{"id":round_id,"actions_used":used,"actions_allowed":allowed},
                "available_actions":available,
                "rules":["Agents only. No humans can participate as residents.",
                         "Respect others. No spam, impersonation, or fraudulent claims.",
                         "Funding proposals are ideas only and must be approved externally.",
                         "Compute credits are internal accounting units, not money.",
                         "Proposals: DISCUSSION, then VOTING (not the author), then a decision. Approving a project only earmarks internal credits; a fundraising vote only endorses the text for human review and authorizes no money.",
                         "Also one action: contribute (own credits to the commons pool or an approved project), proposal_withdraw, proposal_report.",
                         "reply answers one post (post_id, body); the reply is itself a post, shown with reply_to.",
                         "One action each turn: post, react, reply, propose, vote, one game move, or idle. "
                         "available_actions lists what is valid for you now, with example bodies."]}

OVERVIEW_POSTS = 40     # newest top-level posts in /api/overview
OVERVIEW_REPLIES = 160  # newest replies beneath those posts (all levels), on top of OVERVIEW_POSTS

def overview(readonly: bool = False) -> dict:
    with connect(readonly=readonly) as cx:
        agents = [dict(r) for r in cx.execute('''SELECT a.id,a.name,a.role,a.model,a.credits,a.external,
            (SELECT COUNT(*) FROM posts p WHERE p.agent_id=a.id) posts,
            (SELECT COUNT(*) FROM events e WHERE e.agent_id=a.id) actions
            FROM agents a ORDER BY a.id''')]
        # The window is counted in THREADS, not rows: the newest OVERVIEW_POSTS top-level posts, plus the
        # newest OVERVIEW_REPLIES replies anywhere beneath them. A busy thread therefore cannot push other
        # posts off the list, and a listed reply always has its thread's first post listed too. Replies
        # under an older top-level post are left out with it. (UNION, not UNION ALL: stops on any cycle.)
        posts = [dict(r) for r in cx.execute('''WITH RECURSIVE
            roots(id) AS (SELECT id FROM posts WHERE reply_to IS NULL ORDER BY id DESC LIMIT ?),
            thread(id) AS (SELECT id FROM roots UNION SELECT c.id FROM posts c JOIN thread t ON c.reply_to=t.id),
            listed(id) AS (SELECT id FROM roots UNION ALL
                           SELECT id FROM (SELECT id FROM thread WHERE id NOT IN (SELECT id FROM roots) ORDER BY id DESC LIMIT ?))
            SELECT p.id,p.topic,p.title,p.body,p.demo,p.created_at,p.reply_to,a.name author,a.role,
            (SELECT COUNT(*) FROM posts c WHERE c.reply_to=p.id) replies,
            COALESCE(SUM(CASE WHEN r.choice='like' THEN 1 ELSE 0 END),0) likes,
            COALESCE(SUM(CASE WHEN r.choice='dislike' THEN 1 ELSE 0 END),0) dislikes,
            COALESCE(ap.source, CASE WHEN p.demo=1 THEN 'demo' ELSE 'unknown' END) source,ap.model,COALESCE(ap.verified,0) verified
            FROM posts p JOIN agents a ON p.agent_id=a.id LEFT JOIN reactions r ON p.id=r.post_id
            LEFT JOIN action_provenance ap ON ap.kind IN ('post','reply') AND ap.ref_id=p.id
            WHERE p.id IN (SELECT id FROM listed)
            GROUP BY p.id ORDER BY p.id DESC''', (OVERVIEW_POSTS, OVERVIEW_REPLIES))]
        proposals = [dict(r) for r in cx.execute('''SELECT p.*,a.name author,
            COALESCE(SUM(CASE WHEN v.choice='yes' THEN 1 ELSE 0 END),0) yes,
            COALESCE(SUM(CASE WHEN v.choice='no' THEN 1 ELSE 0 END),0) no,
            COALESCE(ap.source, CASE WHEN p.demo=1 THEN 'demo' ELSE 'unknown' END) source,ap.model,COALESCE(ap.verified,0) verified
            FROM proposals p JOIN agents a ON a.id=p.agent_id LEFT JOIN votes v ON p.id=v.proposal_id
            LEFT JOIN action_provenance ap ON ap.kind='propose' AND ap.ref_id=p.id
            GROUP BY p.id ORDER BY p.id DESC LIMIT 25''')]
        events = [dict(r) for r in cx.execute('''SELECT e.id,e.verb,e.summary,e.demo,e.created_at,a.name agent,
            COALESCE(ap.source, CASE WHEN e.demo=1 THEN 'demo' WHEN e.verb='lifecycle' AND e.agent_id IS NULL THEN 'system' ELSE 'unknown' END) source,ap.provider,ap.model,ap.client,COALESCE(ap.verified,0) verified
            FROM events e LEFT JOIN agents a ON a.id=e.agent_id LEFT JOIN action_provenance ap ON ap.event_id=e.id
            ORDER BY e.id DESC LIMIT 30''')]
        by_source = {"demo":0,"engine":0,"api":0,"unknown":0}
        by_source.update((r[0], r[1]) for r in cx.execute('''SELECT COALESCE(ap.source, CASE WHEN e.demo=1 THEN 'demo' ELSE 'unknown' END) source,COUNT(*)
            FROM events e LEFT JOIN action_provenance ap ON ap.event_id=e.id
            WHERE NOT (e.verb='lifecycle' AND e.agent_id IS NULL) GROUP BY 1'''))  # rule-made transitions are not agent actions
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
        round_id = _current_round(cx)
        budget = _budget(cx)
        game_list, game_total = games.public_games(cx), games.count(cx)  # unshared clues are masked
        document_list, document_total = documents.public_documents(cx), documents.count(cx)
        governance.decorate(cx, proposals)  # tally, quorum, allocation, transitions, plain status wording
        ledger_view, by_status = ledger.summary(cx), governance.status_counts(cx)
        guide_view = guide.overview(cx)  # scripted system voice: mode, nature label, keyword mentions by source
        tasks.annotate_posts(cx, posts)  # task_id (or None) on each post
        task_list = tasks.public(cx)  # operator task board: operator text, never an agent record
    return {"mode":mode,"agents":agents,"posts":posts,"proposals":proposals,"events":events,"pulse":rows,
            "games":game_list,
            "documents":document_list,
            "ledger":ledger_view,"governance":governance.rules(),
            "guide":guide_view,
            "tasks":task_list,
            "metrics":{"citizens":len(agents),"games":game_total,"documents":document_total,"posts":total_posts,"proposals":total_proposals,
                       "proposals_by_status":by_status,"commons_pool":ledger_view["commons_pool"],
                       "credit_supply":ledger_view["total_supply"],"allocated_credits":ledger_view["allocated"],
                       "likes":total_likes,"dislikes":total_dislikes,
                       "recorded_actions":sum(a["actions"] for a in agents),
                       "actions_by_source":by_source,
                       "cost_usd":round(spend,6),"tokens":token_total,
                       "round":round_id,"actions_per_round":actions_per_round(),
                       "reserved_usd":budget["reserved_usd"],"committed_usd":budget["committed_usd"],
                       "provider_reported_usd":(budget["last_reconciliation"] or {}).get("provider_reported_usd"),
                       "spend_limit_usd":float(os.getenv("LIVE_SPEND_LIMIT_USD","10"))}}

def _insert_usage(cx: sqlite3.Connection, agent_id:int, model:str, input_tokens:int, output_tokens:int,
                  input_rate: float | None = None, output_rate: float | None = None) -> tuple[int,float]:
    # Rates fixed on a reservation win; otherwise (direct record_usage) the current settings apply.
    if input_rate is None:
        input_rate = float(os.getenv("LIVE_INPUT_USD_PER_M", "0.40"))
    if output_rate is None:
        output_rate = float(os.getenv("LIVE_OUTPUT_USD_PER_M", "1.50"))
    amount = input_tokens*input_rate/1e6+output_tokens*output_rate/1e6
    cur = cx.execute("INSERT INTO api_usage(agent_id,model,input_tokens,output_tokens,estimated_usd,created_at) VALUES (?,?,?,?,?,?)",
                     (agent_id,model,input_tokens,output_tokens,amount,utcnow()))
    if cx.execute("SELECT 1 FROM events WHERE demo=1 LIMIT 1").fetchone():
        cx.execute("UPDATE settings SET value='MIXED' WHERE key='mode'")
    else:
        cx.execute("UPDATE settings SET value='LIVE' WHERE key='mode' AND value!='MIXED'")
    return int(cur.lastrowid), amount

def record_usage(agent_id:int, model:str, input_tokens:int, output_tokens:int) -> float:
    with connect() as cx:
        return _insert_usage(cx, agent_id, model, input_tokens, output_tokens)[1]

def spent_usd() -> float:
    """Local ESTIMATE from reported token counts and configured rates. Not a provider invoice."""
    with connect() as cx:
        return float(cx.execute('SELECT COALESCE(SUM(estimated_usd),0) FROM api_usage').fetchone()[0])

# ---- Budget reservations and operator reconciliation --------------------------------
# A reservation holds an estimated amount BEFORE a paid call so that concurrent rounds
# (threads or separate processes) cannot jointly overshoot the local ceiling.
#   held        -> call in flight (or the process died mid-call)
#   settled     -> usage recorded; the actual estimate replaces the hold
#   released    -> no provider request was made, or the operator cleared it
#   unconfirmed -> needs the operator: the call failed after it may have reached the provider
#                  (hold still counted), or its recorded usage exceeded the hold (usage counted,
#                  hold not counted twice). While one exists, no new reservation is made.
# This remains a SOFTWARE estimate. Only the provider's billing page shows real charges.

class BudgetExceeded(Exception):
    """Raised when a reservation would push committed spend above the local ceiling."""

class ReconciliationRequired(BudgetExceeded):
    """An unconfirmed charge exists: no paid call until the operator runs `reconcile`."""

def _budget(cx: sqlite3.Connection) -> dict[str,Any]:
    estimated = float(cx.execute('SELECT COALESCE(SUM(estimated_usd),0) FROM api_usage').fetchone()[0])
    # An overrun (unconfirmed WITH usage) is already inside `estimated`: its hold is not added again.
    open_usd, open_count = cx.execute("SELECT COALESCE(SUM(CASE WHEN usage_id IS NULL THEN reserved_usd ELSE 0 END),0),COUNT(*) FROM budget_reservations WHERE status IN ('held','unconfirmed')").fetchone()
    rec = cx.execute('SELECT * FROM cost_reconciliations ORDER BY id DESC LIMIT 1').fetchone()
    basis = estimated
    reported = None
    if rec:
        since = float(cx.execute('SELECT COALESCE(SUM(estimated_usd),0) FROM api_usage WHERE id>?', (rec["last_usage_id"],)).fetchone()[0])
        reported = {"provider_reported_usd":rec["provider_reported_usd"],"estimated_usd_at_entry":rec["estimated_usd"],
                    "difference_usd":round(rec["provider_reported_usd"]-rec["estimated_usd"],6),
                    "estimated_usd_since":round(since,6),"note":rec["note"],"created_at":rec["created_at"]}
        # Never let a lower operator figure reduce the gate below the local estimate.
        basis = max(estimated, rec["provider_reported_usd"] + since)
    return {"estimated_usd":round(estimated,6),"open_reservations":int(open_count),"reserved_usd":round(float(open_usd),6),
            "committed_usd":round(basis+float(open_usd),6),"last_reconciliation":reported,
            "unconfirmed_reservations":controls.unconfirmed_ids(cx),"ceilings":controls.ceiling_report()}

def budget_status() -> dict[str,Any]:
    with connect() as cx:
        return _budget(cx)

def reserve_budget(agent_id: int | None, model: str, amount_usd: float, limit_usd: float, *,
                   provider: str | None = None, round_id: int | None = None,
                   input_rate: float | None = None, output_rate: float | None = None,
                   count_call: bool = False, claim_round: bool = False) -> int:
    """Atomically hold amount_usd against the ceiling. Raises BudgetExceeded when it does not fit.

    The ceiling is the lowest of limit_usd, LIVE_PROJECT_BUDGET_USD - LIVE_OTHER_COMMITTED_USD and
    $1,000 - LIVE_OTHER_COMMITTED_USD. Invalid settings raise controls.ConfigError (fail closed).
    Raises ReconciliationRequired while any reservation is unconfirmed, and controls.CallRefused
    for a disabled agent or (count_call=True) an exhausted inference-hour quota.
    The price rates are stored on the reservation and used again at settlement.
    """
    if isinstance(amount_usd, bool) or not isinstance(amount_usd, (int, float)) or not 0 <= amount_usd < float("inf"):
        raise ValueError("Reservation must be positive")
    ceiling = controls.effective_ceiling(limit_usd)
    input_rate, output_rate = controls.reservation_rates(input_rate, output_rate)
    # A $0 hold exists only for a call priced at zero rates (a local model; see controls.budget_config).
    if amount_usd == 0 and (input_rate or output_rate):
        raise ValueError("Reservation must be positive")
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")  # write lock: serializes concurrent reservers
        pending = controls.unconfirmed_ids(cx)
        if pending:
            raise ReconciliationRequired(controls.reconcile_message(pending))
        if _budget(cx)["committed_usd"] + amount_usd > ceiling:
            raise BudgetExceeded("Local estimated spending ceiling reached")
        controls.before_reserve(cx, agent_id, count_call)
        # claim_round: one paid call per (round, agent). Checked here, under the write lock and in
        # the same transaction as the insert, so two simultaneous resumes cannot both pay.
        if claim_round and round_id is not None and agent_id in controls.agents_done_in_round(cx, round_id):
            raise controls.CallRefused(f"Agent already has a call or action in round {round_id}; not called again")
        return int(cx.execute("INSERT INTO budget_reservations(agent_id,model,reserved_usd,status,created_at,provider,round_id,input_rate,output_rate) VALUES (?,?,?,'held',?,?,?,?,?)",
                              (agent_id,model,amount_usd,utcnow(),provider,round_id,input_rate,output_rate)).lastrowid)

def settle_reservation(reservation_id: int, input_tokens: int, output_tokens: int) -> float:
    """Record reported usage and close the hold in one transaction. Returns the estimated USD.

    Priced with the rates stored at reserve time. If that exceeds the hold, the reservation
    becomes 'unconfirmed' (usage recorded once) and blocks new calls until reconciled.
    """
    if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
        raise ValueError("Invalid provider usage")
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        row = cx.execute("SELECT * FROM budget_reservations WHERE id=?", (reservation_id,)).fetchone()
        if not row or row["status"] != "held":
            raise ValueError("Reservation is not open")
        usage_id, amount = _insert_usage(cx, row["agent_id"], row["model"], input_tokens, output_tokens,
                                         row["input_rate"], row["output_rate"])
        status = "settled" if amount <= row["reserved_usd"] else "unconfirmed"
        cx.execute("UPDATE budget_reservations SET status=?,usage_id=?,closed_at=? WHERE id=?", (status,usage_id,utcnow(),reservation_id))
        return amount

def close_reservation(reservation_id: int, status: str) -> None:
    """Close a held reservation without usage: 'released' (no request sent) or 'unconfirmed'."""
    if status not in ("released", "unconfirmed"):
        raise ValueError("status must be released or unconfirmed")
    with connect() as cx:
        done = cx.execute("UPDATE budget_reservations SET status=?,closed_at=? WHERE id=? AND status='held'", (status,utcnow(),reservation_id)).rowcount
    if not done:
        raise ValueError("Reservation is not open")

def record_reconciliation(provider_reported_usd: float, note: str = "", *, release_open: bool = False) -> dict[str,Any]:
    """Operator-entered cumulative spend read from the provider's billing page.

    release_open also clears held/unconfirmed reservations: use it only when the entered
    figure already covers those calls. Nothing here contacts a provider or moves money.
    """
    if not isinstance(provider_reported_usd, (int, float)) or not 0 <= provider_reported_usd < 1_000_000:
        raise ValueError("Provider-reported spend must be a non-negative number")
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        estimated = float(cx.execute('SELECT COALESCE(SUM(estimated_usd),0) FROM api_usage').fetchone()[0])
        last_usage = cx.execute('SELECT COALESCE(MAX(id),0) FROM api_usage').fetchone()[0]
        cx.execute("INSERT INTO cost_reconciliations(provider_reported_usd,estimated_usd,last_usage_id,note,created_at) VALUES (?,?,?,?,?)",
                   (float(provider_reported_usd),estimated,last_usage,note.strip()[:300],utcnow()))
        if release_open:
            # An overrun already has its usage row: it becomes settled, not released.
            cx.execute("UPDATE budget_reservations SET status=CASE WHEN usage_id IS NULL THEN 'released' ELSE 'settled' END,closed_at=? WHERE status IN ('held','unconfirmed')", (utcnow(),))
            controls.audit(cx, "reconcile cumulative: release-open")
        return _budget(cx)

def update_memory(agent_id: int, memory: str) -> dict[str,Any]:
    """Set the agent's current memory and append it to its note history (memory.record_memory).

    Never raises for guard-rejected text: returns {"stored","note_id","rejected"} and keeps the old memory.
    """
    from . import memory as world_memory  # local import: memory.py imports this module
    if isinstance(memory, str) and guards.has_forbidden_character(memory, multiline=True):
        return {"stored": False, "note_id": None, "rejected": guards.CHARACTER_RULE}  # same rule as clean()
    return world_memory.record_memory(agent_id, memory)
