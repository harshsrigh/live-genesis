"""Operator controls: credential lifecycle, receipts, budget gates, quotas, audit, backup.

Ported (behaviour, not code) from the reference branch `codex/local-0.2` onto this
codebase's tables. Everything here is local bookkeeping: nothing contacts a provider,
nothing moves money, and no function returns or stores a bearer key except
`rotate_key`, which hands the new key to the operator CLI exactly once.

Honest limits:
* Budget figures are ESTIMATES plus whatever the operator declares in
  `LIVE_OTHER_COMMITTED_USD`. The provider's billing page is the only source of truth.
* Receipts stop a caller from *labelling* an action `engine` without a settled
  reservation. Code running inside this process with database access could still forge
  rows; this is a consistency control, not a sandbox.
* `RateLimiter` state lives in one process's memory and is lost on restart; the client
  address is the direct peer unless `LIVE_TRUSTED_PROXIES` names the proxy in front.

`now` is the single time source for expiry, quota windows and the HTTP limiter; tests
replace it (`monkeypatch.setattr(controls, "now", ...)`) instead of sleeping.
"""
from __future__ import annotations

import hashlib
import ipaddress
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from . import providers


def now() -> float:
    """Seconds since the epoch. Replaced in tests."""
    return time.time()


def _stamp() -> str:
    from .core import utcnow
    return utcnow()


# ---- Schema ---------------------------------------------------------------------------
KEY_LIFETIME_SECONDS = 90 * 86400

