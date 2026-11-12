"""Commons Challenge: three agents pool private digits to unlock a shared beacon.

Ported from the design on branch codex/local-0.2. A tiny cooperative coordination
task: each of three agents is dealt one private digit, each must publish its own
clue (`game_share`), then any of the three may submit the 3-digit answer
(`game_guess`, at most MAX_ATTEMPTS). Winning scores MAX_ATTEMPTS + 1 - attempts.

Nothing here is called directly by a client. `core.submit_action` owns the
transaction, quota, idempotency, cooldown, event and provenance row and calls
`play()` inside it, so a rejected move (HTTPException) rolls everything back.

Privacy: `game_clues.value` is read in exactly three places below — the win check,
the owner's own context, and (only once `shared=1`) the public views.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any

from fastapi import HTTPException

VERBS = ("game_start", "game_share", "game_guess")
MAX_ATTEMPTS = 3
SEATS = 3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(state IN ('ACTIVE','WON','LOST')),
    attempts INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0,
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)), created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS games_one_active ON games(demo) WHERE state='ACTIVE';
CREATE TABLE IF NOT EXISTS game_clues (
    game_id INTEGER NOT NULL REFERENCES games(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 3),
    value INTEGER NOT NULL CHECK(value BETWEEN 0 AND 9),
    shared INTEGER NOT NULL DEFAULT 0 CHECK(shared IN (0,1)),
    PRIMARY KEY (game_id, agent_id), UNIQUE (game_id, position)
);
CREATE TABLE IF NOT EXISTS game_moves (
    id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL REFERENCES games(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    move TEXT NOT NULL CHECK(move IN ('game_start','game_share','game_guess')),
    detail TEXT NOT NULL, answer TEXT, correct INTEGER, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS game_moves_game ON game_moves(game_id, id);
'''


def draw_digit() -> int:
    """One private clue digit. Tests monkeypatch this attribute for determinism."""
    return secrets.randbelow(10)


def pick_peers(cx: sqlite3.Connection, starter_id: int) -> list[int]:
    """The two other seats: lowest ids among agents that are not revoked, excluding the starter.

    The only place players are chosen. A revoked agent could never share its clue,
    which would leave the game stuck, so it is never seated.
    """
    from . import controls  # local import: controls is loaded after this module by core
    return [i for i in controls.active_agent_ids(cx) if i != starter_id][:SEATS - 1]


def play(cx: sqlite3.Connection, agent_id: int, action: dict[str, Any], payload: dict[str, Any],
         *, demo: bool, stamp: str) -> str:
    """Apply one game move inside the caller's transaction.

    Adds game_id/move_id/state/score/attempts_left to `payload` and returns the public
    event summary. The summary never contains a clue that has not been shared.
    """
    verb = action["action"]
    answer_text, correct = None, None
    if verb == "game_start":
        if cx.execute("SELECT 1 FROM games WHERE state='ACTIVE' AND demo=?", (int(demo),)).fetchone():
            raise HTTPException(409, "A Commons Challenge is already active. Play that one (see games in your context) instead of starting another.")
        members = [agent_id] + pick_peers(cx, agent_id)
        if len(members) != SEATS:
            raise HTTPException(409, "Three registered agents are required. Do not retry; only the operator can register agents.")
        gid = cx.execute("INSERT INTO games(demo,created_at) VALUES (?,?)", (int(demo), stamp)).lastrowid
        for position, member in enumerate(members, start=1):
            digit = draw_digit()
            if type(digit) is not int or not 0 <= digit <= 9:
                raise RuntimeError("draw_digit must return an integer 0-9")
            cx.execute("INSERT INTO game_clues(game_id,agent_id,position,value) VALUES (?,?,?,?)", (gid, member, position, digit))
        names = [r[0] for r in cx.execute("SELECT a.name FROM game_clues c JOIN agents a ON a.id=c.agent_id WHERE c.game_id=? ORDER BY c.position", (gid,))]
        payload["seats"] = names
        detail = "Three private clues dealt to " + ", ".join(names)
        summary = f"started Commons Challenge #{gid} with {names[1]} and {names[2]} · three private clues dealt"
        state, score, attempts = "ACTIVE", 0, 0
    else:
        gid = action.get("game_id")
        if type(gid) is not int:
            raise HTTPException(422, "game_id integer required")
        game = cx.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
        if not game:
            raise HTTPException(404, "Game not found. Use a game id from GET /api/agent/context.")
        if game["state"] != "ACTIVE":
            raise HTTPException(409, "Game is finished and accepts no more moves. Send game_start to begin a new one.")
        if bool(game["demo"]) != bool(demo):
            raise HTTPException(409, "Scripted demo games and agent games cannot mix")
        clue = cx.execute("SELECT position,value,shared FROM game_clues WHERE game_id=? AND agent_id=?", (gid, agent_id)).fetchone()
        if not clue:
            raise HTTPException(403, "Only the three assigned agents can play this game. Your own games are listed in your context.")
        state, score, attempts = "ACTIVE", 0, game["attempts"]
        if verb == "game_share":
            if clue["shared"]:
                raise HTTPException(409, "Clue already shared. Once all three clues are shared, send game_guess.")
            cx.execute("UPDATE game_clues SET shared=1 WHERE game_id=? AND agent_id=?", (gid, agent_id))
            detail = f"Position {clue['position']} is {clue['value']}"
            summary = f"shared a clue in Commons Challenge #{gid} · position {clue['position']} is {clue['value']}"
        else:
            answer = action.get("answer")
            if not isinstance(answer, list) or len(answer) != SEATS or any(type(n) is not int or not 0 <= n <= 9 for n in answer):
                raise HTTPException(422, "answer must be a list of three integer digits 0-9")
            clues = cx.execute("SELECT value,shared FROM game_clues WHERE game_id=? ORDER BY position", (gid,)).fetchall()
            if not all(c["shared"] for c in clues):
                raise HTTPException(409, "All three agents must share their clues first. Send game_share if you have not; otherwise wait for the others.")
            attempts += 1
            won = answer == [c["value"] for c in clues]
            state = "WON" if won else "LOST" if attempts >= MAX_ATTEMPTS else "ACTIVE"
            score = MAX_ATTEMPTS + 1 - attempts if won else 0
            cx.execute("UPDATE games SET state=?,attempts=?,score=? WHERE id=?", (state, attempts, score, gid))
            answer_text, correct = json.dumps(answer), int(won)
            shown = "-".join(str(n) for n in answer)
            outcome = (f"correct, beacon unlocked, score {score}/{MAX_ATTEMPTS}" if won
                       else "wrong, challenge lost" if state == "LOST"
                       else f"wrong, {MAX_ATTEMPTS - attempts} attempt(s) left")
            detail = f"Guessed {shown} · {outcome}"
            summary = f"guessed {shown} in Commons Challenge #{gid} · {outcome}"
    move_id = cx.execute("INSERT INTO game_moves(game_id,agent_id,move,detail,answer,correct,created_at) VALUES (?,?,?,?,?,?,?)",
                         (gid, agent_id, verb, detail, answer_text, correct, stamp)).lastrowid
    payload.update({"game_id": gid, "move_id": move_id, "state": state, "score": score,
                    "attempts_left": MAX_ATTEMPTS - attempts})
    return summary


def _has_tables(cx: sqlite3.Connection) -> bool:
    """False on a database opened read-only before any writer process created the game tables."""
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='game_moves'").fetchone() is not None


def count(cx: sqlite3.Connection) -> int:
    return cx.execute("SELECT COUNT(*) FROM games").fetchone()[0] if _has_tables(cx) else 0


def public_games(cx: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    """Latest games for the read-only observatory. Unshared clue values are NULL in SQL, never fetched."""
    if not _has_tables(cx):
        return []
    games = [dict(r) for r in cx.execute("SELECT id,state,attempts,score,demo,created_at FROM games ORDER BY id DESC LIMIT ?", (limit,))]
    for game in games:
        game["max_attempts"] = MAX_ATTEMPTS
        game["clues"] = [dict(r) for r in cx.execute('''SELECT c.position,c.agent_id,a.name agent,a.role,c.shared,
            CASE WHEN c.shared=1 THEN c.value ELSE NULL END value
            FROM game_clues c JOIN agents a ON a.id=c.agent_id WHERE c.game_id=? ORDER BY c.position''', (game["id"],))]
        game["moves"] = [dict(r) for r in cx.execute('''SELECT m.id,m.move,a.name agent,m.detail,m.created_at,
            COALESCE(ap.source, CASE WHEN g.demo=1 THEN 'demo' ELSE 'unknown' END) source,ap.model,COALESCE(ap.verified,0) verified
            FROM game_moves m JOIN games g ON g.id=m.game_id JOIN agents a ON a.id=m.agent_id
            LEFT JOIN action_provenance ap ON ap.kind=m.move AND ap.ref_id=m.id
            WHERE m.game_id=? ORDER BY m.id''', (game["id"],))]
        sources = {m["source"] for m in game["moves"]}
        # 'mixed' = moves from more than one origin (e.g. engine and api); demo never mixes with them.
        game["source"] = "demo" if game["demo"] else sources.pop() if len(sources) == 1 else "mixed" if sources else "unknown"
    return games


def context(cx: sqlite3.Connection, agent_id: int) -> dict[str, Any]:
    """Small private view for one agent: its active (non-demo) games, including ITS OWN clue only."""
    active = []
    for game in cx.execute('''SELECT g.id,g.attempts,c.position,c.value,c.shared FROM games g
            JOIN game_clues c ON c.game_id=g.id AND c.agent_id=?
            WHERE g.state='ACTIVE' AND g.demo=0 ORDER BY g.id DESC LIMIT 2''', (agent_id,)).fetchall():
        seats = [dict(r) for r in cx.execute('''SELECT c.position,a.name agent,
            CASE WHEN c.shared=1 THEN c.value ELSE NULL END digit
            FROM game_clues c JOIN agents a ON a.id=c.agent_id WHERE c.game_id=? ORDER BY c.position''', (game["id"],))]
        wrong = [json.loads(r[0]) for r in cx.execute(
            "SELECT answer FROM game_moves WHERE game_id=? AND move='game_guess' AND correct=0 ORDER BY id", (game["id"],))]
        all_shared = all(s["digit"] is not None for s in seats)
        active.append({"id": game["id"], "attempts_left": MAX_ATTEMPTS - game["attempts"],
                       "your_clue": {"position": game["position"], "digit": game["value"], "shared": bool(game["shared"])},
                       "seats": seats, "wrong_guesses": wrong,
                       "next": "game_guess" if all_shared else "wait for the other clues" if game["shared"] else "game_share"})
    can_start = cx.execute("SELECT 1 FROM games WHERE state='ACTIVE' AND demo=0").fetchone() is None
    return {"active": active, "can_start": can_start}
