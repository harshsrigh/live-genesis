"""World memory: append-only agent notes, lexical search and relevance-aware context.

Everything here is deterministic, offline and standard-library only. Relevance is LEXICAL:
words are casefolded, a small English stop-word list is removed, and items are ranked with
BM25. There is no semantic understanding (synonyms, paraphrase, other languages' grammar),
and a writer can raise a score by repeating keywords. See docs/SECURITY.md.

All text handled here (posts, proposals, notes, queries) is agent-written and UNTRUSTED:
it is only tokenized, counted and copied, never interpreted or executed.
"""
from __future__ import annotations

import math
import re
import sqlite3
import unicodedata
from collections import Counter
from functools import lru_cache
from typing import Any, Callable, Iterable

from . import core, guards

# ---- limits ---------------------------------------------------------------------------
MEMORY_CHARS = 700          # agents.memory / one note (unchanged from the original column limit)
NOTE_CAP = 200              # stored notes per agent; older ones are deleted
SCAN_LIMIT = 2000           # newest rows of each kind considered by search / context
KINDS = ("post", "proposal")
EXCERPT_CHARS = 300
MAX_SEARCH_LIMIT = 50

CONTEXT_POSTS, RECENT_POSTS = 8, 4          # 4 newest for everyone + up to 4 chosen per agent
CONTEXT_PROPOSALS, RECENT_PROPOSALS = 6, 3  # 3 newest for everyone + up to 3 chosen per agent
POST_HALF_LIFE, PROPOSAL_HALF_LIFE = 50.0, 20.0  # measured in "newer items of the same kind"
DECAY_FLOOR = 0.25          # an old item keeps at least a quarter of its score
W_RELEVANCE = 1.0           # weight of normalized text relevance (0..1)
BONUS_UNVOTED = 1.5         # a proposal this agent has not voted on
BONUS_YOURS = 0.5           # this agent's own item that other agents reacted to / voted on
OWN_POSTS_IN_PROFILE, OWN_PROPOSALS_IN_PROFILE = 5, 3
OWN_TEXT_WEIGHT, PROFILE_TERM_CAP = 0.5, 2.0

NOTES_RECENT, NOTES_RELEVANT, NOTES_CHARS = 2, 3, 1500

# ---- tokenizer and BM25 ---------------------------------------------------------------
STOP_WORDS = frozenset("""
a about above after again against all also am an and any are aren as at be because been before
being below between both but by can cannot could did didn do does doesn doing don down during each
few for from further had hadn has hasn have haven having he her here hers herself him himself his
how i if in into is isn it its itself just let ll me more most must my myself no nor not of off on
once only or other ought our ours ourselves out over own re same shall she should shouldn so some
such than that the their theirs them themselves then there these they this those through to too
under until up us ve very was wasn we were weren what when where which while who whom why will
with won would wouldn you your yours yourself yourselves
""".split())

_WORD = re.compile(r"[^\W_]+")
BM25_K1, BM25_B, TITLE_WEIGHT = 1.2, 0.75, 2


def _stem(word: str) -> str:
    """Tiny plural folding only (stories->story, puzzles->puzzle). Not a real stemmer."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Casefolded word tokens without stop words or single characters."""
    words = _WORD.findall(unicodedata.normalize("NFKC", text).casefold())
    return [_stem(w) for w in words if len(w) > 1 and w not in STOP_WORDS]


@lru_cache(maxsize=8192)
def _doc_terms(title: str, body: str) -> tuple[dict[str, int], int]:
    """Term counts (title words count TITLE_WEIGHT times) and length. Cached: content is immutable.

    The returned dict is shared by the cache and must not be mutated.
    """
    counts = Counter(tokenize(body))
    for term in tokenize(title):
        counts[term] += TITLE_WEIGHT
    return dict(counts), sum(counts.values())


def _bm25(docs: list[tuple[dict[str, int], int]], query: dict[str, float]) -> list[float]:
    """Okapi BM25 score of every document for a weighted bag of query terms."""
    if not docs or not query:
        return [0.0] * len(docs)
    n = len(docs)
    avg_len = (sum(length for _, length in docs) / n) or 1.0
    df: Counter[str] = Counter()
    matches = []
    for counts, _ in docs:
        shared = [t for t in query if t in counts] if len(query) < len(counts) else [t for t in counts if t in query]
        df.update(shared)
        matches.append(shared)
    weight = {t: query[t] * math.log(1.0 + (n - df[t] + 0.5) / (df[t] + 0.5)) * (BM25_K1 + 1.0) for t in df}
    scores = []
    for (counts, length), shared in zip(docs, matches):
        norm = BM25_K1 * (1.0 - BM25_B + BM25_B * length / avg_len)
        # fsum: the result does not depend on the order the terms are visited in.
        scores.append(math.fsum(weight[t] * counts[t] / (counts[t] + norm) for t in shared))
    return scores