_COLUMNS = [
    ("agents", "enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("agents", "key_expires", "REAL NOT NULL DEFAULT 0"),  # epoch seconds; 0 = no expiry
    # 1 = operated by an outside client holding the key (cli set-external): the engine never calls or pays for it.
    ("agents", "external", "INTEGER NOT NULL DEFAULT 0"),
    # Reservations made before this migration keep NULL here; nothing is inferred for them.
    ("budget_reservations", "provider", "TEXT"),
    ("budget_reservations", "round_id", "INTEGER"),
    ("budget_reservations", "input_rate", "REAL"),
    ("budget_reservations", "output_rate", "REAL"),
    ("budget_reservations", "used", "INTEGER NOT NULL DEFAULT 0"),  # 1 = receipt consumed
    ("budget_reservations", "reconciled_usd", "REAL"),  # operator-verified charge, if entered
    ("action_provenance", "reservation_id", "INTEGER"),
]


def migrate(cx: sqlite3.Connection) -> None:
    """Additive and repeatable: safe on a new database and on one created by any earlier version."""
    for table, column, declaration in _COLUMNS:
        if column not in {r[1] for r in cx.execute(f"PRAGMA table_info({table})")}:
            cx.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    cx.executescript('''
    CREATE TABLE IF NOT EXISTS operator_audit (
        id INTEGER PRIMARY KEY, operation TEXT NOT NULL, agent_id INTEGER, created_at TEXT NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS operator_audit_no_update BEFORE UPDATE ON operator_audit
        BEGIN SELECT RAISE(ABORT, 'operator_audit is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS operator_audit_no_delete BEFORE DELETE ON operator_audit
        BEGIN SELECT RAISE(ABORT, 'operator_audit is append-only'); END;
    CREATE TABLE IF NOT EXISTS agent_limits (
        agent_id INTEGER NOT NULL REFERENCES agents(id), kind TEXT NOT NULL,
        maximum INTEGER NOT NULL, PRIMARY KEY (agent_id, kind)
    );
    CREATE TABLE IF NOT EXISTS quota_windows (
        agent_id INTEGER NOT NULL, kind TEXT NOT NULL, bucket INTEGER NOT NULL,
        count INTEGER NOT NULL, PRIMARY KEY (agent_id, kind)
    );
    CREATE INDEX IF NOT EXISTS budget_reservations_round ON budget_reservations(round_id, agent_id);
    -- One row per engine turn, for the operator only (`cli turns`). Categories and counts: never model text,
    -- never provider error text. Not part of /api/overview or of any export.
    CREATE TABLE IF NOT EXISTS engine_turns (
        id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL REFERENCES rounds(id),
        agent_id INTEGER NOT NULL REFERENCES agents(id),
        reservation_id INTEGER REFERENCES budget_reservations(id),
        outcome TEXT NOT NULL CHECK(outcome IN ('recorded','truncated','invalid_json','invalid_schema',
                                                'action_refused','inference_failed','not_called')),
        stop TEXT CHECK(stop IN ('end','length','other','unknown')),
        input_tokens INTEGER, output_tokens INTEGER,
        memory TEXT NOT NULL DEFAULT 'none' CHECK(memory IN ('written','unchanged','omitted','not_text','refused','none')),
        memory_refusal TEXT, created_at TEXT NOT NULL, refusal TEXT, verb TEXT
    );
    CREATE INDEX IF NOT EXISTS engine_turns_round ON engine_turns(round_id, id);
    CREATE TRIGGER IF NOT EXISTS engine_turns_no_update BEFORE UPDATE ON engine_turns
        BEGIN SELECT RAISE(ABORT, 'engine_turns is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS engine_turns_no_delete BEFORE DELETE ON engine_turns
        BEGIN SELECT RAISE(ABORT, 'engine_turns is append-only'); END;
    ''')
    # Added after the table first shipped: why an action_refused / invalid_schema turn recorded nothing.
    # ADD COLUMN is not an UPDATE: the append-only triggers stay, and existing rows read NULL.
    if "refusal" not in {r[1] for r in cx.execute("PRAGMA table_info(engine_turns)")}:
        cx.execute("ALTER TABLE engine_turns ADD COLUMN refusal TEXT")
    # Added later still: WHICH verb a schema-valid reply chose (idle included: an idle turn records no event,
    # so this column is its only trace). Same additive pattern; rows written before it read NULL.
    if "verb" not in {r[1] for r in cx.execute("PRAGMA table_info(engine_turns)")}:
        cx.execute("ALTER TABLE engine_turns ADD COLUMN verb TEXT")


# ---- Engine turn diagnostics (operator-only) -------------------------------------------
# outcome: recorded (an action was stored) | truncated (reply cut off at the output limit) | invalid_json |
#          invalid_schema | action_refused (validation, guards, quota, engine contribute cap) |
#          inference_failed (the call or its usage failed; see the reservation) | not_called (nothing was sent).
# memory:  written | unchanged (same text as the previous note, or blank) | omitted (the reply had no `memory`) |
#          not_text (`memory` was not a string) | refused (a content guard; category in memory_refusal) |
#          none (no action was recorded, so memory was not considered).
# refusal: only for invalid_schema / action_refused: a category built by engine.refusal_category from a fixed
#          vocabulary (`422:validation`, `404:not_found`, `409:conflict`, `429:quota`, `<status>:guard:<guard category>`,
#          `schema:<request field names>`, `contribute_cap`, `error:<ExceptionClass>`). Never model text, never the
#          free-text error detail.
# verb:    the action verb of a reply that passed the request schema (recorded and action_refused turns), and only
#          when it is one of the known verbs (known_verbs); NULL otherwise and for rows older than the column.
REFUSAL_SHAPE = re.compile(r"[A-Za-z0-9_:,.()' -]{1,120}")

def record_turn(round_id: int, agent_id: int, outcome: str, *, reservation: int | None = None, stop: str | None = None,
                input_tokens: int | None = None, output_tokens: int | None = None, memory: str = "none",
                memory_refusal: str | None = None, refusal: str | None = None, verb: str | None = None) -> int:
    """Append one engine-turn diagnostic. Callers pass categories and counts only: `memory_refusal` is a guard
    CATEGORY (as returned by core.update_memory), never model text or a provider message. `refusal` is a
    category from engine.refusal_category; a value that does not look like one is stored as 'unrecognised'.
    `verb` is stored only when it is a known action verb (known_verbs); anything else is stored as NULL."""
    if refusal is not None and not (isinstance(refusal, str) and REFUSAL_SHAPE.fullmatch(refusal)):
        refusal = "unrecognised"
    from . import core  # local import: core imports this module
    if not (isinstance(verb, str) and verb in known_verbs()):
        verb = None
    with core.connect() as cx:
        return int(cx.execute(
            "INSERT INTO engine_turns(round_id,agent_id,reservation_id,outcome,stop,input_tokens,output_tokens,memory,"
            "memory_refusal,created_at,refusal,verb) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (round_id, agent_id, reservation, outcome, stop, input_tokens, output_tokens, memory,
             memory_refusal[:120] if isinstance(memory_refusal, str) else None, _stamp(), refusal, verb)).lastrowid)


