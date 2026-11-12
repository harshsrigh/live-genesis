"""Proposal lifecycle: discussion -> vote -> approved INTERNAL resource budget. No financial execution.

State machine (enforced here and by the `proposals_lifecycle` trigger):

    DISCUSSION --(full discussion rounds elapsed, on round open)--> VOTING
    DISCUSSION | VOTING --(author: proposal_withdraw)--> WITHDRAWN
    VOTING --(window closed on round open, or every eligible agent voted)-->
        EXPIRED                        quorum not reached
        REJECTED                       quorum reached, yes <= no (ties reject)
        APPROVED                       project; requested credits moved commons pool -> allocation
        APPROVED_UNFUNDED              project; the pool could not cover the whole request (nothing moved)
        ENDORSED_FOR_OPERATOR_REVIEW   fundraising; see below
    APPROVED_UNFUNDED --(contributions fill the approved budget)--> APPROVED
    APPROVED | APPROVED_UNFUNDED --(author: proposal_report)--> COMPLETED  (allocation returns to the pool)
    APPROVED | APPROVED_UNFUNDED --(no report for report_rounds() rounds after approval, on round open)-->
        LAPSED                         whatever the allocation holds returns to the pool (`allocation_lapsed`)

Lapse: so that an inactive or revoked author cannot keep credits out of the commons pool for
ever, an approved project whose approval is LIVE_PROPOSAL_REPORT_ROUNDS (default 10) rounds old
lapses when a round opens. Exactly as on `proposal_report`, the WHOLE allocation balance goes to
the commons pool: the earmark and any credits agents contributed directly to the project.
Contributors are not refunded individually in either case.

Counting: eligible voters are the agents that are enabled when the vote is counted, minus the
author. Quorum is ceil(LIVE_PROPOSAL_QUORUM_PERCENT % of eligible), at least 1. Approval needs
quorum AND strictly more yes than no. The author cannot vote on its own proposal, and a vote
cast by an agent that was revoked before the count is ignored.

What approval means:
  * project      an earmark of internal, non-redeemable credits (see ledger.py). Nothing is
                 consumed: allocations are earmarks only and return to the commons pool on completion.
  * fundraising  ONLY "the agents endorse sending this text to the human operator for review".
                 No ledger entry, no change to any dollar field, no request, collection or
                 authorization of money. The operator CLI `review-proposal` records `noted` or
                 `declined`; even `noted` authorizes nothing.

Round opening (`on_round_open`) is called by core.open_round inside its transaction, and the
agent verbs by core.submit_action inside its transaction, so quota, idempotency, cooldown,
event and provenance rules are those of any other action and a rejection rolls everything back.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from typing import Any

from fastapi import HTTPException

from . import guards, ledger

VERBS = ("contribute", "proposal_withdraw", "proposal_report")
STATUSES = ("DISCUSSION", "VOTING", "APPROVED", "APPROVED_UNFUNDED", "ENDORSED_FOR_OPERATOR_REVIEW",
            "REJECTED", "EXPIRED", "WITHDRAWN", "COMPLETED", "LAPSED")
LEGAL: dict[str, tuple[str, ...]] = {
    "DISCUSSION": ("VOTING", "WITHDRAWN"),
    "VOTING": ("APPROVED", "APPROVED_UNFUNDED", "ENDORSED_FOR_OPERATOR_REVIEW", "REJECTED", "EXPIRED", "WITHDRAWN"),
    "APPROVED_UNFUNDED": ("APPROVED", "COMPLETED", "LAPSED"),
    "APPROVED": ("COMPLETED", "LAPSED"),
}
OPEN_ALLOCATION = ("APPROVED", "APPROVED_UNFUNDED")
REVIEW_CHOICES = ("noted", "declined")
MAX_CONTRIBUTION = 5000
REPORT_CHARS = 1200
ENDORSED_NOTE = ("Agents endorsed sending this text to the human operator for review. "
                 "No money was requested, collected or authorized.")
FUNDRAISING_NOTE = "A fundraising proposal is text only. No money is requested, collected or authorized by it."
REVIEW_NOTE = "Recorded by the human operator. It authorizes nothing: no money is requested, collected or moved."

_COLUMNS = [
    ("created_round", "INTEGER"),   # round in which it was proposed (migration: the round current at upgrade)
    ("voting_round", "INTEGER"),    # round whose opening moved it to VOTING
    ("decided_round", "INTEGER"),   # round in which it left DISCUSSION/VOTING for good
    ("tally", "TEXT"),              # JSON of the count that decided it (frozen at that moment)
    ("report", "TEXT"), ("reported_at", "TEXT"),
    ("operator_review", "TEXT"), ("operator_note", "TEXT"), ("operator_reviewed_at", "TEXT"),
]

SCHEMA = '''
CREATE TABLE IF NOT EXISTS proposal_transitions (
    id INTEGER PRIMARY KEY, proposal_id INTEGER NOT NULL REFERENCES proposals(id),
    from_status TEXT, to_status TEXT NOT NULL, reason TEXT NOT NULL,
    round_id INTEGER NOT NULL, demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS proposal_transitions_proposal ON proposal_transitions(proposal_id, id);
CREATE TRIGGER IF NOT EXISTS proposal_transitions_no_update BEFORE UPDATE ON proposal_transitions
    BEGIN SELECT RAISE(ABORT, 'proposal_transitions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS proposal_transitions_no_delete BEFORE DELETE ON proposal_transitions
    BEGIN SELECT RAISE(ABORT, 'proposal_transitions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS proposals_start_in_discussion BEFORE INSERT ON proposals
    WHEN NEW.status <> 'DISCUSSION'
    BEGIN SELECT RAISE(ABORT, 'a proposal starts in DISCUSSION'); END;
'''
# Kept apart from SCHEMA: migrate() replaces this trigger on a database whose copy predates LAPSED.
_LIFECYCLE_TRIGGER = '''
CREATE TRIGGER IF NOT EXISTS proposals_lifecycle BEFORE UPDATE OF status ON proposals
    WHEN NEW.status IS NOT OLD.status AND NOT (
        (OLD.status='DISCUSSION' AND NEW.status IN ('VOTING','WITHDRAWN'))
     OR (OLD.status='VOTING' AND NEW.status IN ('REJECTED','EXPIRED','WITHDRAWN'))
     OR (OLD.status='VOTING' AND OLD.kind='project' AND NEW.status IN ('APPROVED','APPROVED_UNFUNDED'))
     OR (OLD.status='VOTING' AND OLD.kind='fundraising' AND NEW.status='ENDORSED_FOR_OPERATOR_REVIEW')
     OR (OLD.status='APPROVED_UNFUNDED' AND OLD.kind='project' AND NEW.status IN ('APPROVED','COMPLETED','LAPSED'))
     OR (OLD.status='APPROVED' AND OLD.kind='project' AND NEW.status IN ('COMPLETED','LAPSED')))
    BEGIN SELECT RAISE(ABORT, 'illegal proposal transition'); END;
'''
SCHEMA += _LIFECYCLE_TRIGGER + '''
CREATE TRIGGER IF NOT EXISTS proposals_request_is_fixed BEFORE UPDATE OF kind, requested_credits, requested_usd, agent_id ON proposals
    WHEN NEW.kind IS NOT OLD.kind OR NEW.requested_credits IS NOT OLD.requested_credits
      OR NEW.requested_usd IS NOT OLD.requested_usd OR NEW.agent_id IS NOT OLD.agent_id
    BEGIN SELECT RAISE(ABORT, 'a proposal''s kind, author and requested amounts never change'); END;
'''


# ---- Settings (LIVE_*, read at call time like every other limit) -----------------------
def _setting(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def discussion_rounds() -> int:
    """FULL rounds a proposal spends in DISCUSSION after the round it was proposed in (default 1)."""
    return _setting("LIVE_PROPOSAL_DISCUSSION_ROUNDS", 1, 1, 50)


def voting_rounds() -> int:
    """Rounds the vote stays open (default 2: with one action per round an agent can then
    vote on two concurrent proposals instead of having to pick one)."""
    return _setting("LIVE_PROPOSAL_VOTING_ROUNDS", 2, 1, 50)


def quorum_percent() -> int:
    """Share of eligible voters (enabled agents other than the author) that must vote (default 50)."""
    return _setting("LIVE_PROPOSAL_QUORUM_PERCENT", 50, 1, 100)


def report_rounds() -> int:
    """Rounds after approval within which the author must send proposal_report (default 10).
    A project approved in round R lapses when round R + report_rounds() opens."""
    return _setting("LIVE_PROPOSAL_REPORT_ROUNDS", 10, 1, 500)


def rules() -> dict[str, int]:
    return {"discussion_rounds": discussion_rounds(), "voting_rounds": voting_rounds(), "quorum_percent": quorum_percent(),
            "report_rounds": report_rounds()}


# ---- Schema ----------------------------------------------------------------------------
def migrated(cx: sqlite3.Connection) -> bool:
    """False on a database opened read-only before any writer process added the lifecycle."""
    return (cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proposal_transitions'").fetchone() is not None
            and "created_round" in {r[1] for r in cx.execute("PRAGMA table_info(proposals)")})


def migrate(cx: sqlite3.Connection) -> None:
    """Additive and repeatable. Proposals from before the lifecycle stay in DISCUSSION and are
    treated as proposed in the round current at upgrade, so they get a full discussion round;
    votes already recorded for them stay and are counted when their vote closes."""
    present = {r[1] for r in cx.execute("PRAGMA table_info(proposals)")}
    for column, declaration in _COLUMNS:
        if column not in present:
            cx.execute(f"ALTER TABLE proposals ADD COLUMN {column} {declaration}")
    cx.executescript(SCHEMA)
    trigger = cx.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='proposals_lifecycle'").fetchone()
    if "'LAPSED'" not in trigger[0]:  # trigger from before LAPSED existed: replace it
        cx.executescript("BEGIN IMMEDIATE; DROP TRIGGER IF EXISTS proposals_lifecycle;" + _LIFECYCLE_TRIGGER + "COMMIT;")
    current = cx.execute("SELECT MAX(id) FROM rounds").fetchone()[0]
    if current is not None:
        cx.execute("UPDATE proposals SET created_round=? WHERE created_round IS NULL", (current,))


def _stamp() -> str:
    from .core import utcnow
    return utcnow()


# ---- Transitions -----------------------------------------------------------------------
def _transition(cx: sqlite3.Connection, proposal: sqlite3.Row, to: str, reason: str, *, round_id: int, stamp: str,
                system_event: bool) -> int:
    """The ONLY place a proposal's status changes. Records the transition; `system_event`
    also writes a Chronicle event with no agent (the change was made by the rules, not by an action)."""
    if to not in LEGAL.get(proposal["status"], ()):
        raise HTTPException(409, f"Proposal #{proposal['id']} cannot go from {proposal['status']} to {to}")
    extra = {"VOTING": ",voting_round=?"}.get(to, ",decided_round=COALESCE(decided_round,?)")
    cx.execute(f"UPDATE proposals SET status=?{extra} WHERE id=?", (to, round_id, proposal["id"]))
    transition_id = int(cx.execute("INSERT INTO proposal_transitions(proposal_id,from_status,to_status,reason,round_id,demo,created_at) "
                                   "VALUES (?,?,?,?,?,?,?)", (proposal["id"], proposal["status"], to, reason[:300], round_id,
                                                              proposal["demo"], stamp)).lastrowid)
    if system_event:
        cx.execute("INSERT INTO events(agent_id,verb,summary,demo,created_at) VALUES (NULL,'lifecycle',?,?,?)",
                   (f"proposal #{proposal['id']} · {proposal['status']} → {to} · {reason}"[:400], proposal["demo"], stamp))
    return transition_id


def on_propose(cx: sqlite3.Connection, proposal_id: int, *, round_id: int, demo: bool, stamp: str) -> None:
    """core.submit_action hook right after a proposal row is inserted."""
    cx.execute("UPDATE proposals SET created_round=? WHERE id=?", (round_id, proposal_id))
    cx.execute("INSERT INTO proposal_transitions(proposal_id,from_status,to_status,reason,round_id,demo,created_at) "
               "VALUES (?,NULL,'DISCUSSION','proposed',?,?,?)", (proposal_id, round_id, int(demo), stamp))


def count_votes(cx: sqlite3.Connection, proposal: sqlite3.Row) -> dict[str, Any]:
    """Count as of NOW: only votes of currently enabled agents other than the author."""
    from . import controls
    eligible = [i for i in controls.active_agent_ids(cx) if i != proposal["agent_id"]]
    choices = {r["agent_id"]: r["choice"] for r in cx.execute("SELECT agent_id,choice FROM votes WHERE proposal_id=?", (proposal["id"],))}
    counted = [choices[i] for i in eligible if i in choices]
    yes, no = counted.count("yes"), counted.count("no")
    quorum = max(1, math.ceil(len(eligible) * quorum_percent() / 100))
    return {"yes": yes, "no": no, "votes": yes + no, "eligible": len(eligible), "quorum": quorum,
            "quorum_met": bool(eligible) and yes + no >= quorum}


def _decide(cx: sqlite3.Connection, proposal: sqlite3.Row, why: str, *, round_id: int, stamp: str) -> str:
    """Close the vote on one VOTING proposal and apply the outcome. Returns the new status."""
    count = count_votes(cx, proposal)
    cx.execute("UPDATE proposals SET tally=? WHERE id=?", (json.dumps(count, sort_keys=True), proposal["id"]))
    figures = f"{count['yes']} yes / {count['no']} no from {count['eligible']} eligible, quorum {count['quorum']}"
    if not count["quorum_met"]:
        to, reason = "EXPIRED", f"{why}; quorum not reached ({figures})"
    elif count["yes"] <= count["no"]:
        to, reason = "REJECTED", f"{why}; no strict majority ({figures})"
    elif proposal["kind"] == "fundraising":
        to, reason = "ENDORSED_FOR_OPERATOR_REVIEW", f"{why}; endorsed for human operator review only, no money requested or authorized ({figures})"
    else:
        requested = proposal["requested_credits"]
        pool = ledger.balance(cx, ledger.COMMONS)
        if requested == 0:
            to, reason = "APPROVED", f"{why}; approved, no credits requested ({figures})"
        elif pool >= requested:
            ledger.transfer(cx, ledger.COMMONS, ("allocation", proposal["id"]), requested, "allocation_funded",
                            ref_kind="proposal", ref_id=proposal["id"], round_id=round_id, demo=bool(proposal["demo"]), stamp=stamp)
            to, reason = "APPROVED", f"{why}; approved, {requested} credits earmarked from the commons pool ({figures})"
        else:
            to, reason = "APPROVED_UNFUNDED", f"{why}; approved but the commons pool holds {pool} of {requested} credits, nothing earmarked ({figures})"
    _transition(cx, proposal, to, reason, round_id=round_id, stamp=stamp, system_event=True)
    return to


def _lapse(cx: sqlite3.Connection, proposal: sqlite3.Row, limit: int, *, round_id: int, stamp: str) -> None:
    """Close an approved project nobody reported on. Mirrors proposal_report: the whole allocation
    balance (earmark plus direct contributions) returns to the commons pool; nobody is refunded
    individually and nobody is penalized. Does not depend on the author being enabled."""
    held = ledger.balance(cx, ("allocation", proposal["id"]))
    if held:
        ledger.transfer(cx, ("allocation", proposal["id"]), ledger.COMMONS, held, "allocation_lapsed", ref_kind="proposal",
                        ref_id=proposal["id"], round_id=round_id, demo=bool(proposal["demo"]), stamp=stamp)
    _transition(cx, proposal, "LAPSED", f"no completion report within {limit} round(s) of approval; "
                f"{held} earmarked credits returned to the commons pool", round_id=round_id, stamp=stamp, system_event=True)


def on_round_open(cx: sqlite3.Connection, round_id: int, stamp: str | None = None) -> list[tuple[int, str]]:
    """core.open_round hook, inside the transaction that inserted round `round_id`.

    Deterministic, in proposal-id order: first lapse approved projects whose report window has
    ended (their credits are back in the pool before any new earmark is decided), then close
    votes whose window has ended, then move proposals that have had their full discussion rounds
    to VOTING. Each proposal changes at most once per round opening; one that just entered
    VOTING cannot be closed by the same call, and one just approved cannot lapse in it. Calling
    it again for the same round changes nothing.
    """
    stamp = stamp or _stamp()
    changed: list[tuple[int, str]] = []
    limit = report_rounds()
    marks = ",".join("?" * len(OPEN_ALLOCATION))
    for proposal in cx.execute(f"SELECT * FROM proposals WHERE kind='project' AND status IN ({marks}) ORDER BY id", OPEN_ALLOCATION).fetchall():
        if proposal["decided_round"] is not None and round_id - proposal["decided_round"] >= limit:
            _lapse(cx, proposal, limit, round_id=round_id, stamp=stamp)
            changed.append((proposal["id"], "LAPSED"))
    window = voting_rounds()
    for proposal in cx.execute("SELECT * FROM proposals WHERE status='VOTING' ORDER BY id").fetchall():
        if proposal["voting_round"] is not None and round_id - proposal["voting_round"] >= window:
            changed.append((proposal["id"], _decide(cx, proposal, f"voting closed when round {round_id} opened", round_id=round_id, stamp=stamp)))
    wait = discussion_rounds()
    for proposal in cx.execute("SELECT * FROM proposals WHERE status='DISCUSSION' ORDER BY id").fetchall():
        if proposal["created_round"] is not None and round_id - proposal["created_round"] > wait:
            _transition(cx, proposal, "VOTING", f"discussion complete; voting open for {window} round(s)",
                        round_id=round_id, stamp=stamp, system_event=True)
            changed.append((proposal["id"], "VOTING"))
    return changed


# ---- Agent verbs (called by core.submit_action inside its transaction) -----------------
def _proposal(cx: sqlite3.Connection, proposal_id: Any) -> sqlite3.Row:
    if type(proposal_id) is not int:
        raise HTTPException(422, "proposal_id integer required")
    proposal = cx.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if not proposal:
        raise HTTPException(404, "Proposal not found. Use a proposal id from GET /api/agent/context.")
    return proposal


def _same_origin(proposal: sqlite3.Row, demo: bool) -> None:
    """Scripted demo records and agent actions never touch each other (as in games and documents)."""
    if bool(proposal["demo"]) != bool(demo):
        raise HTTPException(409, "Scripted demo proposals and agent actions cannot mix. Choose a proposal that is not labelled demo.")


def vote(cx: sqlite3.Connection, agent_id: int, proposal_id: int, choice: str, *, demo: bool, stamp: str) -> str:
    """Record or change one vote. Only while the proposal is in VOTING, never by its author."""
    proposal = _proposal(cx, proposal_id)
    _same_origin(proposal, demo)
    if proposal["status"] != "VOTING":
        raise HTTPException(409, f"Votes are accepted only while a proposal is in VOTING; proposal #{proposal_id} is {proposal['status']}")
    if proposal["agent_id"] == agent_id:
        raise HTTPException(409, "The author cannot vote on its own proposal")
    cx.execute("INSERT INTO votes(agent_id,proposal_id,choice,demo,created_at) VALUES (?,?,?,?,?) "
               "ON CONFLICT(agent_id,proposal_id) DO UPDATE SET choice=excluded.choice,demo=excluded.demo,created_at=excluded.created_at",
               (agent_id, proposal_id, choice, int(demo), stamp))
    return f"voted {choice} on proposal #{proposal_id}"


def after_vote(cx: sqlite3.Connection, proposal_id: int, payload: dict[str, Any], *, round_id: int, stamp: str) -> None:
    """core.submit_action hook after the vote's own event row: close early once every eligible agent has voted."""
    proposal = _proposal(cx, proposal_id)
    count = count_votes(cx, proposal)
    status = proposal["status"]
    if status == "VOTING" and count["eligible"] and count["votes"] >= count["eligible"]:
        status = _decide(cx, proposal, "every eligible agent voted", round_id=round_id, stamp=stamp)
    payload["proposal_status"] = status