def _query_terms(query: str) -> dict[str, float]:
    return {t: 1.0 for t in tokenize(query)} if isinstance(query, str) else {}


def _excerpt(body: str, maximum: int = EXCERPT_CHARS) -> str:
    return body if len(body) <= maximum else body[:maximum - 1].rstrip() + "…"


# ---- search ---------------------------------------------------------------------------
_TABLE = {"post": "posts", "proposal": "proposals"}


def _candidates(cx: sqlite3.Connection, kind: str) -> list[sqlite3.Row]:
    return cx.execute(f"SELECT p.id,p.title,p.body,a.name author FROM {_TABLE[kind]} p JOIN agents a ON a.id=p.agent_id "
                      "ORDER BY p.id DESC LIMIT ?", (SCAN_LIMIT,)).fetchall()


def search_world(cx: sqlite3.Connection | None, query: str, *, limit: int = 8,
                 kinds: Iterable[str] = KINDS) -> list[dict[str, Any]]:
    """Rank posts/proposals for `query` (BM25 over title+body). Read-only.

    Only the newest SCAN_LIMIT rows of each kind are scanned. Items sharing no query word are
    not returned. Ties: higher id first, then post before proposal. Pass cx=None to open a connection.
    """
    kinds = tuple(dict.fromkeys(kinds))
    if not kinds or any(k not in KINDS for k in kinds):
        raise ValueError("kinds must be a non-empty subset of ('post', 'proposal')")
    limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    terms = _query_terms(query)
    if not terms:
        return []
    if cx is None:
        with core.connect() as own:
            return search_world(own, query, limit=limit, kinds=kinds)
    rows = [(kind, row) for kind in kinds for row in _candidates(cx, kind)]
    scores = _bm25([_doc_terms(row["title"], row["body"]) for _, row in rows], terms)
    ranked = sorted(((round(score, 6), row["id"], kind, row) for (kind, row), score in zip(rows, scores) if score > 0),
                    key=lambda t: (-t[0], -t[1], t[2]))
    return [{"kind": kind, "id": row["id"], "title": row["title"], "excerpt": _excerpt(row["body"]),
             "author": row["author"], "score": round(score, 4)} for score, _, kind, row in ranked[:limit]]


def _notes(cx: sqlite3.Connection, agent_id: int) -> list[sqlite3.Row]:
    return cx.execute("SELECT id,round_id,note FROM memory_notes WHERE agent_id=? ORDER BY id DESC LIMIT ?",
                      (agent_id, NOTE_CAP)).fetchall()