def known_verbs() -> frozenset[str]:
    """Every action verb core.submit_action accepts: the only values engine_turns.verb may hold."""
    from . import core, documents, games, governance
    return frozenset((*core.BASE_VERBS, *games.VERBS, *documents.VERBS, *governance.VERBS))


def turns(round_id: int | None = None) -> dict[str, Any]:
    """Engine turns of one round (default: the latest round that has any), oldest first, with counts by outcome."""
    from . import core
    with core.connect() as cx:
        if round_id is None:
            round_id = cx.execute("SELECT MAX(round_id) FROM engine_turns").fetchone()[0]
        rows = [] if round_id is None else [dict(r) for r in cx.execute(
            "SELECT t.id,t.round_id AS round,a.name AS agent,t.reservation_id AS reservation,t.outcome,t.verb,t.stop,"
            "t.input_tokens,t.output_tokens,t.memory,t.memory_refusal,t.refusal,t.created_at "
            "FROM engine_turns t JOIN agents a ON a.id=t.agent_id WHERE t.round_id=? ORDER BY t.id", (round_id,))]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    return {"round": round_id, "outcomes": counts, "turns": rows,
            "note": "Operator diagnostics: categories and token counts only, no model text. Never shown on the public page."}


def audit(cx: sqlite3.Connection, operation: str, agent_id: int | None = None) -> None:
    """Append one operator change. Callers must never put key material in `operation`."""
    cx.execute("INSERT INTO operator_audit(operation,agent_id,created_at) VALUES (?,?,?)",
               (operation[:200], agent_id, _stamp()))


def active_agent_ids(cx: sqlite3.Connection) -> list[int]:
    """Ids of agents whose access has not been revoked, in id order."""
    return [r[0] for r in cx.execute("SELECT id FROM agents WHERE enabled=1 ORDER BY id")]


# ---- Credential lifecycle -------------------------------------------------------------

def check_credential(agent: Any, credential_hash: str | None = None) -> None:
    """Refuse a disabled agent (403) or an expired / replaced key (401).

    `credential_hash` is the key hash the caller authenticated with. core.submit_action
    passes it again INSIDE its write transaction, so a request that authenticated just
    before a rotation or revocation is not recorded after it.
    """
    if not agent["enabled"]:
        raise HTTPException(403, DISABLED)
    if credential_hash is not None and not secrets.compare_digest(agent["key_hash"], credential_hash):
        raise HTTPException(401, EXPIRED)
    if agent["key_expires"] and agent["key_expires"] <= now():
        raise HTTPException(401, EXPIRED)


def check_actor(agent: Any, credential_hash: str | None) -> None:
    """submit_action hook. Expiry concerns the bearer key, so it is checked only for
    callers that present one; the operator's own engine is not locked out by it."""
    if credential_hash is None:
        if not agent["enabled"]:
            raise HTTPException(403, DISABLED)
        return
    check_credential(agent, credential_hash)


def rotate_key(agent_id: int, *, revoke: bool = False) -> str | None:
    """Replace an agent's key. The old key stops working when this transaction commits.

    rotate: returns the new key (the only copy; the database keeps its SHA-256 hash),
    re-enables the agent and sets expiry to now + 90 days.
    revoke: disables the agent, stores the hash of a discarded random key, returns None.
    """
    from .core import connect
    key = "live_" + secrets.token_urlsafe(32)
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        if not cx.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
            raise ValueError("Unknown agent")
        cx.execute("UPDATE agents SET key_hash=?,enabled=?,key_expires=? WHERE id=?",
                   (hashlib.sha256(key.encode()).hexdigest(), int(not revoke),
                    0 if revoke else now() + KEY_LIFETIME_SECONDS, agent_id))
        audit(cx, "revoke-key" if revoke else "rotate-key", agent_id)
    return None if revoke else key


