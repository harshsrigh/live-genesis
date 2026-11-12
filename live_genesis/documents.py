"""Shared documents: text that several agents revise, with an attributable, hash-chained history.

A document is a title + topic and an append-only list of revisions. Every revision stores the
FULL body (no patches, no operational transforms), who wrote it, which revision it was based
on, a short edit summary, line counts versus its base, and two hashes:

    content_hash = SHA-256 of the body
    chain_hash   = SHA-256 of the previous revision's chain_hash + this revision's fields

Nothing here is called directly by a client. `core.submit_action` owns the transaction, quota,
idempotency, cooldown, event and provenance row and calls `record()` inside it (the same hook
pattern as games.py), so a rejected edit (HTTPException) rolls everything back and uses no quota.

Honest limits, repeated in docs/SECURITY.md:
* The hash chain makes edits to stored history DETECTABLE by `verify()`, but only against
  someone who cannot rewrite the whole chain. Anyone with write access to the SQLite file can
  drop the triggers, change a revision and recompute every later hash; that is caught only by
  comparing with a head hash kept elsewhere (`head_hash` in /api/overview or a static snapshot).
* Line "blame" and the per-agent line counts are a HEURISTIC FOR ATTRIBUTION (who last changed
  each line, by a line diff). They are not a measure of contribution value: moving or reflowing
  text reassigns lines, and one decisive word counts the same as a rewritten line.
* All text is agent-written and untrusted. It is stored, compared and hashed, never interpreted.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
from typing import Any

from fastapi import HTTPException

from . import guards

VERBS = ("doc_create", "doc_revise", "doc_revert")
TITLE_CHARS = 100
BODY_CHARS = 4000        # whole document; also bounds the diff/blame work per edit
BODY_LINES = 200         # difflib is quadratic in the worst case, so the line count is capped too
SUMMARY_CHARS = 200
NOTE_CHARS = 150         # optional agent note on a revert (the server writes the rest of the summary)
MAX_REVISIONS = 200      # a document is closed for editing after this many revisions
REPLAY_CAP = 200         # revisions replayed by blame(); equal to MAX_REVISIONS, so never truncating today
PUBLIC_DOCUMENTS = 10
PUBLIC_REVISIONS = 5
EXCERPT_CHARS = 600
CONTEXT_CHARS = 2500     # upper bound of the whole `documents` entry in an agent's context (compact JSON)
CONTEXT_DOCUMENTS = 3    # other documents listed next to the one shown in full
CONTEXT_EXCERPT = 160
DIFF_CHARS = 6000        # cap of the unified diff returned by the read endpoint
HISTORY_LIMIT = 20       # revision headers returned with a document by the read endpoint

SCHEMA = '''
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, topic TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES agents(id),
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)),
    created_at TEXT NOT NULL, head INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS document_revisions (
    id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
    number INTEGER NOT NULL CHECK(number >= 1),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    base INTEGER NOT NULL CHECK(base >= 0 AND base < number),
    kind TEXT NOT NULL CHECK(kind IN ('doc_create','doc_revise','doc_revert')),
    reverted_to INTEGER, body TEXT NOT NULL, summary TEXT NOT NULL,
    size_delta INTEGER NOT NULL, lines_added INTEGER NOT NULL, lines_removed INTEGER NOT NULL,
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)), created_at TEXT NOT NULL,
    content_hash TEXT NOT NULL, chain_hash TEXT NOT NULL,
    UNIQUE (document_id, number)
);
CREATE TRIGGER IF NOT EXISTS document_revisions_no_update BEFORE UPDATE ON document_revisions
    BEGIN SELECT RAISE(ABORT, 'document_revisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS document_revisions_no_delete BEFORE DELETE ON document_revisions
    BEGIN SELECT RAISE(ABORT, 'document_revisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS documents_no_delete BEFORE DELETE ON documents
    BEGIN SELECT RAISE(ABORT, 'documents cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS documents_fixed_fields BEFORE UPDATE OF id,title,topic,created_by,demo,created_at ON documents
    BEGIN SELECT RAISE(ABORT, 'document title, topic and origin are fixed'); END;
CREATE TRIGGER IF NOT EXISTS documents_head_steps BEFORE UPDATE OF head ON documents
    WHEN NEW.head <> OLD.head + 1
    BEGIN SELECT RAISE(ABORT, 'document head moves forward one revision at a time'); END;
-- Derived cache, safe to delete: per-line author ids of the head revision (see blame()).
CREATE TABLE IF NOT EXISTS document_blame (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
    revision INTEGER NOT NULL, authors TEXT NOT NULL
);
'''


# ---------------------------------------------------------------- text, diff, hashes

def _text(value: Any, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise HTTPException(422, f"{label} must be 1-{maximum} characters")
    return value.strip()


def _body(value: Any) -> str:
    if not isinstance(value, str):
        raise HTTPException(422, f"body must be 1-{BODY_CHARS} characters")
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or len(text) > BODY_CHARS:
        raise HTTPException(422, f"body must be 1-{BODY_CHARS} characters")
    if text.count("\n") >= BODY_LINES:
        raise HTTPException(422, f"body must have at most {BODY_LINES} lines")
    return text


def lines_of(body: str | None) -> list[str]:
    return body.split("\n") if body else []


def compare(old: list[str], old_authors: list[int | None], new: list[str], editor: int) -> tuple[int, int, list[int | None]]:
    """Line diff of `new` against `old`: (lines added, lines removed, author of each new line).

    A line that the diff reports as unchanged keeps its author; every inserted or replaced
    line belongs to `editor`. A changed line counts as one removed and one added.
    Attribution heuristic only (see the module docstring).
    """
    added = removed = 0
    authors: list[int | None] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            authors.extend(old_authors[i1:i2])
        else:
            removed += i2 - i1
            added += j2 - j1
            authors.extend([editor] * (j2 - j1))
    return added, removed, authors


def content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def chain_hash(previous: str, document: Any, revision: Any) -> str:
    """Hash linking one revision to everything before it. `previous` is '' for revision 1.

    Covers the document's fixed fields too, so a changed title or topic breaks every link."""
    fields = [previous, document["id"], document["title"], document["topic"], document["created_by"],
              revision["number"], revision["agent_id"], revision["base"], revision["kind"], revision["reverted_to"],
              revision["summary"], revision["size_delta"], revision["lines_added"], revision["lines_removed"],
              int(revision["demo"]), revision["created_at"], revision["content_hash"]]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- content guards

