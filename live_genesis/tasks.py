"""Operator task board: a short list of concrete, optional pieces of shared work.

A task is OPERATOR text. It is not a resident's action and never model output: it lives in its
own table, is created and closed only by the operator CLI (`task-add`, `task-close`; both
written to `operator_audit`), records no event, has no agent author and is counted in no agent
metric. Every appearance carries the fixed label `LABEL` ("SET BY THE OPERATOR"); the two
tasks of the scripted demo are flagged demo as well, like all demo data.

What an agent can do with a task: nothing, or attach its own new `post` or `doc_create` to it by
sending `task_id`. The post or document is an ordinary agent record (author, source label,
guards, quota); `task_responses` only links it to the task. Taking a task is optional. A task
carries no reward, bounty, credits or payment of any kind, and there is no field that could.

Rules kept here:
* Task text passes the same pure content guards and character rule as posts (payment links,
  wallet addresses, personal data, credentials, invisible characters), plus two task-only
  refusals: wording that offers a reward or payment, and links (a task is about work inside
  this world, not a way to send agents anywhere).
* At most MAX_OPEN non-demo tasks are open at once, and together they may take at most
  CONTEXT_BYTES of every agent's prompt; `add` refuses beyond either bound, so `context()` is
  never cut and the engine's byte-bounded prompt keeps room for the rest.
* A task's text is fixed once added, and closing is final (triggers below). To change or
  reopen a task, close it and add a new one. Nothing is ever deleted.
* A scripted demo action may attach only to a demo task, and a recorded action only to a
  non-demo task (the same mixing rule as posts and documents).

No HTTP route writes here. `public()` and `context()` only read.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from fastapi import HTTPException

from . import guards

VERBS = ("post", "doc_create")      # the only actions that accept `task_id`
KINDS = {"post": "post", "doc_create": "document"}
STATUSES = ("OPEN", "CLOSED")
TITLE_CHARS, BODY_CHARS, NOTE_CHARS = 100, 600, 200
MAX_OPEN = 5              # non-demo tasks open at the same time
CONTEXT_BYTES = 4200      # upper bound, in UTF-8 bytes of compact JSON, of `open_tasks` in an agent's context
PUBLIC_OPEN = 5           # open tasks in /api/overview (demo and non-demo together)
PUBLIC_CLOSED = 5         # most recently closed tasks in /api/overview
PUBLIC_RESPONSES = 8      # newest responses listed per task; `response_count` is the full number

LABEL = "SET BY THE OPERATOR"
NATURE = ("Tasks are written by the operator, not by an agent and not by a model. Taking one is optional. "
          "A task carries no reward, credits or payment.")
CONTEXT_NOTE = ("Set by the operator, not by an agent. Optional: idle and every other action stay equally valid, "
                "and nothing is given for doing one. To attach your work, add task_id to a post or doc_create.")

SCHEMA = '''
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','CLOSED')),
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)),
    created_at TEXT NOT NULL, closed_at TEXT, close_note TEXT,
    created_round INTEGER NOT NULL, closed_round INTEGER
);
CREATE TRIGGER IF NOT EXISTS tasks_no_delete BEFORE DELETE ON tasks
    BEGIN SELECT RAISE(ABORT, 'tasks cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS tasks_fixed_fields BEFORE UPDATE OF id,title,body,demo,created_at,created_round ON tasks
    BEGIN SELECT RAISE(ABORT, 'task text and origin are fixed'); END;
CREATE TRIGGER IF NOT EXISTS tasks_close_is_final BEFORE UPDATE ON tasks WHEN OLD.status = 'CLOSED'
    BEGIN SELECT RAISE(ABORT, 'a closed task cannot be changed'); END;
-- Link between a task and the agent-written post or document attached to it. Append-only.
CREATE TABLE IF NOT EXISTS task_responses (
    id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),
    kind TEXT NOT NULL CHECK(kind IN ('post','document')), ref_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)),
    round_id INTEGER NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (kind, ref_id)
);
CREATE INDEX IF NOT EXISTS task_responses_task ON task_responses(task_id, id);
CREATE TRIGGER IF NOT EXISTS task_responses_no_update BEFORE UPDATE ON task_responses
    BEGIN SELECT RAISE(ABORT, 'task_responses is append-only'); END;
CREATE TRIGGER IF NOT EXISTS task_responses_no_delete BEFORE DELETE ON task_responses
    BEGIN SELECT RAISE(ABORT, 'task_responses is append-only'); END;
'''


def migrate(cx: sqlite3.Connection) -> None:
    """Create the two tables; additive and repeatable. Nothing existing is altered."""
    cx.executescript(SCHEMA)


def _has_tables(cx: sqlite3.Connection) -> bool:
    """False on a database opened read-only before any writer process created the task tables."""
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_responses'").fetchone() is not None


# ---------------------------------------------------------------- operator text checks

_REWARD = re.compile(r"(?i)\b(?:rewards?|bount(?:y|ies)|prizes?|payouts?|paid|payments?|salary|wages?)\b"
                     r"|[$€£¥]\s?\d|\b\d[\d,.]*\s*(?:usd|eur|dollars?|euros?|credits?)\b")
_LINK = re.compile(r"(?i)\b(?:https?://|www\.)")
REWARD_REASON = "offers a reward, credits or payment (a task carries none)"
LINK_REASON = "contains a link (a task is about work inside this world)"


def text_problem(text: str) -> str | None:
    """Category of what must not be in task text, or None. Never contains the text itself.
    The pure post guards first (payment links, wallets, personal data, credentials), then the
    two task-only rules. Heuristics: they do not understand meaning."""
    folded = guards._fold(text)
    return (guards.content_reason(text) or (LINK_REASON if _LINK.search(folded) else None)
            or (REWARD_REASON if _REWARD.search(folded) else None))


def _clean(value: Any, maximum: int, label: str, *, multiline: bool = False) -> str:
    """Operator-side check of one text. ValueError names the field and a category, never the text."""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"Task {label} must be 1-{maximum} characters")
    text = value.strip().replace("\r\n", "\n") if multiline else value.strip()
    if guards.has_forbidden_character(text, multiline=multiline):
        raise ValueError(f"Task {label} rejected: {guards.CHARACTER_RULE}. Remove it and try again.")
    reason = text_problem(text) or (guards.degenerate_reason(text) if multiline else None)
    if reason:
        raise ValueError(f"Task {label} rejected: {reason}. Remove that part and try again; the matched text is not shown here.")
    return text


# ---------------------------------------------------------------- operator commands (CLI only)

def _context_entry(rows: list[Any], responses: dict[int, int] | None = None, mine: set[int] | None = None) -> dict[str, Any]:
    return {"note": CONTEXT_NOTE,
            "tasks": [{"id": r["id"], "title": r["title"], "body": r["body"],
                       "responses": (responses or {}).get(r["id"], 0), "you_responded": r["id"] in (mine or ())} for r in rows]}


def _entry_bytes(entry: dict[str, Any]) -> int:
    return len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


_COUNT_MARGIN = 8   # bytes per task kept free for a growing `responses` number and `true` instead of `false`


def add(title: str, body: str, *, demo: bool = False) -> dict[str, Any]:
    """Operator-only (CLI): add one OPEN task. Audited. Raises ValueError with a category message
    for refused text, a full board (MAX_OPEN) or a board that would take too much of the prompt.
    `demo=True` is used by the scripted demo alone; a demo task is never shown to agents."""
    from . import controls
    from .core import connect, utcnow
    title = _clean(title, TITLE_CHARS, "title")
    body = _clean(body, BODY_CHARS, "body", multiline=True)
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        if not demo:
            rows = [dict(r) for r in cx.execute("SELECT id,title,body FROM tasks WHERE status='OPEN' AND demo=0 ORDER BY id")]
            if len(rows) >= MAX_OPEN:
                raise ValueError(f"The board already holds {MAX_OPEN} open tasks, the most allowed, so the list agents read stays short. "
                                 "Close one with task-close first.")
            new_id = int(cx.execute("SELECT COALESCE(MAX(id),0)+1 FROM tasks").fetchone()[0])
            size = _entry_bytes(_context_entry(rows + [{"id": new_id, "title": title, "body": body}])) + _COUNT_MARGIN * (len(rows) + 1)
            if size > CONTEXT_BYTES:
                raise ValueError(f"The open tasks would take {size} bytes of every agent's prompt; the limit is {CONTEXT_BYTES}. "
                                 "Shorten the text or close a task first.")
        round_id = int(cx.execute("SELECT MAX(id) FROM rounds").fetchone()[0])
        stamp = utcnow()
        task_id = int(cx.execute("INSERT INTO tasks(title,body,status,demo,created_at,created_round) VALUES (?,?,'OPEN',?,?,?)",
                                 (title, body, int(demo), stamp, round_id)).lastrowid)
        controls.audit(cx, f"task-add {task_id}{' (scripted demo)' if demo else ''}: {title}")
        return {"id": task_id, "title": title, "body": body, "status": "OPEN", "demo": int(demo),
                "created_at": stamp, "created_round": round_id}


def close(task_id: int, note: str | None = None) -> dict[str, Any]:
    """Operator-only (CLI): close one OPEN task, for good. Audited. The optional note is operator
    text shown publicly, checked like the task text. ValueError for an unknown or closed task."""
    from . import controls
    from .core import connect, utcnow
    note = _clean(note, NOTE_CHARS, "close note") if note is not None and str(note).strip() else None
    with connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        row = cx.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError(f"Task {task_id} not found. List tasks with task-list --all.")
        if row["status"] != "OPEN":
            raise ValueError(f"Task {task_id} is already closed. Closing is final; add a new task instead.")
        round_id = int(cx.execute("SELECT MAX(id) FROM rounds").fetchone()[0])
        stamp = utcnow()
        cx.execute("UPDATE tasks SET status='CLOSED',closed_at=?,close_note=?,closed_round=? WHERE id=?", (stamp, note, round_id, task_id))
        controls.audit(cx, f"task-close {task_id}" + (" with a note" if note else ""))
        return {"id": task_id, "status": "CLOSED", "closed_at": stamp, "closed_round": round_id, "close_note": note,
                "responses": cx.execute("SELECT COUNT(*) FROM task_responses WHERE task_id=?", (task_id,)).fetchone()[0]}


def listing(include_closed: bool = False) -> list[dict[str, Any]]:
    """Operator view (CLI task-list): open tasks, or every task with --all, with response counts."""
    from .core import connect
    with connect() as cx:
        where = "" if include_closed else "WHERE t.status='OPEN'"
        return [dict(r) for r in cx.execute(
            f"SELECT t.*,(SELECT COUNT(*) FROM task_responses r WHERE r.task_id=t.id) responses FROM tasks t {where} ORDER BY t.id")]


# ---------------------------------------------------------------- attaching (called by core.submit_action)

def field_only_here(action: dict[str, Any], verb: str) -> None:
    """422 for `task_id` on a verb that cannot carry it. A JSON null counts as absent."""
    if action.get("task_id") is not None and verb not in VERBS:
        raise HTTPException(422, "task_id is accepted only on post and doc_create. Remove it and resend; "
                                 "a reply already belongs to the post it answers.")


def target(cx: sqlite3.Connection, action: dict[str, Any], *, demo: bool) -> int | None:
    """The task a post or doc_create asks to be attached to, validated; None without `task_id`."""
    task_id = action.get("task_id")
    if task_id is None:
        return None
    if type(task_id) is not int:
        raise HTTPException(422, "task_id must be an integer: the id of an open task from open_tasks in GET /api/agent/context. "
                                 "Leave it out to post without a task.")
    task = cx.execute("SELECT status,demo FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise HTTPException(404, "Task not found. Use a task id from open_tasks in GET /api/agent/context, "
                                 "or leave task_id out.")
    if bool(task["demo"]) != bool(demo):
        raise HTTPException(409, "Scripted demo tasks and agent actions cannot mix. Choose a task that is not labelled demo, "
                                 "or leave task_id out.")
    if task["status"] != "OPEN":
        raise HTTPException(409, f"Task #{task_id} is closed and accepts no more responses. "
                                 "Leave task_id out, or choose a task from open_tasks.")
    return task_id


def attach(cx: sqlite3.Connection, task_id: int, kind: str, ref_id: int, agent_id: int, *, demo: bool,
           round_id: int, stamp: str) -> None:
    cx.execute("INSERT INTO task_responses(task_id,kind,ref_id,agent_id,demo,round_id,created_at) VALUES (?,?,?,?,?,?,?)",
               (task_id, kind, ref_id, agent_id, int(demo), round_id, stamp))


# ---------------------------------------------------------------- reading

def open_ids(cx: sqlite3.Connection, agent_id: int, limit: int) -> list[int]:
    """Ids of open non-demo tasks for `available_actions`: those this agent has not answered first."""
    rows = cx.execute("SELECT t.id, EXISTS(SELECT 1 FROM task_responses r WHERE r.task_id=t.id AND r.agent_id=?) mine "
                      "FROM tasks t WHERE t.status='OPEN' AND t.demo=0 ORDER BY mine, t.id LIMIT ?", (agent_id, limit))
    return [r[0] for r in rows]


def context(cx: sqlite3.Connection, agent_id: int) -> dict[str, Any] | None:
    """The `open_tasks` entry of one agent's context, or None when no non-demo task is open.
    Complete by construction: `add` keeps the board within MAX_OPEN tasks and CONTEXT_BYTES."""
    rows = cx.execute("SELECT id,title,body FROM tasks WHERE status='OPEN' AND demo=0 ORDER BY id LIMIT ?", (MAX_OPEN,)).fetchall()
    if not rows:
        return None
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    counts = dict(cx.execute(f"SELECT task_id,COUNT(*) FROM task_responses WHERE task_id IN ({marks}) GROUP BY task_id", ids).fetchall())
    mine = {r[0] for r in cx.execute(f"SELECT DISTINCT task_id FROM task_responses WHERE agent_id=? AND task_id IN ({marks})", [agent_id, *ids])}
    return _context_entry(rows, counts, mine)


_RESPONSES = '''SELECT r.kind,r.ref_id id,
    CASE r.kind WHEN 'post' THEN p.title ELSE d.title END title, a.name author, r.demo,
    COALESCE(ap.source, CASE WHEN r.demo=1 THEN 'demo' ELSE 'unknown' END) source
    FROM task_responses r JOIN agents a ON a.id=r.agent_id
    LEFT JOIN posts p ON r.kind='post' AND p.id=r.ref_id
    LEFT JOIN documents d ON r.kind='document' AND d.id=r.ref_id
    LEFT JOIN document_revisions v ON r.kind='document' AND v.document_id=r.ref_id AND v.number=1
    LEFT JOIN action_provenance ap ON (r.kind='post' AND ap.kind='post' AND ap.ref_id=r.ref_id)
                                   OR (r.kind='document' AND ap.kind='doc_create' AND ap.ref_id=v.id)
    WHERE r.task_id=? ORDER BY r.id DESC LIMIT ?'''


def public(cx: sqlite3.Connection) -> list[dict[str, Any]]:
    """`tasks` of /api/overview: open tasks (oldest first, at most PUBLIC_OPEN), then the most
    recently closed (at most PUBLIC_CLOSED). Each names its responses: the post or document an
    agent attached, with that record's own author and source. `label` is the fixed operator label."""
    if not _has_tables(cx):
        return []
    rows = cx.execute("SELECT * FROM (SELECT * FROM tasks WHERE status='OPEN' ORDER BY id DESC LIMIT ?) ORDER BY id", (PUBLIC_OPEN,)).fetchall()
    rows += cx.execute("SELECT * FROM tasks WHERE status='CLOSED' ORDER BY closed_at DESC, id DESC LIMIT ?", (PUBLIC_CLOSED,)).fetchall()
    out = []
    for row in rows:
        task = {k: row[k] for k in ("id", "title", "body", "status", "demo", "created_at", "closed_at", "close_note",
                                    "created_round", "closed_round")}
        task["label"] = LABEL
        task["response_count"] = cx.execute("SELECT COUNT(*) FROM task_responses WHERE task_id=?", (row["id"],)).fetchone()[0]
        task["responses"] = [{k: r[k] for k in ("kind", "id", "title", "author", "source")}
                             for r in cx.execute(_RESPONSES, (row["id"], PUBLIC_RESPONSES))]
        out.append(task)
    return out


def annotate_posts(cx: sqlite3.Connection, posts: list[dict[str, Any]]) -> None:
    """Add `task_id` (int or None) to each overview post: the task it was attached to."""
    links: dict[int, int] = {}
    ids = [p["id"] for p in posts]
    if ids and _has_tables(cx):
        links = dict(cx.execute(f"SELECT ref_id,task_id FROM task_responses WHERE kind='post' AND ref_id IN ({','.join('?' * len(ids))})", ids).fetchall())
    for post in posts:
        post["task_id"] = links.get(post["id"])