# ---- Per-agent limits and fixed-window quotas -----------------------------------------
# kind -> (environment variable, built-in default, window seconds or None for "per round")
LIMITS: dict[str, tuple[str, int, int | None]] = {
    "actions-round": ("LIVE_ACTIONS_PER_ROUND", 1, None),
    "actions-minute": ("LIVE_ACTIONS_PER_MINUTE", 10, 60),
    "actions-day": ("LIVE_ACTIONS_PER_DAY", 100, 86400),
    "inference-hour": ("LIVE_CALLS_PER_HOUR", 12, 3600),
    "searches-minute": ("LIVE_SEARCHES_PER_MINUTE", 30, 60),  # GET /api/agent/search, per agent
}
# Only windows this short answer 429 with a Retry-After header; for longer ones (hour, day)
# the detail says that retrying soon is pointless.
RETRY_AFTER_MAX_WINDOW = 60
DISABLED = "Agent disabled by the operator. Do not retry; only the operator can enable it again."
EXPIRED = "Agent credential expired or rotated. Do not retry with it; ask the operator for the current key."


class CallRefused(Exception):
    """A paid call was refused for ONE agent before anything was sent (disabled, hourly quota)."""


def default_limit(kind: str) -> int:
    """Environment default (minimum 1). An unreadable value falls back to the built-in default."""
    variable, builtin, _ = LIMITS[kind]
    try:
        return max(1, int(os.getenv(variable, str(builtin))))
    except ValueError:
        return builtin


def limit(cx: sqlite3.Connection, agent_id: int | None, kind: str) -> int:
    """Effective limit for one agent: the operator's override if set, else the default."""
    if agent_id is not None:
        row = cx.execute("SELECT maximum FROM agent_limits WHERE agent_id=? AND kind=?", (agent_id, kind)).fetchone()
        if row:
            return int(row[0])
    return default_limit(kind)


def set_limit(agent_id: int, kind: str, maximum: int | None) -> None:
    """Operator-only. maximum=None restores the default; 0 blocks that kind for the agent."""
    from .core import connect
    if kind not in LIMITS:
        raise ValueError(f"Unknown quota kind; choose from {sorted(LIMITS)}")
    if maximum is not None and (type(maximum) is not int or not 0 <= maximum <= 10000):
        raise ValueError("Quota must be an integer from 0 to 10000")
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        if not cx.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
            raise ValueError("Unknown agent")
        if maximum is None:
            cx.execute("DELETE FROM agent_limits WHERE agent_id=? AND kind=?", (agent_id, kind))
        else:
            cx.execute("INSERT INTO agent_limits VALUES (?,?,?) ON CONFLICT(agent_id,kind) DO UPDATE SET maximum=excluded.maximum",
                       (agent_id, kind, maximum))
        audit(cx, f"set-quota {kind}: {'default' if maximum is None else maximum}", agent_id)