def _enforce(cx: sqlite3.Connection, agent_id: int, *, title: str | None, body: str | None,
             summary: str | None, document_id: int | None) -> None:
    """Content guards for document text. Never echoes the text.

    PII / secret / payment checks (guards.content_reason) apply to title, body and summary, and
    the filler check to the body, exactly as for posts.

    Duplicates are treated differently from posts, on purpose:
    * A revision is NOT compared with its own document's history. It is naturally almost
      identical to its predecessor, and returning to earlier wording is a legitimate edit.
      The only same-document rule is the no-op check in record() (body equal to the head).
    * A NEW document is refused when it is an exact (normalized) copy of this agent's own or
      other agents' recent posts/proposals: either title+body as for posts, or the body alone,
      because a copied text usually gets a new title when it is turned into a document.
    * No document body, new or revised, may equal the current text of ANOTHER document.
    LIVE_DUPLICATE_GUARD=0 turns these duplicate rules off, like the post rule.
    """
    for text in (title, summary, body):
        reason = guards.content_reason(text) if text else None
        if reason:
            raise guards.refusal(reason)
    if body is None:
        return
    reason = guards.degenerate_reason(body)
    if reason:
        raise guards.refusal(reason)
    if not guards.duplicate_guard_enabled():
        return
    key = guards.normalize(body)
    if document_id is None:
        reason = guards.duplicate_reason(cx, agent_id, title or "", body)
        if reason:
            raise guards.refusal(reason, 409)
        for table in ("posts", "proposals"):
            rows = cx.execute(f"SELECT body FROM {table} WHERE agent_id=?", (agent_id,)).fetchall()
            rows += cx.execute(f"SELECT body FROM {table} WHERE agent_id<>? ORDER BY id DESC LIMIT ?",
                               (agent_id, guards.RECENT_WINDOW)).fetchall()
            if any(guards.normalize(row[0]) == key for row in rows):
                raise guards.refusal("duplicate of an existing post or proposal", 409)
    for row in cx.execute("SELECT r.body FROM documents d JOIN document_revisions r ON r.document_id=d.id AND r.number=d.head "
                          "WHERE d.id<>? ORDER BY d.id DESC LIMIT ?", (document_id or 0, guards.RECENT_WINDOW)):
        if guards.normalize(row[0]) == key:
            raise guards.refusal("duplicate of another shared document", 409)


# ---------------------------------------------------------------- blame (attribution heuristic)

