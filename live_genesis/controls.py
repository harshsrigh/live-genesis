"""Operator controls: budget gates, reconciliation, the operator audit log, the request limit, backup.

Everything here is local bookkeeping: nothing contacts a provider and nothing moves money.

Honest limits:
* Budget figures are ESTIMATES plus whatever the operator declares in
  `LIVE_OTHER_COMMITTED_USD`. The provider's billing page is the only source of truth.
* `RateLimiter` state lives in one process's memory and is lost on restart; the client
  address is the direct peer unless `LIVE_TRUSTED_PROXIES` names the proxy in front.

`now` is the single time source for the HTTP limiter; tests replace it
(`monkeypatch.setattr(controls, "now", ...)`) instead of sleeping.
"""
from __future__ import annotations

import ipaddress
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import providers


def now() -> float:
    """Seconds since the epoch. Replaced in tests."""
    return time.time()


def _stamp() -> str:
    from .core import utcnow
    return utcnow()


# ---- Schema ---------------------------------------------------------------------------
_COLUMNS = [
    # Added to budget_reservations after it first shipped. Rows from before keep NULL here; nothing is inferred.
    ("budget_reservations", "provider", "TEXT"),
    ("budget_reservations", "input_rate", "REAL"),
    ("budget_reservations", "output_rate", "REAL"),
    ("budget_reservations", "reconciled_usd", "REAL"),  # operator-verified charge, if entered
]


def migrate(cx: sqlite3.Connection) -> None:
    """Additive and repeatable: safe on a new database and on one created by any earlier version.
    Only the tables named here are touched; an older database's other tables are left as they are."""
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
    ''')


def audit(cx: sqlite3.Connection, operation: str, agent_id: int | None = None) -> None:
    """Append one operator change. Callers must never put key material in `operation`.
    `agent_id` is a column of the table as it first shipped; nothing writes it any more (NULL)."""
    cx.execute("INSERT INTO operator_audit(operation,agent_id,created_at) VALUES (?,?,?)",
               (operation[:200], agent_id, _stamp()))


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


def spend_config() -> dict[str, float]:
    """The ceilings without the price rates: project gates plus LIVE_SPEND_LIMIT_USD, validated. For an archive
    run whose every model is on a named endpoint with its own rates (the global pair is then not used)."""
    gates = project_gates()
    gates["spend_limit_usd"] = _number("LIVE_SPEND_LIMIT_USD", "10", positive=True)
    return gates


def budget_config() -> dict[str, float]:
    """Everything a paid run needs, validated up front.

    Rates must be positive (fail closed). The one exception: `openai-compatible` with a loopback
    base URL may use zero rates, so a model running on this machine is recorded as $0 estimated
    (providers.zero_rates_allowed). A hosted endpoint, free tier or not, still needs positive rates here.
    (An Answers Over Time named endpoint carries its own rates and is not priced by this pair: providers.endpoints.)"""
    gates = spend_config()
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


def reconcile_reservation(reservation_id: int, actual_usd: float) -> dict[str, Any]:
    """Operator-entered, verified charge for ONE held or unconfirmed reservation.

    The figure is typed in from the provider's bill; nothing is fetched. Usage already
    recorded for the call (an overrun) is not counted twice: only the difference is added.
    The reservation is closed.
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
        cx.execute("UPDATE budget_reservations SET status='settled',usage_id=?,reconciled_usd=?,closed_at=? WHERE id=?",
                   (usage_id, float(actual_usd), _stamp(), reservation_id))
        audit(cx, f"reconcile reservation {reservation_id}: {float(actual_usd)}")
        return _budget(cx)


# ---- Estimates shared by every paid run --------------------------------------------------
# Input-token bound for a hold: one token is at least one byte for byte-level BPE tokenizers,
# so UTF-8 bytes of system + prompt, plus this margin for message framing, bounds the count.
# If a provider ever reports more, the call settles as `unconfirmed` and the run stops.
INPUT_TOKEN_MARGIN = 200
EPSILON_USD = 1e-9  # float slack only; never a spending allowance
# core rounds committed spend to 6 decimals, so the ceiling handed to reserve_budget carries this
# much slack. The exact cap is the run's own check (sum of settled estimates + next hold <= max_usd).
LEDGER_ROUNDING_USD = 2e-6
COST_CAVEAT = "estimated from token counts and configured rates, not a provider invoice"


def suggested_cap(worst_case_usd: float) -> str:
    """The worst case rounded UP to the next cent, as text for the command line."""
    return f"{max(math.ceil(worst_case_usd * 100 - 1e-9), 1) / 100:.2f}"  # at least 0.01: --max-usd must be positive (zero local rates)


# ---- In-memory HTTP rate limits -------------------------------------------------------

LIMITER_MAX_KEYS = 10_000  # distinct keys remembered per window; see RateLimiter


class RateLimiter:
    """Fixed-window counter per key, in THIS process's memory only.

    Lost on restart and not shared between worker processes. Memory is bounded: all counts
    are dropped when the window changes, and at most `max_keys` distinct keys are
    remembered within one window. A key that arrives when the table is full is not
    counted and is allowed (fail open): refusing it would let an attacker who rotates
    addresses lock out everyone else.

    One instance exists: `public_limiter` counts every request except /health per client address
    (web.limit_public_requests).
    """

    def __init__(self, maximum: int | None = None, seconds: int = 60, clock: Callable[[], float] | None = None,
                 *, variable: str = "LIVE_PUBLIC_REQUESTS_PER_MINUTE", default: int = 300, max_keys: int = LIMITER_MAX_KEYS):
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


# Every request except /health, keyed on the client address (web.limit_public_requests).
# Generous: one page view is a handful of requests (page, stylesheet, mark, fonts).
public_limiter = RateLimiter()


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
    """The address the per-address request limit is keyed on.

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

    The copy holds everything the database holds. For a database made before the agent society was
    removed that includes its tables: private agent memory and agent key hashes.
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