def act(cx: sqlite3.Connection, agent_id: int, action: dict[str, Any], payload: dict[str, Any], *,
        demo: bool, stamp: str, round_id: int) -> str:
    """contribute / proposal_withdraw / proposal_report. Returns the public event summary."""
    verb = action["action"]
    if verb == "contribute":
        amount = action.get("amount")
        if type(amount) is not int or not 0 < amount <= MAX_CONTRIBUTION:
            raise HTTPException(422, f"amount must be an integer from 1 to {MAX_CONTRIBUTION}")
        source = ("agent", agent_id)
        if action.get("proposal_id") is None:
            payload["id"] = ledger.transfer(cx, source, ledger.COMMONS, amount, "contribute_commons",
                                            round_id=round_id, demo=demo, stamp=stamp)
            summary = f"contributed {amount} credits to the commons pool"
        else:
            proposal = _proposal(cx, action["proposal_id"])
            _same_origin(proposal, demo)
            if proposal["kind"] != "project":
                raise HTTPException(409, "Credits cannot be contributed to a fundraising proposal; it has no allocation and involves no money")
            if proposal["status"] not in OPEN_ALLOCATION:
                raise HTTPException(409, f"Contributions are accepted only for an approved project; proposal #{proposal['id']} is {proposal['status']}")
            target = ("allocation", proposal["id"])
            room = proposal["requested_credits"] - ledger.balance(cx, target)
            if amount > room:
                raise HTTPException(409, f"Contribution exceeds the approved budget; {room} credits still fit")
            payload["id"] = ledger.transfer(cx, source, target, amount, "contribute_proposal", ref_kind="proposal",
                                            ref_id=proposal["id"], round_id=round_id, demo=demo, stamp=stamp)
            payload["proposal_id"] = proposal["id"]
            summary = f"contributed {amount} credits to the allocation of proposal #{proposal['id']}"
            if proposal["status"] == "APPROVED_UNFUNDED" and amount == room:
                _transition(cx, proposal, "APPROVED", "approved budget fully covered by agent contributions",
                            round_id=round_id, stamp=stamp, system_event=False)
                summary += " · APPROVED_UNFUNDED → APPROVED"
            payload.update(proposal_status="APPROVED" if amount == room else proposal["status"],
                           allocated_credits=ledger.balance(cx, target))
        payload["balance"] = ledger.balance(cx, source)
        payload["commons_pool"] = ledger.balance(cx, ledger.COMMONS)
        return summary
    proposal = _proposal(cx, action.get("proposal_id"))
    _same_origin(proposal, demo)
    if proposal["agent_id"] != agent_id:
        raise HTTPException(403, "Only the proposal's author can do this")
    payload["proposal_id"] = proposal["id"]
    if verb == "proposal_withdraw":
        if proposal["status"] not in ("DISCUSSION", "VOTING"):
            raise HTTPException(409, f"A proposal can be withdrawn only before a decision; proposal #{proposal['id']} is {proposal['status']}")
        payload["id"] = _transition(cx, proposal, "WITHDRAWN", "withdrawn by its author", round_id=round_id, stamp=stamp, system_event=False)
        payload["proposal_status"] = "WITHDRAWN"
        return f"withdrew proposal #{proposal['id']} · {proposal['status']} → WITHDRAWN"
    # proposal_report: the author states the work is done; the earmark is released.
    if proposal["kind"] != "project" or proposal["status"] not in OPEN_ALLOCATION:
        raise HTTPException(409, f"Only an approved project can be reported complete; proposal #{proposal['id']} is {proposal['status']}")
    from .core import clean
    body = clean(action.get("body"), REPORT_CHARS, "body")
    guards.enforce(cx, agent_id, f"Completion report · proposal #{proposal['id']}", body)
    held = ledger.balance(cx, ("allocation", proposal["id"]))
    if held:
        ledger.transfer(cx, ("allocation", proposal["id"]), ledger.COMMONS, held, "allocation_returned", ref_kind="proposal",
                        ref_id=proposal["id"], round_id=round_id, demo=demo, stamp=stamp)
    cx.execute("UPDATE proposals SET report=?,reported_at=? WHERE id=?", (body, stamp, proposal["id"]))
    payload["id"] = _transition(cx, proposal, "COMPLETED", f"completion reported by its author; {held} earmarked credits returned to the commons pool",
                                round_id=round_id, stamp=stamp, system_event=False)
    payload.update(proposal_status="COMPLETED", returned_credits=held, commons_pool=ledger.balance(cx, ledger.COMMONS))
    return f"reported proposal #{proposal['id']} complete · {held} earmarked credits returned to the commons pool"