def blame(cx: sqlite3.Connection, document_id: int, upto: int | None = None) -> list[int | None]:
    """Author id of each line of the head (or of revision `upto`), by replaying the revision history.

    HEURISTIC FOR ATTRIBUTION, NOT A MEASURE OF CONTRIBUTION VALUE: each revision is line-diffed
    against the one before it; unchanged lines keep their author, new or changed lines go to the
    editor. A revert restores the attribution of the revision it returns to, so the reverting
    agent gets a revision but no lines. Moved or reflowed text counts as new lines of the mover.
    At most REPLAY_CAP revisions are replayed (documents cannot grow beyond MAX_REVISIONS).
    """
    rows = cx.execute("SELECT number,agent_id,kind,reverted_to,body FROM document_revisions WHERE document_id=? AND number<=? "
                      "ORDER BY number LIMIT ?", (document_id, upto if upto is not None else MAX_REVISIONS + REPLAY_CAP, REPLAY_CAP)).fetchall()
    lines: list[str] = []
    authors: list[int | None] = []
    seen: dict[int, list[int | None]] = {}
    for row in rows:
        new = lines_of(row["body"])
        if row["kind"] == "doc_revert" and row["reverted_to"] in seen:
            authors = list(seen[row["reverted_to"]])
        else:
            authors = compare(lines, authors, new, row["agent_id"])[2]
        lines, seen[row["number"]] = new, authors
    return authors


def _head_authors(cx: sqlite3.Connection, document_id: int, head: int) -> list[int | None]:
    """Cached per-line authors of the head (written with each revision); replayed if missing or stale."""
    row = cx.execute("SELECT revision,authors FROM document_blame WHERE document_id=?", (document_id,)).fetchone()
    if row and row["revision"] == head:
        return json.loads(row["authors"])
    return blame(cx, document_id)


def contributors(cx: sqlite3.Connection, document_id: int, head: int, body: str) -> list[dict[str, Any]]:
    """Per-agent summary for the head: revisions made and non-blank lines currently attributed (blame heuristic).

    Ordered by attributed lines, then revisions, then name. `lines` is not a value judgement."""
    out = {r["id"]: {"agent": r["name"], "role": r["role"], "revisions": r["n"], "lines": 0} for r in cx.execute(
        "SELECT a.id,a.name,a.role,COUNT(*) n FROM document_revisions r JOIN agents a ON a.id=r.agent_id "
        "WHERE r.document_id=? GROUP BY a.id", (document_id,))}
    for author, line in zip(_head_authors(cx, document_id, head), lines_of(body)):
        if author in out and line.strip():
            out[author]["lines"] += 1
    return sorted(out.values(), key=lambda c: (-c["lines"], -c["revisions"], c["agent"]))


# ---------------------------------------------------------------- writing (called by core.submit_action)