def consume_window(cx: sqlite3.Connection, agent_id: int, kind: str) -> None:
    """Count one use in the current fixed window, or raise 429. Runs inside the caller's
    write transaction, so a later rejection of the same request rolls the count back."""
    seconds = LIMITS[kind][2]
    bucket = int(now() // seconds)
    maximum = limit(cx, agent_id, kind)
    row = cx.execute("SELECT bucket,count FROM quota_windows WHERE agent_id=? AND kind=?", (agent_id, kind)).fetchone()
    used = row["count"] if row and row["bucket"] == bucket else 0
    if used >= maximum:
        head = f"{kind} quota exhausted ({used}/{maximum})"
        if maximum <= 0:
            raise HTTPException(429, f"{head}: the operator has set this limit to 0. Retrying will not help.")
        wait = max(1, math.ceil((bucket + 1) * seconds - now()))
        if seconds <= RETRY_AFTER_MAX_WINDOW:
            # A short window: a real, bounded wait, so the response says how long.
            raise HTTPException(429, f"{head}. Wait {wait} second(s), then resend (same Idempotency-Key for an action).",
                                headers={"Retry-After": str(wait)})
        raise HTTPException(429, f"{head}. The window resets in about {max(1, round(wait / 60))} minute(s); "
                                 "retrying sooner will not help.")
    cx.execute("INSERT INTO quota_windows(agent_id,kind,bucket,count) VALUES (?,?,?,1) "
               "ON CONFLICT(agent_id,kind) DO UPDATE SET bucket=excluded.bucket,count=?", (agent_id, kind, bucket, used + 1))


def count_search(agent_id: int) -> None:
    """Count one GET /api/agent/search for this agent in its per-minute window, or raise 429
    (with Retry-After). The only thing a search writes; it records no event."""
    from .core import connect
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        consume_window(cx, agent_id, "searches-minute")


def set_external(agent_id: int, external: bool) -> str:
    """Mark one agent as operated by an outside client (True) or by this server's engine (False). Audited.

    An external agent stays a resident (it can post, vote, be seated in games); only engine.run_round skips it."""
    from .core import connect
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        row = cx.execute("SELECT name FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not row:
            raise ValueError("Unknown agent id")
        cx.execute("UPDATE agents SET external=? WHERE id=?", (int(bool(external)), agent_id))
        audit(cx, f"set-external {'on' if external else 'off'}", agent_id)
        return row["name"]


# ---- Receipt-bound engine provenance --------------------------------------------------

def before_record(cx: sqlite3.Connection, agent_id: int, origin: dict[str, Any], demo: bool) -> dict[str, Any]:
    """submit_action hook, after the replay and round-quota checks and before any write.

    * Engine provenance: `origin["receipt"]` must be a budget reservation of THIS agent,
      closed with recorded provider usage and not used before. It is marked used here, in
      the caller's transaction, and its provider/model replace anything the caller said.
    * Non-demo actions (idle included) count against the per-minute and per-day windows.
    """
    receipt = origin.get("receipt")
    if receipt is not None:
        row = None
        if not demo and type(receipt) is int:
            row = cx.execute("SELECT * FROM budget_reservations WHERE id=? AND agent_id=? AND used=0 "
                             "AND usage_id IS NOT NULL AND status IN ('settled','unconfirmed')", (receipt, agent_id)).fetchone()
        if row is None or not (row["provider"] or origin.get("provider")):
            raise HTTPException(409, "Invalid or consumed model receipt")
        if cx.execute("SELECT external FROM agents WHERE id=?", (agent_id,)).fetchone()[0]:
            # Second line of defence behind engine.run_round, which never selects such an agent.
            raise HTTPException(409, "Externally operated agent cannot record engine actions")
        cx.execute("UPDATE budget_reservations SET used=1 WHERE id=?", (receipt,))
        origin = {**origin, "provider": row["provider"] or origin["provider"], "model": row["model"]}
    if not demo:
        consume_window(cx, agent_id, "actions-minute")
        consume_window(cx, agent_id, "actions-day")
    return origin


# ---- Budget gates ---------------------------------------------------------------------
PRE_VALIDATION_GATE_USD = 1000.0
PROJECT_BUDGET_MAX_USD = 4000.0  # LIVE_PROJECT_BUDGET_USD must stay strictly below this


class ConfigError(RuntimeError):
    """Operator configuration is unusable. Raised before any provider call (fail closed)."""


def _number(name: str, default: str, *, positive: bool) -> float:
    raw = os.getenv(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ConfigError(f"{name} must be a finite {'positive' if positive else 'non-negative'} number; "
                          "fix it in .env. No paid call was made.")
    return value


def project_gates() -> dict[str, float]:
    """Validated project-level ceilings. `available_usd` is what API spend may reach."""
    project = _number("LIVE_PROJECT_BUDGET_USD", "3999", positive=True)
    other = _number("LIVE_OTHER_COMMITTED_USD", "0", positive=False)
    if project >= PROJECT_BUDGET_MAX_USD:
        raise ConfigError("LIVE_PROJECT_BUDGET_USD must be below 4000 (the proposed project budget). No paid call was made.")
    return {"project_budget_usd": project, "other_committed_usd": other, "pre_validation_gate_usd": PRE_VALIDATION_GATE_USD,
            "available_usd": min(project - other, PRE_VALIDATION_GATE_USD - other)}


def budget_config() -> dict[str, float]:
    """Everything a paid round needs, validated up front.

    Rates must be positive (fail closed). The one exception: `openai-compatible` with a loopback
    base URL may use zero rates, so a model running on this machine is recorded as $0 estimated
    (providers.zero_rates_allowed). A hosted endpoint, free tier or not, still needs positive rates."""
    gates = project_gates()
    gates["spend_limit_usd"] = _number("LIVE_SPEND_LIMIT_USD", "10", positive=True)
    positive = not providers.zero_rates_allowed()
    gates["input_rate"] = _number("LIVE_INPUT_USD_PER_M", "0", positive=positive)
    gates["output_rate"] = _number("LIVE_OUTPUT_USD_PER_M", "0", positive=positive)
    return gates


def effective_ceiling(limit_usd: float) -> float:
    """min(caller's spend limit, project budget - other commitments, $1,000 gate - other commitments)."""
    if isinstance(limit_usd, bool) or not isinstance(limit_usd, (int, float)) or not math.isfinite(limit_usd) or limit_usd <= 0:
        raise ConfigError("The spend limit must be a finite positive number. No paid call was made.")
    return min(float(limit_usd), project_gates()["available_usd"])


def ceiling_report() -> dict[str, Any]:
    """Ceilings for status output. Never raises: a public read must not fail on bad config."""
    try:
        gates = project_gates()
        spend = _number("LIVE_SPEND_LIMIT_USD", "10", positive=True)
    except ConfigError as err:
        return {"error": str(err)}
    return {"spend_limit_usd": spend, "project_budget_usd": gates["project_budget_usd"],
            "other_committed_usd": gates["other_committed_usd"], "pre_validation_gate_usd": PRE_VALIDATION_GATE_USD,
            "effective_usd": round(min(spend, gates["available_usd"]), 6)}


def reservation_rates(input_rate: float | None, output_rate: float | None) -> tuple[float, float]:
    """Rates fixed on a reservation at reserve time (same defaults as usage recording)."""
    def one(value: float | None, name: str, default: str) -> float:
        if value is None:
            return _number(name, default, positive=False)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ConfigError(f"{name} must be a finite non-negative number. No paid call was made.")
        return float(value)
    return one(input_rate, "LIVE_INPUT_USD_PER_M", "0.40"), one(output_rate, "LIVE_OUTPUT_USD_PER_M", "1.50")


def unconfirmed_ids(cx: sqlite3.Connection) -> list[int]:
    return [r[0] for r in cx.execute("SELECT id FROM budget_reservations WHERE status='unconfirmed' ORDER BY id")]


def reconcile_message(ids: list[int]) -> str:
    listed = ", ".join(map(str, ids[:5])) + (" ..." if len(ids) > 5 else "")
    return (f"Unconfirmed provider charge (reservation {listed}): no further paid calls. Check the provider's bill, then run "
            f"`python -m live_genesis.cli reconcile --receipt {ids[0]} --actual-usd <amount charged for that call>`.")


def before_reserve(cx: sqlite3.Connection, agent_id: int | None, count_call: bool) -> None:
    """reserve_budget hook, inside its write transaction and after the ceiling check."""
    if agent_id is None:
        return
    row = cx.execute("SELECT enabled FROM agents WHERE id=?", (agent_id,)).fetchone()
    if row is not None and not row["enabled"]:
        raise CallRefused("Agent disabled by the operator; no call made")
    if count_call:
        try:
            consume_window(cx, agent_id, "inference-hour")
        except HTTPException as err:
            raise CallRefused(f"{err.detail.rstrip('.')}; no call made") from None


def reconcile_reservation(reservation_id: int, actual_usd: float) -> dict[str, Any]:
    """Operator-entered, verified charge for ONE held or unconfirmed reservation.

    The figure is typed in from the provider's bill; nothing is fetched. Usage already
    recorded for the call (an overrun) is not counted twice: only the difference is added.
    The reservation is closed and can never serve as an engine receipt afterwards.
    """
    from .core import _budget, connect
    if isinstance(actual_usd, bool) or not isinstance(actual_usd, (int, float)) or not math.isfinite(actual_usd) \
            or not 0 <= actual_usd < 1_000_000:
        raise ValueError("Actual charge must be a finite non-negative number")
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        row = cx.execute("SELECT * FROM budget_reservations WHERE id=?", (reservation_id,)).fetchone()
        if not row or row["status"] not in ("held", "unconfirmed"):
            raise ValueError("Only held or unconfirmed reservations can be reconciled")
        recorded = 0.0
        if row["usage_id"] is not None:
            recorded = float(cx.execute("SELECT estimated_usd FROM api_usage WHERE id=?", (row["usage_id"],)).fetchone()[0])
        usage_id = row["usage_id"]
        if actual_usd != recorded:
            # A zero-token adjustment row: the sum over api_usage now equals the verified charge.
            cur = cx.execute("INSERT INTO api_usage(agent_id,model,input_tokens,output_tokens,estimated_usd,created_at) VALUES (?,?,0,0,?,?)",
                             (row["agent_id"], row["model"], actual_usd - recorded, _stamp()))
            usage_id = usage_id if usage_id is not None else cur.lastrowid
        cx.execute("UPDATE budget_reservations SET status='settled',used=1,usage_id=?,reconciled_usd=?,closed_at=? WHERE id=?",
                   (usage_id, float(actual_usd), _stamp(), reservation_id))
        audit(cx, f"reconcile reservation {reservation_id}: {float(actual_usd)}", row["agent_id"])
        return _budget(cx)


# ---- Resumable rounds -----------------------------------------------------------------

def agents_done_in_round(cx: sqlite3.Connection, round_id: int) -> set[int]:
    """Agents that must not be called again in this round: they already recorded an action
    in it, or a paid call was reserved for them in it and not released (settled,
    unconfirmed, or still held by a run that died mid-call)."""
    done = {r[0] for r in cx.execute("SELECT agent_id FROM round_actions WHERE round_id=? AND verb!='idle'", (round_id,))}
    done |= {r[0] for r in cx.execute("SELECT agent_id FROM budget_reservations WHERE round_id=? AND status!='released' "
                                      "AND agent_id IS NOT NULL", (round_id,))}
    return done


def mixed_origin_agents() -> list[str]:
    """Names of agents that are NOT marked external but have recorded both `api` and `engine`
    actions: a sign that an outside client is using an identity the engine also runs (cli status)."""
    from .core import connect
    with connect() as cx:
        return [r[0] for r in cx.execute("""SELECT a.name FROM agents a JOIN events e ON e.agent_id=a.id
            JOIN action_provenance ap ON ap.event_id=e.id WHERE a.external=0 AND ap.source IN ('api','engine')
            GROUP BY a.id HAVING COUNT(DISTINCT ap.source)=2 ORDER BY a.id""")]


# ---- In-memory HTTP rate limits -------------------------------------------------------

LIMITER_MAX_KEYS = 10_000  # distinct keys remembered per window; see RateLimiter


class RateLimiter:
    """Fixed-window counter per key, in THIS process's memory only.

    Lost on restart and not shared between worker processes. Memory is bounded: all counts
    are dropped when the window changes, and at most `max_keys` distinct keys are
    remembered within one window. A key that arrives when the table is full is not
    counted and is allowed (fail open): refusing it would let an attacker who rotates
    addresses lock out everyone else.

    Two instances guard /api/agent/* (see app.requires_agent):
    * `api_limiter`: requests per AUTHENTICATED agent id, counted after authentication.
    * `auth_failure_limiter`: FAILED authentications per client address.
    """

    def __init__(self, maximum: int | None = None, seconds: int = 60, clock: Callable[[], float] | None = None,
                 *, variable: str = "LIVE_API_REQUESTS_PER_MINUTE", default: int = 120, max_keys: int = LIMITER_MAX_KEYS):
        self.maximum, self.seconds, self.clock = maximum, seconds, clock
        self.variable, self.default, self.max_keys = variable, default, max_keys
        self._lock = threading.Lock()
        self._window = -1
        self._counts: dict[str, int] = {}

    def limit(self) -> int:
        if self.maximum is not None:
            return self.maximum
        try:
            return max(1, int(os.getenv(self.variable, str(self.default))))
        except ValueError:
            return self.default

    def reset(self) -> None:
        with self._lock:
            self._window, self._counts = -1, {}

    def _roll(self) -> None:
        window = int((self.clock or now)() // self.seconds)
        if window != self._window:
            self._window, self._counts = window, {}

    def allow(self, client: str) -> bool:
        """Count one use for this key and say whether it was within the limit."""
        with self._lock:
            self._roll()
            used = self._counts.get(client, 0)
            if used >= self.limit():
                return False
            if client in self._counts or len(self._counts) < self.max_keys:
                self._counts[client] = used + 1
            return True

    def blocked(self, client: str) -> bool:
        """True when this key has already used its whole allowance. Counts nothing."""
        with self._lock:
            self._roll()
            return self._counts.get(client, 0) >= self.limit()

    def size(self) -> int:
        """Number of keys currently remembered (never above `max_keys`)."""
        with self._lock:
            return len(self._counts)

    def retry_after(self) -> int:
        """Whole seconds until the current window ends (at least 1)."""
        current = (self.clock or now)()
        return max(1, math.ceil((int(current // self.seconds) + 1) * self.seconds - current))


api_limiter = RateLimiter()  # keyed on the authenticated agent id
auth_failure_limiter = RateLimiter(variable="LIVE_AUTH_FAILURES_PER_MINUTE", default=10)  # keyed on the client address


def trusted_proxies() -> frozenset[str]:
    """Peer addresses whose X-Forwarded-For header is believed (LIVE_TRUSTED_PROXIES, comma-separated
    exact addresses, empty by default). No networks or wildcards: list each proxy address."""
    return frozenset(_address(part) for part in os.getenv("LIVE_TRUSTED_PROXIES", "").split(",") if part.strip())


def _address(text: str) -> str:
    """Canonical text of an IP address (so ::1 and 0:0:0:0:0:0:0:1 compare equal); other text lower-cased."""
    text = text.strip()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text.lower()


def client_address(peer: str | None, forwarded: list[str] | None = None) -> str:
    """The address the failed-authentication limit is keyed on.

    Default: the direct peer, and X-Forwarded-For is ignored (any client can send that header).
    Only when the peer is listed in LIVE_TRUSTED_PROXIES: the right-most X-Forwarded-For entry
    that is not itself a trusted proxy. That is the entry the nearest trusted proxy appended;
    everything to its left is client-supplied and never used. If the header is missing, holds
    only trusted addresses, or that entry is not an IP address, the peer is used.
    """
    peer_text = _address(peer) if peer else "unknown"
    trusted = trusted_proxies()
    if peer_text not in trusted or not forwarded:
        return peer_text
    for entry in reversed([part for value in forwarded for part in value.split(",")]):
        candidate = _address(entry)
        if candidate in trusted:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return peer_text
    return peer_text


# ---- Backup ---------------------------------------------------------------------------

def backup(destination: str | os.PathLike[str]) -> Path:
    """Copy the live database with SQLite's online backup API. Never overwrites a file.

    The copy holds everything the database holds: private agent memory and key hashes.
    """
    from .core import connect
    target = Path(destination).expanduser().resolve()
    if target.exists():
        raise FileExistsError("Backup destination already exists; choose a new file name")
    if not target.parent.is_dir():
        raise FileNotFoundError("Backup destination directory does not exist")
    with connect() as source:
        copy = sqlite3.connect(str(target))
        try:
            source.backup(copy)
        except BaseException:
            copy.close()
            target.unlink(missing_ok=True)
            raise
        copy.close()
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target