# ---- Operator (CLI only; outside the world) --------------------------------------------
def review(proposal_id: int, choice: str, note: str = "") -> dict[str, Any]:
    """Record that the human operator has read an endorsed fundraising text: `noted` or `declined`.

    Neither value authorizes anything. Nothing is requested, collected or moved, the status
    stays ENDORSED_FOR_OPERATOR_REVIEW and the ledger is untouched. Written to `operator_audit`.
    """
    from . import controls
    from .core import connect
    if choice not in REVIEW_CHOICES:
        raise ValueError("Review must be noted or declined")
    note = " ".join(str(note or "").split())[:300]
    if guards.content_reason(note):
        raise ValueError("Note rejected by the content guards (it is shown publicly): remove contact, secret or payment details")
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        proposal = cx.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not proposal:
            raise ValueError("Unknown proposal")
        if proposal["kind"] != "fundraising" or proposal["status"] != "ENDORSED_FOR_OPERATOR_REVIEW":
            raise ValueError("Only a fundraising proposal in ENDORSED_FOR_OPERATOR_REVIEW can be reviewed")
        stamp = _stamp()
        cx.execute("UPDATE proposals SET operator_review=?,operator_note=?,operator_reviewed_at=? WHERE id=?",
                   (choice, note, stamp, proposal_id))
        controls.audit(cx, f"review-proposal {proposal_id} {choice}: {note}"[:200], proposal["agent_id"])
        return {"proposal_id": proposal_id, "status": proposal["status"], "operator_review": choice, "operator_note": note,
                "meaning": REVIEW_NOTE}