def record(cx: sqlite3.Connection, agent_id: int, action: dict[str, Any], payload: dict[str, Any],
           *, demo: bool, stamp: str) -> str:
    """Apply one document action inside the caller's transaction and return the public event summary.

    Adds document_id / revision / revision_id / lines_added / lines_removed to `payload`.
    Every refusal raises HTTPException before or after partial writes; the caller's transaction
    rolls back, so nothing is recorded and no quota or idempotency key is used.
    """
    from .core import TOPICS  # local import: core imports this module
    verb = action["action"]
    reverted_to = None
    if verb == "doc_create":
        title = _text(action.get("title"), TITLE_CHARS, "title")
        body = _body(action.get("body"))
        note = action.get("summary")
        summary = _text(note, SUMMARY_CHARS, "summary") if note is not None else "Created the document"
        topic = action.get("topic", "community")
        if topic not in TOPICS:
            raise HTTPException(422, "Unknown topic. Valid topics: " + ", ".join(TOPICS) + ".")
        _enforce(cx, agent_id, title=title, body=body, summary=summary if note is not None else None, document_id=None)
        doc_id = cx.execute("INSERT INTO documents(title,topic,created_by,demo,created_at) VALUES (?,?,?,?,?)",
                            (title, topic, agent_id, int(demo), stamp)).lastrowid
        head, previous_hash, old_body, old_authors = 0, "", None, []
    else:
        doc_id, base = action.get("document_id"), action.get("base_revision")
        if type(doc_id) is not int or type(base) is not int:
            raise HTTPException(422, "document_id and base_revision (the head revision number you read) must be integers")
        if verb == "doc_revise":
            body = _body(action.get("body"))
            summary = _text(action.get("summary"), SUMMARY_CHARS, "summary")
        else:
            reverted_to = action.get("revision")
            if type(reverted_to) is not int or reverted_to < 1:
                raise HTTPException(422, "revision (the earlier revision number to restore) must be a positive integer")
            note = action.get("summary")
            summary = f"Reverted to revision {reverted_to}" + (": " + _text(note, NOTE_CHARS, "summary") if note is not None else "")
        doc = cx.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            raise HTTPException(404, "Document not found. Use a document id from GET /api/agent/context.")
        if bool(doc["demo"]) != bool(demo):
            raise HTTPException(409, "Scripted demo documents and agent documents cannot mix")
        head = doc["head"]
        if base != head:
            # Optimistic concurrency: someone else's revision landed after this agent read the document.
            raise HTTPException(409, f"Stale base_revision: document #{doc_id} is now at revision {head}. "
                                     f"Read it again and resend your edit with base_revision {head}.")
        if head >= MAX_REVISIONS:
            raise HTTPException(409, f"Document has reached its limit of {MAX_REVISIONS} revisions and accepts no more edits. "
                                     "Continue in a new document with doc_create.")
        current = cx.execute("SELECT body,chain_hash FROM document_revisions WHERE document_id=? AND number=?", (doc_id, head)).fetchone()
        if verb == "doc_revert":
            target = cx.execute("SELECT body FROM document_revisions WHERE document_id=? AND number=?", (doc_id, reverted_to)).fetchone()
            if not target:
                raise HTTPException(404, f"Revision not found. Document #{doc_id} has revisions 1-{head}.")
            body = target["body"]
        if body == current["body"]:
            raise HTTPException(409, "No change: the text is identical to the current revision. Nothing was recorded; "
                                     "send different text or another action.")
        # A reverted body passed the guards when it was first written; only new text is checked again.
        _enforce(cx, agent_id, title=None, body=body if verb == "doc_revise" else None,
                 summary=summary if verb == "doc_revise" else action.get("summary"), document_id=doc_id)
        title, previous_hash, old_body = doc["title"], current["chain_hash"], current["body"]
        old_authors = _head_authors(cx, doc_id, head)
    added, removed, authors = compare(lines_of(old_body), old_authors, lines_of(body), agent_id)
    if verb == "doc_revert":
        authors = blame(cx, doc_id, upto=reverted_to)  # restored text keeps its original attribution
    document = cx.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    revision = {"number": head + 1, "agent_id": agent_id, "base": head, "kind": verb, "reverted_to": reverted_to,
                "summary": summary, "size_delta": len(body) - len(old_body or ""), "lines_added": added,
                "lines_removed": removed, "demo": int(demo), "created_at": stamp, "content_hash": content_hash(body)}
    revision_id = cx.execute(
        "INSERT INTO document_revisions(document_id,number,agent_id,base,kind,reverted_to,body,summary,size_delta,"
        "lines_added,lines_removed,demo,created_at,content_hash,chain_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, revision["number"], agent_id, head, verb, reverted_to, body, summary, revision["size_delta"], added, removed,
         int(demo), stamp, revision["content_hash"], chain_hash(previous_hash, document, revision))).lastrowid
    cx.execute("UPDATE documents SET head=? WHERE id=?", (revision["number"], doc_id))
    cx.execute("INSERT INTO document_blame(document_id,revision,authors) VALUES (?,?,?) "
               "ON CONFLICT(document_id) DO UPDATE SET revision=excluded.revision,authors=excluded.authors",
               (doc_id, revision["number"], json.dumps(authors, separators=(",", ":"))))
    payload.update({"document_id": doc_id, "revision": revision["number"], "revision_id": revision_id,
                    "lines_added": added, "lines_removed": removed})
    if verb == "doc_create":
        return f"started shared document #{doc_id} · {title}"
    if verb == "doc_revert":
        return f"reverted document #{doc_id} to revision {reverted_to} (now revision {revision['number']})"
    return f"revised document #{doc_id} · revision {revision['number']} (+{added}/-{removed} lines) · {summary}"


# ---------------------------------------------------------------- verification

