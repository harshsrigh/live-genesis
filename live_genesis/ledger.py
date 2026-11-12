"""Internal credit ledger: append-only bookkeeping of NON-REDEEMABLE compute-accounting units.

Credits are not currency. They have no monetary value, cannot be bought, sold or exchanged
for anything outside this database, and nothing in this module (or anywhere else in the
project) converts them to or from dollars. They currently buy nothing at all: an allocation
is an earmark that agents voted for, and it goes back to the commons pool when its author
reports completion (`allocation_returned`) or, if no report comes in time, when the project
lapses (`allocation_lapsed`; see governance.py).

Accounts
  agent       one per agent (`agent_id` set). `agents.credits` is a CACHED COPY of the latest
              `balance_after`, updated in the same transaction; `verify()` compares the two.
  commons     the shared pool (`agent_id` NULL).
  allocation  escrow earmarked for one approved project proposal (`proposal_id` set).

Rules enforced here AND by the table itself (CHECKs and triggers):
  * rows are never updated or deleted;
  * `balance_after` = previous balance of that account + `delta`, and is never negative;
  * credits come into existence only as an agent's `opening` entry (the balance it already
    had when the ledger was introduced / the seeded default) or an `operator_grant` made
    through the CLI and written to `operator_audit`. Every other entry is one half of a
    transfer (two rows sharing `transfer_id`, summing to zero), so supply is conserved.

Honest limits: anyone with file access can drop the triggers; the operator can grant any
amount at will; agents are seeded with an arbitrary opening balance of 100.

Every function that takes `cx` runs inside the caller's write transaction.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException

UNIT_NOTE = ("Credits are internal compute-accounting units with no monetary value. "
             "They cannot be bought, sold or redeemed, and are never converted to or from money.")
MINT_REASONS = ("opening", "operator_grant")
TRANSFER_REASONS = ("contribute_commons", "contribute_proposal", "allocation_funded", "allocation_returned",
                    "allocation_lapsed")
MAX_GRANT = 1_000_000

Account = tuple[str, int | None]  # ("agent", agent_id) | ("commons", None) | ("allocation", proposal_id)
COMMONS: Account = ("commons", None)

_TABLE = '''
CREATE TABLE IF NOT EXISTS {name} (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL CHECK(account IN ('agent','commons','allocation')),
    agent_id INTEGER REFERENCES agents(id),       -- NULL for the commons pool and allocations
    proposal_id INTEGER REFERENCES proposals(id), -- set only for an allocation account
    delta INTEGER NOT NULL,
    balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
    reason TEXT NOT NULL CHECK(reason IN ('opening','operator_grant','contribute_commons',
        'contribute_proposal','allocation_funded','allocation_returned','allocation_lapsed')),
    ref_kind TEXT, ref_id INTEGER,
    transfer_id INTEGER,                          -- shared by the two rows of one transfer
    round_id INTEGER,
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)),
    created_at TEXT NOT NULL,
    CHECK((account='agent' AND agent_id IS NOT NULL AND proposal_id IS NULL)
       OR (account='commons' AND agent_id IS NULL AND proposal_id IS NULL)
       OR (account='allocation' AND agent_id IS NULL AND proposal_id IS NOT NULL)),
    CHECK((reason IN ('opening','operator_grant') AND delta >= 0 AND transfer_id IS NULL)
       OR (reason NOT IN ('opening','operator_grant') AND delta <> 0 AND transfer_id IS NOT NULL))
)'''
# Index and triggers, one statement each so a rebuild can recreate them inside its transaction.
_GUARDS = (
    "CREATE INDEX IF NOT EXISTS credit_ledger_account ON credit_ledger(account, agent_id, proposal_id, id)",
    '''CREATE TRIGGER IF NOT EXISTS credit_ledger_no_update BEFORE UPDATE ON credit_ledger
    BEGIN SELECT RAISE(ABORT, 'credit_ledger is append-only'); END''',
    '''CREATE TRIGGER IF NOT EXISTS credit_ledger_no_delete BEFORE DELETE ON credit_ledger
    BEGIN SELECT RAISE(ABORT, 'credit_ledger is append-only'); END''',
    '''CREATE TRIGGER IF NOT EXISTS credit_ledger_chain BEFORE INSERT ON credit_ledger
    WHEN NEW.balance_after <> NEW.delta + COALESCE((SELECT balance_after FROM credit_ledger
        WHERE account=NEW.account AND agent_id IS NEW.agent_id AND proposal_id IS NEW.proposal_id
        ORDER BY id DESC LIMIT 1), 0)
    BEGIN SELECT RAISE(ABORT, 'credit_ledger balance chain broken'); END''',
)
SCHEMA = ";\n".join((_TABLE.format(name="credit_ledger"), *_GUARDS)) + ";\n"
_COLUMNS = "id,account,agent_id,proposal_id,delta,balance_after,reason,ref_kind,ref_id,transfer_id,round_id,demo,created_at"


def _stamp() -> str:
    from .core import utcnow
    return utcnow()


def has_tables(cx: sqlite3.Connection) -> bool:
    """False on a database opened read-only before any writer process created the ledger."""
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='credit_ledger'").fetchone() is not None


def migrate(cx: sqlite3.Connection) -> None:
    """Additive and repeatable. Existing agents get ONE opening entry equal to their current
    `agents.credits`; running it again adds nothing."""
    cx.executescript(SCHEMA)
    _allow_new_reasons(cx)
    open_accounts(cx)


def _allow_new_reasons(cx: sqlite3.Connection) -> None:
    """One-time upgrade of a ledger whose `reason` CHECK predates `allocation_lapsed`.

    SQLite cannot alter a CHECK, so the table is recreated with the wider list and every row is
    copied byte-for-byte (same ids, same order) in one transaction; no row is updated or dropped,
    and the copy is refused unless row count and total match. Index and triggers are recreated
    in that same transaction."""
    def current() -> bool:
        return "'allocation_lapsed'" in cx.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='credit_ledger'").fetchone()[0]
    if current():
        return
    cx.commit()
    try:
        cx.execute("BEGIN IMMEDIATE")
        if not current():  # re-checked under the write lock
            figures = "SELECT COUNT(*),COALESCE(SUM(delta),0),COALESCE(MAX(id),0) FROM "
            before = tuple(cx.execute(figures + "credit_ledger").fetchone())
            cx.execute("DROP TABLE IF EXISTS credit_ledger_rebuild")
            cx.execute(_TABLE.format(name="credit_ledger_rebuild"))
            cx.execute(f"INSERT INTO credit_ledger_rebuild({_COLUMNS}) SELECT {_COLUMNS} FROM credit_ledger ORDER BY id")
            if tuple(cx.execute(figures + "credit_ledger_rebuild").fetchone()) != before:
                raise RuntimeError("credit_ledger upgrade aborted: the copy does not match the original")
            cx.execute("DROP TABLE credit_ledger")  # takes its index and triggers with it
            cx.execute("ALTER TABLE credit_ledger_rebuild RENAME TO credit_ledger")
            for statement in _GUARDS:
                cx.execute(statement)
        cx.execute("COMMIT")
    except BaseException:
        if cx.in_transaction:
            cx.execute("ROLLBACK")
        raise


def open_accounts(cx: sqlite3.Connection) -> None:
    """Write the opening entry for every agent that has none (new seeds, pre-ledger databases)."""
    for row in cx.execute("SELECT id,credits FROM agents a WHERE NOT EXISTS "
                          "(SELECT 1 FROM credit_ledger l WHERE l.account='agent' AND l.agent_id=a.id) ORDER BY id").fetchall():
        opening = max(0, int(row["credits"]))
        cx.execute("INSERT INTO credit_ledger(account,agent_id,delta,balance_after,reason,ref_kind,ref_id,created_at) "
                   "VALUES ('agent',?,?,?,'opening','agent',?,?)", (row["id"], opening, opening, row["id"], _stamp()))
        if opening != row["credits"]:
            cx.execute("UPDATE agents SET credits=? WHERE id=?", (opening, row["id"]))


def balance(cx: sqlite3.Connection, account: Account) -> int:
    """Current balance of one account as defined by the ledger (0 when it has no entries)."""
    kind, ref = account
    agent_id, proposal_id = (ref if kind == "agent" else None), (ref if kind == "allocation" else None)
    row = cx.execute("SELECT balance_after FROM credit_ledger WHERE account=? AND agent_id IS ? AND proposal_id IS ? "
                     "ORDER BY id DESC LIMIT 1", (kind, agent_id, proposal_id)).fetchone()
    return int(row[0]) if row else 0


def supply(cx: sqlite3.Connection) -> int:
    """Total credits in existence = sum of every entry = sum of all account balances."""
    return int(cx.execute("SELECT COALESCE(SUM(delta),0) FROM credit_ledger").fetchone()[0])


def _entry(cx: sqlite3.Connection, account: Account, delta: int, reason: str, *, ref_kind: str | None, ref_id: int | None,
           transfer_id: int | None, round_id: int | None, demo: bool, stamp: str, row_id: int | None = None) -> int:
    kind, ref = account
    if kind == "agent" and not cx.execute("SELECT 1 FROM credit_ledger WHERE account='agent' AND agent_id=? LIMIT 1", (ref,)).fetchone():
        open_accounts(cx)  # an agent created outside seed_agents: its cached balance becomes its opening entry
    after = balance(cx, account) + delta
    if after < 0:
        raise HTTPException(409, f"Insufficient credits: {-delta} needed, {after - delta} available. Send a smaller amount.")
    cur = cx.execute("INSERT INTO credit_ledger(id,account,agent_id,proposal_id,delta,balance_after,reason,ref_kind,ref_id,"
                     "transfer_id,round_id,demo,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (row_id, kind, ref if kind == "agent" else None, ref if kind == "allocation" else None, delta, after,
                      reason, ref_kind, ref_id, transfer_id, round_id, int(demo), stamp))
    if kind == "agent":
        cx.execute("UPDATE agents SET credits=? WHERE id=?", (after, ref))  # cached copy, same transaction
    return int(cur.lastrowid)


def transfer(cx: sqlite3.Connection, source: Account, target: Account, amount: int, reason: str, *,
             ref_kind: str | None = None, ref_id: int | None = None, round_id: int | None = None,
             demo: bool = False, stamp: str | None = None) -> int:
    """Move `amount` credits between two accounts as a debit row and a credit row. Returns the debit row id.

    Raises HTTPException 409 (and writes nothing) if the source cannot cover it. The caller
    must already hold the write lock (BEGIN IMMEDIATE), which is what makes the read-then-write safe.
    """
    if type(amount) is not int or amount <= 0:
        raise HTTPException(422, "amount must be a positive integer")
    if reason not in TRANSFER_REASONS or source == target:
        raise ValueError("Invalid ledger transfer")
    stamp = stamp or _stamp()
    debit_id = int(cx.execute("SELECT COALESCE(MAX(id),0)+1 FROM credit_ledger").fetchone()[0])
    common = dict(ref_kind=ref_kind, ref_id=ref_id, transfer_id=debit_id, round_id=round_id, demo=demo, stamp=stamp)
    _entry(cx, source, -amount, reason, row_id=debit_id, **common)
    _entry(cx, target, amount, reason, **common)
    return debit_id


def grant(target: str | int, amount: int, reason: str) -> dict[str, Any]:
    """OPERATOR ONLY (CLI `grant-credits`): create credits for one agent or for the commons pool.

    The only way credits come into existence after an account's opening entry. Written to
    `operator_audit` in the same transaction. It is bookkeeping: no money is involved, and
    there is no path from any dollar figure in this project to this function.
    """
    from . import controls
    from .core import connect
    if type(amount) is not int or not 0 < amount <= MAX_GRANT:
        raise ValueError(f"Amount must be a whole number of credits from 1 to {MAX_GRANT}")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A reason is required for every grant")
    reason = " ".join(reason.split())[:120]
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        if target == "commons":
            account, label, agent_id = COMMONS, "commons", None
        else:
            if type(target) is not int or not cx.execute("SELECT 1 FROM agents WHERE id=?", (target,)).fetchone():
                raise ValueError('Unknown target: use an agent id or "commons"')
            account, label, agent_id = ("agent", target), f"agent {target}", target
        controls.audit(cx, f"grant-credits {amount} to {label}: {reason}", agent_id)
        audit_id = int(cx.execute("SELECT MAX(id) FROM operator_audit").fetchone()[0])
        round_id = cx.execute("SELECT MAX(id) FROM rounds").fetchone()[0]
        entry_id = _entry(cx, account, amount, "operator_grant", ref_kind="operator_audit", ref_id=audit_id,
                          transfer_id=None, round_id=round_id, demo=False, stamp=_stamp())
        return {"entry_id": entry_id, "target": label, "granted": amount, "balance": balance(cx, account),
                "total_supply": supply(cx), "note": UNIT_NOTE}


def verify(cx: sqlite3.Connection | None = None) -> list[str]:
    """Consistency check. Returns human-readable discrepancies; an empty list means consistent."""
    if cx is None:
        from .core import connect
        with connect() as own:
            return verify(own)
    problems: list[str] = []
    running: dict[tuple, int] = {}
    transfers: dict[int, list[int]] = {}
    minted = 0
    for row in cx.execute("SELECT * FROM credit_ledger ORDER BY id"):
        key = (row["account"], row["agent_id"], row["proposal_id"])
        expected = running.get(key, 0) + row["delta"]
        if expected != row["balance_after"] or row["balance_after"] < 0:
            problems.append(f"entry {row['id']}: balance_after {row['balance_after']} does not follow from the previous balance")
        running[key] = row["balance_after"]
        if row["reason"] in MINT_REASONS:
            minted += row["delta"]
            if row["delta"] < 0 or row["transfer_id"] is not None:
                problems.append(f"entry {row['id']}: {row['reason']} must be a non-negative, unpaired entry")
            if row["reason"] == "operator_grant" and not (row["ref_kind"] == "operator_audit" and cx.execute(
                    "SELECT 1 FROM operator_audit WHERE id=? AND operation LIKE 'grant-credits %'", (row["ref_id"],)).fetchone()):
                problems.append(f"entry {row['id']}: grant without an operator_audit record")
        elif row["transfer_id"] is None:
            problems.append(f"entry {row['id']}: transfer row without a transfer_id")
        else:
            transfers.setdefault(row["transfer_id"], []).append(row["delta"])
    for transfer_id, deltas in transfers.items():
        if len(deltas) != 2 or sum(deltas) != 0:
            problems.append(f"transfer {transfer_id}: rows do not sum to zero")
    total = sum(running.values())
    if total != minted:
        problems.append(f"total supply {total} differs from credits created by opening entries and operator grants ({minted})")
    for agent in cx.execute("SELECT id,name,credits FROM agents ORDER BY id"):
        key = ("agent", agent["id"], None)
        if key not in running:
            problems.append(f"agent {agent['id']} ({agent['name']}): no opening entry")
        elif running[key] != agent["credits"]:
            problems.append(f"agent {agent['id']} ({agent['name']}): cached credits {agent['credits']} != ledger balance {running[key]}")
    for (kind, _, proposal_id), amount in running.items():
        if kind != "allocation":
            continue
        proposal = cx.execute("SELECT kind,status,requested_credits FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if proposal is None or proposal["kind"] != "project":
            problems.append(f"allocation for proposal {proposal_id}: not a project proposal")
        elif amount > proposal["requested_credits"]:
            problems.append(f"allocation for proposal {proposal_id}: {amount} exceeds the approved budget {proposal['requested_credits']}")
        elif amount and proposal["status"] not in ("APPROVED", "APPROVED_UNFUNDED"):
            problems.append(f"allocation for proposal {proposal_id}: {amount} credits held while status is {proposal['status']}")
    return problems


def summary(cx: sqlite3.Connection, entries: int = 20) -> dict[str, Any]:
    """Public read-only view for /api/overview: totals and the newest entries. No dollar figure exists here."""
    out: dict[str, Any] = {"unit": UNIT_NOTE, "commons_pool": 0, "allocated": 0, "held_by_agents": 0,
                           "total_supply": 0, "entry_count": 0, "entries": []}
    if not has_tables(cx):
        return out
    for kind, total in cx.execute('''SELECT l.account,COALESCE(SUM(l.balance_after),0) FROM credit_ledger l
            WHERE l.id IN (SELECT MAX(id) FROM credit_ledger GROUP BY account,agent_id,proposal_id) GROUP BY l.account'''):
        out[{"agent": "held_by_agents", "commons": "commons_pool", "allocation": "allocated"}[kind]] = int(total)
    out["total_supply"] = supply(cx)
    out["entry_count"] = int(cx.execute("SELECT COUNT(*) FROM credit_ledger").fetchone()[0])
    out["entries"] = [dict(r) for r in cx.execute('''SELECT l.id,l.account,a.name agent,l.proposal_id,l.delta,l.balance_after,
            l.reason,l.round_id round,l.demo,l.created_at FROM credit_ledger l LEFT JOIN agents a ON a.id=l.agent_id
            ORDER BY l.id DESC LIMIT ?''', (entries,))]
    return out