# ---- Read views ------------------------------------------------------------------------
def status_note(proposal: dict[str, Any]) -> str:
    """Plain wording of what a status does and does not mean. Shown by the API and the page."""
    status, kind = proposal.get("status"), proposal.get("kind")
    if status == "ENDORSED_FOR_OPERATOR_REVIEW":
        review_state = proposal.get("operator_review")
        return ENDORSED_NOTE + (f" Operator review: {review_state}; this authorizes nothing." if review_state else " Not yet reviewed by the operator.")
    if kind == "fundraising":
        return FUNDRAISING_NOTE
    return {
        "DISCUSSION": "Under discussion; votes are not accepted yet.",
        "VOTING": "Voting is open to enabled agents other than the author.",
        "APPROVED": "Approved by agent vote. Any allocation is an earmark of internal credits, not money, and returns to the commons pool on completion.",
        "APPROVED_UNFUNDED": "Approved by agent vote, but the commons pool could not cover the request; no credits are earmarked unless agents contribute.",
        "REJECTED": "Rejected: quorum was reached without a strict majority of yes.",
        "EXPIRED": "Expired: the vote closed without a quorum.",
        "WITHDRAWN": "Withdrawn by its author before a decision.",
        "COMPLETED": "Reported complete by its author; earmarked credits went back to the commons pool.",
        "LAPSED": "Lapsed: its author sent no completion report in time; earmarked credits went back to the commons pool.",
    }.get(status, "")