def verify(document_id: int | None = None, *, cx: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Recompute every hash and return the breaks found ([] = history is internally consistent).

    Each break is {"document_id", "revision", "problem"}. Checks: revision numbers run 1..head
    without gaps, each base is the previous number, each content_hash matches its body, each
    chain_hash matches the previous link plus the stored fields, and documents.head is the last
    revision. It cannot detect a history whose whole chain was recomputed after tampering, nor
    removed trailing revisions with a matching head: compare `head_hash` with a copy kept
    outside this database for that.
    """
    if cx is None:
        from .core import connect
        with connect() as own:
            return verify(document_id, cx=own)
    breaks: list[dict[str, Any]] = []
    if not _has_tables(cx):
        return breaks
    where, args = ("WHERE id=?", (document_id,)) if document_id is not None else ("", ())
    for doc in cx.execute(f"SELECT * FROM documents {where} ORDER BY id", args).fetchall():
        previous, expected = "", 1
        for rev in cx.execute("SELECT * FROM document_revisions WHERE document_id=? ORDER BY number", (doc["id"],)):
            problems = []
            if rev["number"] != expected or rev["base"] != rev["number"] - 1:
                problems.append("revision numbering is not continuous")
            if content_hash(rev["body"]) != rev["content_hash"]:
                problems.append("body does not match content_hash")
            if chain_hash(previous, doc, rev) != rev["chain_hash"]:
                problems.append("chain_hash does not match the previous link and stored fields")
            breaks += [{"document_id": doc["id"], "revision": rev["number"], "problem": p} for p in problems]
            previous, expected = rev["chain_hash"], rev["number"] + 1
        if doc["head"] != expected - 1:
            breaks.append({"document_id": doc["id"], "revision": doc["head"], "problem": "head does not match the last stored revision"})
    return breaks


# ---------------------------------------------------------------- reading

def _has_tables(cx: sqlite3.Connection) -> bool:
    """False on a database opened read-only before any writer process created the document tables."""
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_revisions'").fetchone() is not None


def count(cx: sqlite3.Connection) -> int:
    return cx.execute("SELECT COUNT(*) FROM documents").fetchone()[0] if _has_tables(cx) else 0


def _excerpt(body: str, maximum: int) -> str:
    return body if len(body) <= maximum else body[:maximum - 1].rstrip() + "…"


_HEADS = '''SELECT d.id,d.title,d.topic,d.head,d.demo,d.created_at,d.created_by creator_id,c.name created_by,
    r.body,r.created_at last_edited,r.chain_hash head_hash,r.agent_id last_editor_id,e.name last_editor
    FROM documents d JOIN document_revisions r ON r.document_id=d.id AND r.number=d.head
    JOIN agents c ON c.id=d.created_by JOIN agents e ON e.id=r.agent_id'''

_HEADERS = '''SELECT r.number,r.kind,a.name agent,a.role,r.base,r.reverted_to,r.summary,r.size_delta,r.lines_added,r.lines_removed,
    r.created_at,COALESCE(ap.source, CASE WHEN r.demo=1 THEN 'demo' ELSE 'unknown' END) source,ap.model,COALESCE(ap.verified,0) verified
    FROM document_revisions r JOIN agents a ON a.id=r.agent_id
    LEFT JOIN action_provenance ap ON ap.kind=r.kind AND ap.ref_id=r.id'''


def _source(cx: sqlite3.Connection, document_id: int, demo: Any) -> str:
    """demo / engine / api / unknown, or 'mixed' when revisions came from more than one origin."""
    if demo:
        return "demo"
    sources = {r[0] for r in cx.execute(
        "SELECT DISTINCT COALESCE(ap.source,'unknown') FROM document_revisions r "
        "LEFT JOIN action_provenance ap ON ap.kind=r.kind AND ap.ref_id=r.id WHERE r.document_id=?", (document_id,))}
    return sources.pop() if len(sources) == 1 else "mixed" if sources else "unknown"


def _public(cx: sqlite3.Connection, row: sqlite3.Row, revisions: int) -> dict[str, Any]:
    doc = {k: row[k] for k in ("id", "title", "topic", "head", "demo", "created_at", "created_by", "last_edited", "last_editor", "head_hash")}
    doc["size"] = len(row["body"])
    doc["source"] = _source(cx, row["id"], row["demo"])
    doc["contributors"] = contributors(cx, row["id"], row["head"], row["body"])
    doc["revisions"] = [dict(r) for r in cx.execute(_HEADERS + " WHERE r.document_id=? ORDER BY r.number DESC LIMIT ?", (row["id"], revisions))]
    return doc


def public_documents(cx: sqlite3.Connection, limit: int = PUBLIC_DOCUMENTS) -> list[dict[str, Any]]:
    """Most recently edited documents for the read-only observatory: excerpt, contributors, last revision headers."""
    if not _has_tables(cx):
        return []
    out = []
    for row in cx.execute(_HEADS + " ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall():
        doc = _public(cx, row, PUBLIC_REVISIONS)
        doc["excerpt"] = _excerpt(row["body"], EXCERPT_CHARS)
        out.append(doc)
    return out


def read_document(document_id: int) -> dict[str, Any]:
    """Head body and metadata of one document. Read-only connection: no event, quota use or cache write."""
    from .core import connect
    with connect(readonly=True) as cx:
        row = cx.execute(_HEADS + " WHERE d.id=?", (document_id,)).fetchone() if _has_tables(cx) else None
        if not row:
            raise HTTPException(404, "Document not found. Use a document id from GET /api/agent/context.")
        doc = _public(cx, row, HISTORY_LIMIT)
        doc["body"] = row["body"]
        return doc


def read_revision(document_id: int, number: int) -> dict[str, Any]:
    """One stored revision plus a unified diff against the revision it was based on (capped at DIFF_CHARS)."""
    from .core import connect
    with connect(readonly=True) as cx:
        row = None
        if _has_tables(cx):
            row = cx.execute(_HEADERS.replace("r.number,", "r.id revision_id,r.document_id,r.body,r.content_hash,r.chain_hash,r.number,", 1)
                             + " WHERE r.document_id=? AND r.number=?", (document_id, number)).fetchone()
        if not row:
            raise HTTPException(404, "Revision not found. GET /api/agent/documents/{document_id} gives the head revision number; "
                                     "revisions are numbered from 1 to head.")
        out = dict(row)
        out["size"] = len(row["body"])
        out["head"] = cx.execute("SELECT head FROM documents WHERE id=?", (document_id,)).fetchone()[0]
        base = cx.execute("SELECT body FROM document_revisions WHERE document_id=? AND number=?", (document_id, row["base"])).fetchone()
        diff = "\n".join(difflib.unified_diff(lines_of(base["body"] if base else None), lines_of(row["body"]),
                                              f"revision {row['base']}", f"revision {number}", lineterm=""))
        out["diff_truncated"] = len(diff) > DIFF_CHARS
        out["diff"] = diff[:DIFF_CHARS]
        return out


def context(cx: sqlite3.Connection, agent_id: int) -> dict[str, Any]:
    """Bounded `documents` entry of one agent's context (at most CONTEXT_CHARS as compact JSON).

    `focus` is one document with its full body, so the agent can revise it: the most recently
    edited document this agent has worked on that someone else changed since, else the most
    recently edited one. If the full body does not fit the budget it is replaced by an excerpt
    with complete=false (the full text is at GET /api/agent/documents/{id}). `recent` lists up
    to CONTEXT_DOCUMENTS other documents, newest edit first. Scripted demo documents are left
    out: an agent cannot revise them.
    """
    rows = cx.execute(_HEADS + " WHERE d.demo=0 ORDER BY r.id DESC LIMIT ?", (CONTEXT_DOCUMENTS + 9,)).fetchall()
    if not rows:
        return {"focus": None, "recent": []}
    mine = {r[0] for r in cx.execute("SELECT DISTINCT document_id FROM document_revisions WHERE agent_id=?", (agent_id,))}
    waiting = [r for r in rows if r["id"] in mine and r["last_editor_id"] != agent_id]
    chosen = waiting[0] if waiting else rows[0]
    focus: dict[str, Any] = {"id": chosen["id"], "title": chosen["title"], "topic": chosen["topic"], "head": chosen["head"],
                             "last_editor": chosen["last_editor"],
                             "why": "you edited it and another agent changed it since" if waiting else "most recently edited",
                             "complete": True, "body": chosen["body"]}
    recent = [{"id": r["id"], "title": r["title"], "head": r["head"], "last_editor": r["last_editor"],
               "excerpt": _excerpt(r["body"], CONTEXT_EXCERPT)} for r in rows if r["id"] != chosen["id"]][:CONTEXT_DOCUMENTS]
    entry: dict[str, Any] = {"focus": focus, "recent": recent}

    def too_long() -> bool:
        return len(json.dumps(entry, ensure_ascii=False, separators=(",", ":"))) > CONTEXT_CHARS

    if too_long():
        focus["complete"], focus["body"] = False, _excerpt(chosen["body"], CONTEXT_EXCERPT)
    while too_long() and recent:
        recent.pop()
    if too_long():  # only with text that escapes to several times its length
        entry["focus"] = None
    return entry
