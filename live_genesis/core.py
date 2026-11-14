"""Small SQLite store: the database connection, the spend-accounting tables and the budget gate.

Answers Over Time keeps its own tables (archive.py, archive_chatbots.py); `init_db` creates them through
`archive.migrate`. Everything about money here is a local ESTIMATE: nothing contacts a provider and
nothing moves money.

A database made before 14 November 2026 also holds the tables of the removed agent society (agents,
posts, proposals ...). This code neither creates, reads, alters nor drops them: they stay as they are.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import archive, config, controls

ROOT = config.ROOT
# Non-secret LIVE_* settings only. Provider API keys are loaded by the paid CLI commands alone.
config.load_settings()
# What a model id shown on a public page may not look like: a dot followed by two or more letters that
# end the label (".org", ".ly", ".example-"), i.e. a domain name. A dot before a digit is a version
# ("gpt-5.1"). Used by archive.model_id_ok.
DECLARED_DOMAIN = re.compile(r"\.[A-Za-z]{2,}(?![A-Za-z0-9])")

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
    """Open the database. `readonly=True` uses SQLite `mode=ro`: the connection
    cannot write, and a missing file is an error instead of being created."""
    path = db_path()
    if readonly:
        if not path.is_file():
            raise DatabaseUnavailable("The database has not been initialized")
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
    """Create what is missing; idempotent.

    `agent_id` on the two accounting tables is NULL on every row this code writes (the archive asks
    models, not agents). The column stays so that rows written for the removed society's agents in an
    older database still read. In such a database these tables already exist with their original
    definition and are left as they are."""
    with connect() as cx:
        cx.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY, agent_id INTEGER,
            model TEXT NOT NULL, input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL, estimated_usd REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS budget_reservations (
            id INTEGER PRIMARY KEY, agent_id INTEGER, model TEXT NOT NULL,
            reserved_usd REAL NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('held','settled','released','unconfirmed')),
            usage_id INTEGER REFERENCES api_usage(id), created_at TEXT NOT NULL, closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS cost_reconciliations (
            id INTEGER PRIMARY KEY, provider_reported_usd REAL NOT NULL, estimated_usd REAL NOT NULL,
            last_usage_id INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        ''')
        controls.migrate(cx)  # additive reservation columns and the operator audit log
        archive.migrate(cx)  # Answers Over Time: question locks, runs, append-only answers, the chat-app track

def _insert_usage(cx: sqlite3.Connection, agent_id: int | None, model: str, input_tokens: int, output_tokens: int,
                  input_rate: float | None = None, output_rate: float | None = None) -> tuple[int,float]:
    # Rates fixed on a reservation win; otherwise (direct record_usage) the current settings apply.
    if input_rate is None:
        input_rate = float(os.getenv("LIVE_INPUT_USD_PER_M", "0.40"))
    if output_rate is None:
        output_rate = float(os.getenv("LIVE_OUTPUT_USD_PER_M", "1.50"))
    amount = input_tokens*input_rate/1e6+output_tokens*output_rate/1e6
    cur = cx.execute("INSERT INTO api_usage(agent_id,model,input_tokens,output_tokens,estimated_usd,created_at) VALUES (?,?,?,?,?,?)",
                     (agent_id,model,input_tokens,output_tokens,amount,utcnow()))
    return int(cur.lastrowid), amount

def record_usage(agent_id: int | None, model: str, input_tokens: int, output_tokens: int) -> float:
    with connect() as cx:
        return _insert_usage(cx, agent_id, model, input_tokens, output_tokens)[1]

def spent_usd() -> float:
    """Local ESTIMATE from reported token counts and configured rates. Not a provider invoice."""
    with connect() as cx:
        return float(cx.execute('SELECT COALESCE(SUM(estimated_usd),0) FROM api_usage').fetchone()[0])

# ---- Budget reservations and operator reconciliation --------------------------------
# A reservation holds an estimated amount BEFORE a paid call so that concurrent runs
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
                   provider: str | None = None,
                   input_rate: float | None = None, output_rate: float | None = None) -> int:
    """Atomically hold amount_usd against the ceiling. Raises BudgetExceeded when it does not fit.

    The ceiling is the lowest of limit_usd, LIVE_PROJECT_BUDGET_USD - LIVE_OTHER_COMMITTED_USD and
    $1,000 - LIVE_OTHER_COMMITTED_USD. Invalid settings raise controls.ConfigError (fail closed).
    Raises ReconciliationRequired while any reservation is unconfirmed.
    The price rates are stored on the reservation and used again at settlement.
    `agent_id` is None on every call this code makes (see init_db).
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
        return int(cx.execute("INSERT INTO budget_reservations(agent_id,model,reserved_usd,status,created_at,provider,input_rate,output_rate) VALUES (?,?,?,'held',?,?,?,?)",
                              (agent_id,model,amount_usd,utcnow(),provider,input_rate,output_rate)).lastrowid)

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