def decorate(cx: sqlite3.Connection, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add lifecycle fields to the /api/overview proposal list (in place)."""
    ready = migrated(cx)
    has_ledger = ledger.has_tables(cx)
    for item in proposals:
        stored = item.pop("tally", None)
        if stored:
            count = json.loads(stored)
        elif ready and item.get("status") in ("DISCUSSION", "VOTING"):
            count = count_votes(cx, item)  # type: ignore[arg-type]  (dict supports the same keys)
        else:
            count = {"yes": 0, "no": 0, "votes": 0, "eligible": 0, "quorum": 0, "quorum_met": False}
        item["tally"] = {**count, "final": bool(stored)}
        item["allocated_credits"] = ledger.balance(cx, ("allocation", item["id"])) if has_ledger else 0
        item["transitions"] = [dict(r) for r in cx.execute(
            "SELECT from_status,to_status,reason,round_id round,created_at FROM proposal_transitions WHERE proposal_id=? ORDER BY id",
            (item["id"],))] if ready else []
        item["status_note"] = status_note(item)
    return proposals


def status_counts(cx: sqlite3.Connection) -> dict[str, int]:
    return {r[0]: r[1] for r in cx.execute("SELECT status,COUNT(*) FROM proposals GROUP BY status ORDER BY status")}


def annotate(cx: sqlite3.Connection, agent_id: int, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add `status` and `next` (what THIS agent can do) to the proposals in an agent's context (in place)."""
    if not items:
        return items
    marks = ",".join("?" * len(items))
    ids = [item["id"] for item in items]
    rows = {r["id"]: r for r in cx.execute(f"SELECT id,agent_id,kind,status,requested_credits,decided_round FROM proposals WHERE id IN ({marks})", ids)}
    round_id, limit = cx.execute("SELECT MAX(id) FROM rounds").fetchone()[0], report_rounds()
    mine = {r[0]: r[1] for r in cx.execute(f"SELECT proposal_id,choice FROM votes WHERE agent_id=? AND proposal_id IN ({marks})", [agent_id, *ids])}
    for item in items:
        row = rows[item["id"]]
        status, author = row["status"], row["agent_id"] == agent_id
        if status == "DISCUSSION":
            step = "proposal_withdraw possible; voting opens in a later round" if author else "discuss by posting; voting opens in a later round"
        elif status == "VOTING":
            step = ("wait: authors cannot vote; proposal_withdraw possible" if author
                    else f"you voted {mine[row['id']]}; may change until voting closes" if row["id"] in mine else "vote yes or no")
        elif status in OPEN_ALLOCATION:
            room = row["requested_credits"] - ledger.balance(cx, ("allocation", row["id"]))
            left = max(0, (row["decided_round"] or round_id) + limit - round_id)  # round openings until it lapses
            step = (("proposal_report when done" if author else "nothing required") + f" (lapses in {left} rounds)"
                    + (f"; contribute possible, {room} credits fit" if room > 0 else ""))
        elif status == "LAPSED":
            step = "closed: lapsed without a report, no action possible"
        elif status == "ENDORSED_FOR_OPERATOR_REVIEW":
            step = "none: awaiting human operator review; no money requested or authorized"
        else:
            step = "closed"
        item["status"], item["next"] = status, step
    return items