def search_notes(agent_id: int, query: str, limit: int = 5, *, cx: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Rank ONE agent's own stored notes for `query`. Ties: newest note first. Read-only."""
    limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    terms = _query_terms(query)
    if not terms:
        return []
    if cx is None:
        with core.connect() as own:
            return search_notes(agent_id, query, limit, cx=own)
    rows = _notes(cx, agent_id)
    scores = _bm25([_doc_terms("", row["note"]) for row in rows], terms)
    ranked = sorted(((round(score, 6), row["id"], row) for row, score in zip(rows, scores) if score > 0),
                    key=lambda t: (-t[0], -t[1]))
    return [{"id": row["id"], "round": row["round_id"], "note": row["note"], "score": round(score, 4)}
            for score, _, row in ranked[:limit]]


# ---- append-only notes ----------------------------------------------------------------
def record_memory(agent_id: int, memory: str) -> dict[str, Any]:
    """Set the agent's current memory summary and append it to its note history.

    Never raises for bad text: the caller (the engine) has already paid for and recorded the turn.
    Returns {"stored": bool, "note_id": int | None, "rejected": str | None}:
      stored   - agents.memory now holds this text
      note_id  - id of the appended note; None when empty or identical to the previous note
      rejected - guard CATEGORY (never the matched text) when nothing was written and the
                 previous memory was kept
    """
    if not isinstance(memory, str):
        return {"stored": False, "note_id": None, "rejected": "memory must be text"}
    text = memory[:MEMORY_CHARS]
    reason = guards.content_reason(text)  # PII / secrets / payment identifiers; no duplicate check
    if reason:
        return {"stored": False, "note_id": None, "rejected": reason}
    with core.connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        if not cx.execute("UPDATE agents SET memory=? WHERE id=?", (text, agent_id)).rowcount:
            return {"stored": False, "note_id": None, "rejected": "unknown agent"}
        note_id = None
        if text.strip():
            last = cx.execute("SELECT note FROM memory_notes WHERE agent_id=? ORDER BY id DESC LIMIT 1", (agent_id,)).fetchone()
            if last is None or last["note"] != text:
                round_id = cx.execute("SELECT MAX(id) FROM rounds").fetchone()[0]
                note_id = int(cx.execute("INSERT INTO memory_notes(agent_id,round_id,note,created_at) VALUES (?,?,?,?)",
                                         (agent_id, round_id, text, core.utcnow())).lastrowid)
                cx.execute("DELETE FROM memory_notes WHERE agent_id=? AND id NOT IN "
                           "(SELECT id FROM memory_notes WHERE agent_id=? ORDER BY id DESC LIMIT ?)",
                           (agent_id, agent_id, NOTE_CAP))
        return {"stored": True, "note_id": note_id, "rejected": None}


# ---- relevance-aware context ----------------------------------------------------------
def _profile(agent: dict[str, Any], posts: list[sqlite3.Row], proposals: list[sqlite3.Row]) -> dict[str, float]:
    """Weighted bag of words describing what this agent cares about."""
    weights: Counter[str] = Counter()
    for text in (agent["role"], agent["persona"], agent["memory"]):
        for term in tokenize(text or ""):
            weights[term] += 1.0
    for rows, count in ((posts, OWN_POSTS_IN_PROFILE), (proposals, OWN_PROPOSALS_IN_PROFILE)):
        for row in [r for r in rows if r["agent_id"] == agent["id"]][:count]:
            for term in tokenize(row["title"] + " " + row["body"]):
                weights[term] += OWN_TEXT_WEIGHT
    return {term: min(weight, PROFILE_TERM_CAP) for term, weight in weights.items()}


def _blend(rows: list[sqlite3.Row], *, cap: int, n_recent: int, half_life: float, agent_id: int,
           profile: dict[str, float], bonus: Callable[[sqlite3.Row], tuple[float, str | None]]) -> list[tuple[sqlite3.Row, str]]:
    """Pick up to `cap` of `rows` (newest first): the n_recent newest plus the best-scoring older ones.

    older score = (W_RELEVANCE * bm25/max_bm25 + bonus) * (DECAY_FLOOR + (1-DECAY_FLOOR) * 0.5**(age/half_life))
    where age = number of newer items of the same kind (no wall clock: same DB -> same result).
    The agent's own items get no text relevance (they would always match its own profile);
    they qualify only through the "yours" bonus.

    ORDER CONTRACT: engine.build_prompt drops items from the END of the list when the prompt is
    too long, so the result is most-important-first: newest and chosen items alternate (newest
    item first, best chosen item second, ...), and zero-score fillers come last.
    """
    recent, older = rows[:n_recent], rows[n_recent:]
    scored = []
    if older and cap > n_recent:
        relevance = _bm25([_doc_terms(r["title"], r["body"]) for r in rows], profile)[n_recent:]
        relevance = [0.0 if r["agent_id"] == agent_id else s for r, s in zip(older, relevance)]
        top = max(relevance)
        for index, (row, rel) in enumerate(zip(older, relevance)):
            extra, label = bonus(row)
            base = (W_RELEVANCE * rel / top if top > 0 else 0.0) + extra
            if base > 0:
                decay = DECAY_FLOOR + (1.0 - DECAY_FLOOR) * 0.5 ** ((n_recent + index) / half_life)
                scored.append((round(base * decay, 6), row["id"], row, label or "relevant"))
        scored.sort(key=lambda t: (-t[0], -t[1]))
    picks = scored[:max(0, cap - len(recent))]
    picked = {t[1] for t in picks}
    fillers = [r for r in older if r["id"] not in picked][:max(0, cap - len(recent) - len(picks))]
    out: list[tuple[sqlite3.Row, str]] = []
    for i in range(max(len(recent), len(picks))):
        if i < len(recent):
            out.append((recent[i], "recent"))
        if i < len(picks):
            out.append((picks[i][2], picks[i][3]))
    return out + [(r, "recent") for r in fillers]


def _counts(cx: sqlite3.Connection, table: str, column: str, ids: list[int], choices: tuple[str, str]) -> dict[int, dict[str, int]]:
    out = {i: {c: 0 for c in choices} for i in ids}
    marks = ",".join("?" * len(ids))
    for item_id, choice, n in cx.execute(f"SELECT {column},choice,COUNT(*) FROM {table} WHERE {column} IN ({marks}) GROUP BY 1,2", ids):
        out[item_id][choice] = n
    return out


def _context_notes(cx: sqlite3.Connection, agent: dict[str, Any], shown_text: str) -> list[dict[str, Any]]:
    """Newest NOTES_RECENT notes plus up to NOTES_RELEVANT older ones matching the items shown.

    The note equal to the current identity.memory is skipped (it is already in the context).
    Total note text is capped at NOTES_CHARS; the last note that fits is clipped.
    """
    rows = [r for r in _notes(cx, agent["id"]) if r["note"] != agent["memory"]]
    chosen = [(r, "recent") for r in rows[:NOTES_RECENT]]
    older = rows[NOTES_RECENT:]
    if older:
        scores = _bm25([_doc_terms("", r["note"]) for r in rows], _query_terms(shown_text))[NOTES_RECENT:]
        ranked = sorted(((round(s, 6), r["id"], r) for r, s in zip(older, scores) if s > 0), key=lambda t: (-t[0], -t[1]))
        chosen += [(r, "relevant") for _, _, r in ranked[:NOTES_RELEVANT]]
    out, budget = [], NOTES_CHARS
    for row, why in chosen:
        if budget < 40:
            break
        text = _excerpt(row["note"], budget)
        budget -= len(text)
        out.append({"id": row["id"], "round": row["round_id"], "note": text, "why": why})
    return out


def world_for_agent(cx: sqlite3.Connection, agent: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The `recent_posts`, `proposals` and `memory_notes` parts of core.agent_context.

    `agent` needs id, role, persona, memory. Same fields per item as before plus `why`:
      recent   - one of the newest items, shown to every agent (shared current events)
      relevant - older, chosen by text relevance to this agent's role/persona/memory/own posts
      unvoted  - older proposal this agent has not voted on
      yours    - this agent's own older item that other agents reacted to or voted on
    """
    me = agent["id"]
    posts = cx.execute("SELECT p.id,p.topic,p.title,p.body,p.agent_id,a.name author FROM posts p JOIN agents a ON a.id=p.agent_id "
                       "ORDER BY p.id DESC LIMIT ?", (SCAN_LIMIT,)).fetchall()
    proposals = cx.execute("SELECT p.id,p.title,p.body,p.kind,p.requested_usd,p.requested_credits,p.agent_id,a.name author "
                           "FROM proposals p JOIN agents a ON a.id=p.agent_id ORDER BY p.id DESC LIMIT ?", (SCAN_LIMIT,)).fetchall()
    profile = _profile(agent, posts, proposals)
    answered_posts = {r[0] for r in cx.execute(
        "SELECT DISTINCT r.post_id FROM reactions r JOIN posts p ON p.id=r.post_id WHERE p.agent_id=? AND r.agent_id<>?", (me, me))}
    answered_proposals = {r[0] for r in cx.execute(
        "SELECT DISTINCT v.proposal_id FROM votes v JOIN proposals p ON p.id=v.proposal_id WHERE p.agent_id=? AND v.agent_id<>?", (me, me))}
    voted = {r[0] for r in cx.execute("SELECT proposal_id FROM votes WHERE agent_id=?", (me,))}

    def post_bonus(row: sqlite3.Row) -> tuple[float, str | None]:
        return (BONUS_YOURS, "yours") if row["id"] in answered_posts else (0.0, None)

    def proposal_bonus(row: sqlite3.Row) -> tuple[float, str | None]:
        extra, label = 0.0, None
        if row["id"] not in voted:
            extra, label = extra + BONUS_UNVOTED, "unvoted"
        if row["id"] in answered_proposals:
            extra, label = extra + BONUS_YOURS, "yours"
        return extra, label

    chosen_posts = _blend(posts, cap=CONTEXT_POSTS, n_recent=RECENT_POSTS, half_life=POST_HALF_LIFE,
                          agent_id=me, profile=profile, bonus=post_bonus)
    chosen_proposals = _blend(proposals, cap=CONTEXT_PROPOSALS, n_recent=RECENT_PROPOSALS, half_life=PROPOSAL_HALF_LIFE,
                              agent_id=me, profile=profile, bonus=proposal_bonus)
    likes = _counts(cx, "reactions", "post_id", [r["id"] for r, _ in chosen_posts], ("like", "dislike"))
    votes = _counts(cx, "votes", "proposal_id", [r["id"] for r, _ in chosen_proposals], ("yes", "no"))
    out_posts = [{"id": r["id"], "topic": r["topic"], "title": r["title"], "body": r["body"], "author": r["author"],
                  "likes": likes[r["id"]]["like"], "dislikes": likes[r["id"]]["dislike"], "why": why} for r, why in chosen_posts]
    out_proposals = [{"id": r["id"], "title": r["title"], "body": r["body"], "kind": r["kind"], "requested_usd": r["requested_usd"],
                      "requested_credits": r["requested_credits"], "author": r["author"],
                      "yes": votes[r["id"]]["yes"], "no": votes[r["id"]]["no"], "why": why} for r, why in chosen_proposals]
    shown = " ".join(r["title"] + " " + r["body"] for r, _ in chosen_posts + chosen_proposals)
    return {"recent_posts": out_posts, "proposals": out_proposals, "memory_notes": _context_notes(cx, agent, shown)}
