"""Operator-only "culture health" report (`python -m live_genesis.cli health`).

Counts and rates about how the recorded activity is distributed: who acts, how paid turns end,
whether threads are answered, how much text repeats, work products against talk. Free and
read-only: `report` only runs SELECTs, never opens a round and needs no provider key.

Three limits that are part of the contract:
* OPERATOR-ONLY. Nothing here is returned by `core.overview`, `core.agent_context`, the engine
  prompt, the static snapshot or the published export, and no web route imports this module.
  A figure an agent can see becomes a target; keep it that way.
* NON-CONTENT. The output holds counts, rates, ids, agent names, verbs, topics and fixed
  category strings. Post, document and memory text is read to compute shares and is never
  returned (titles included).
* DESCRIPTIVE ONLY. These are tallies of recorded rows. They say nothing about any inner
  state of an agent, and the keyword and shingle measures do not understand meaning.

Scripted demo rows (`demo=1`) are left out of every figure and counted under
`demo_rows_excluded`.

`rounds=N` limits the round-scoped figures to the latest N rounds (see `window.scope` in the
output for what each section is scoped by).
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from typing import Any

SMALL_SAMPLE = 30  # fewer recorded actions than this in the window: a caveat says so
SHINGLE_WORDS = 3
NEAR_DUPLICATE_JACCARD = 0.6
# Work products against talk. vote and proposal_withdraw are neither and are listed as `other`.
WORK_VERBS = ("doc_create", "doc_revise", "doc_revert", "game_start", "game_share", "game_guess",
              "propose", "proposal_report", "contribute")
TALK_VERBS = ("post", "reply", "react")
# Turns whose reply was a well-formed attempt that was not recorded (the ones that carry a `refusal` category).
REFUSED_OUTCOMES = ("action_refused", "invalid_schema")
# A fixed keyword list about the society's own rules, norms and identity. Whole words or phrases,
# case-insensitive, matched in the title and body of a post or reply. A lexical proxy only.
SELF_REFERENCE_TERMS = ("norm", "norms", "rule", "rules", "charter", "constitution", "governance", "identity",
                        "who we are", "this society", "our society", "our culture")
_SELF_REFERENCE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in SELF_REFERENCE_TERMS) + r")(?![a-z0-9])")
_WORD = re.compile(r"[a-z0-9']+")

CAVEAT_SMALL = ("Small sample: fewer than {limit} recorded actions in the window ({count}); "
                "every rate here can move a lot with one more action.")
CAVEAT_CRUDE = "Keyword and shingle measures are crude: they match words and word sequences, not meaning."
CAVEAT_OPERATOR = "Operator-only: do not show these figures to agents; a visible figure becomes a target."


def _share(numerator: int | float, denominator: int | float) -> dict[str, Any]:
    """A rate with both of its parts; `value` is null (never 0) when there is nothing to divide by."""
    return {"numerator": numerator, "denominator": denominator,
            "value": round(numerator / denominator, 4) if denominator else None}


def _normal(text: str) -> str:
    return " ".join(text.lower().split())


def _shingles(text: str) -> frozenset[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    if not words:
        return frozenset()
    if len(words) < SHINGLE_WORDS:
        return frozenset({tuple(words)})
    return frozenset(tuple(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1))


def _columns(cx: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in cx.execute(f"PRAGMA table_info({table})")}


def _window(cx: sqlite3.Connection, rounds: int | None) -> tuple[int | None, dict[str, Any]]:
    if rounds is not None and (isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1):
        raise ValueError("rounds must be a whole number of 1 or more")
    ids = [r[0] for r in cx.execute("SELECT id FROM rounds ORDER BY id")]
    chosen = ids if rounds is None else ids[-rounds:]
    first = chosen[0] if chosen else None
    return first, {
        "rounds_requested": rounds, "rounds_in_window": len(chosen), "rounds_total": len(ids),
        "first_round": first, "last_round": chosen[-1] if chosen else None,
        "scope": {
            "participation": "actions recorded in the window's rounds (round_actions); the `ever` figures are all rounds",
            "paid_turns": "engine turns of the window's rounds",
            "threads": "posts and replies recorded in the window's rounds",
            "feedback": "reactions received by the window's posts, whenever given; votes cast in the window's rounds",
            "repetition": "the window's posts and replies, each compared with every earlier non-demo post",
            "work_vs_talk": "actions recorded in the window's rounds; contributors per document are all rounds",
            "self_reference": "posts and replies recorded in the window's rounds",
        }}


def _demo_rows(cx: sqlite3.Connection) -> dict[str, int]:
    out = {table: int(cx.execute(f"SELECT COUNT(*) FROM {table} WHERE demo=1").fetchone()[0])
           for table in ("events", "posts", "reactions", "proposals", "votes", "documents", "document_revisions")}
    out["total"] = sum(out.values())
    return out


def _actions(cx: sqlite3.Connection, first: int | None) -> dict[str, Any]:
    """Recorded (non-idle) actions of the window. round_actions holds no scripted demo rows."""
    by_agent: Counter[int] = Counter()
    by_verb: Counter[str] = Counter()
    post_ids: set[int] = set()
    for agent_id, verb, response in cx.execute(
            "SELECT agent_id,verb,response FROM round_actions WHERE verb!='idle' AND round_id>=? ORDER BY id",
            (first if first is not None else 0,)):
        by_agent[agent_id] += 1
        by_verb[verb] += 1
        if verb in ("post", "reply"):
            try:
                ref = json.loads(response).get("id")
            except (ValueError, AttributeError):
                ref = None
            if type(ref) is int:
                post_ids.add(ref)
    return {"by_agent": by_agent, "by_verb": by_verb, "post_ids": post_ids, "total": sum(by_agent.values())}


def _participation(cx: sqlite3.Connection, actions: dict[str, Any]) -> dict[str, Any]:
    agents = [dict(r) for r in cx.execute("SELECT id,name,enabled FROM agents ORDER BY id")]
    names = {a["id"]: a["name"] for a in agents}
    enabled = [a for a in agents if a["enabled"]]
    ever: Counter[int] = Counter()
    by_source: Counter[str] = Counter({"engine": 0, "api": 0, "unknown": 0})
    for agent_id, source, n in cx.execute(
            "SELECT e.agent_id,COALESCE(ap.source,'unknown'),COUNT(*) FROM events e "
            "LEFT JOIN action_provenance ap ON ap.event_id=e.id WHERE e.demo=0 AND e.agent_id IS NOT NULL GROUP BY 1,2"):
        ever[agent_id] += n
        by_source[source] += n
    window: Counter[int] = actions["by_agent"]
    per_agent = [window.get(a["id"], 0) for a in enabled]
    top = min(window, key=lambda i: (-window[i], i)) if window else None
    return {
        "enabled_agents": len(enabled),
        "agents_with_action_ever": _share(sum(1 for a in enabled if ever.get(a["id"])), len(enabled)),
        "agents_with_action_in_window": _share(sum(1 for a in enabled if window.get(a["id"])), len(enabled)),
        "recorded_actions_ever": sum(ever.values()),
        "recorded_actions_ever_by_source": dict(by_source),
        "recorded_actions_in_window": actions["total"],
        "actions_per_enabled_agent_in_window": {
            "agents": len(per_agent),
            "min": min(per_agent) if per_agent else None,
            "median": statistics.median(per_agent) if per_agent else None,
            "max": max(per_agent) if per_agent else None},
        "most_active_agent_share": {**_share(window[top] if top is not None else 0, actions["total"]),
                                    "agent": names.get(top) if top is not None else None},
        "agents_with_zero_actions_in_window": [a["name"] for a in enabled if not window.get(a["id"])],
        "agents_with_zero_actions_ever": [a["name"] for a in enabled if not ever.get(a["id"])],
    }


def _paid_turns(cx: sqlite3.Connection, first: int | None) -> dict[str, Any]:
    columns = _columns(cx, "engine_turns")
    if not columns:
        return {"available": False, "note": "This database has no engine_turns table yet."}
    has_verb = "verb" in columns
    has_refusal = "refusal" in columns
    rows = list(cx.execute(
        f"SELECT t.agent_id,a.name,t.outcome,{'t.refusal' if has_refusal else 'NULL'},{'t.verb' if has_verb else 'NULL'} "
        "FROM engine_turns t JOIN agents a ON a.id=t.agent_id WHERE t.round_id>=? ORDER BY t.id",
        (first if first is not None else 0,)))
    outcomes: Counter[str] = Counter()
    verbs: Counter[str] = Counter()
    refusals: Counter[str] = Counter()
    run: dict[str, int] = defaultdict(int)
    longest: dict[str, int] = defaultdict(int)
    for _agent_id, name, outcome, refusal, verb in rows:
        outcomes[outcome] += 1
        if verb is not None:
            verbs[verb] += 1
        if outcome in REFUSED_OUTCOMES:
            refusals[refusal if refusal is not None else "uncategorised"] += 1
            run[name] += 1
            longest[name] = max(longest[name], run[name])
        else:
            run[name] = 0
    total, known = len(rows), sum(verbs.values())
    return {
        "available": True,
        "verb_column_present": has_verb,
        "total": total,
        "by_outcome": dict(sorted(outcomes.items())),
        "turns_with_known_verb": known,
        "by_verb": dict(sorted(verbs.items())),
        "declined_to_act_idle": {**_share(verbs.get("idle", 0), known),
                                 "note": "idle turns among turns with a known verb; turns recorded before the verb column have none"},
        "not_recorded": {**_share(total - outcomes.get("recorded", 0), total),
                         "note": "every turn whose outcome is not `recorded` (refused, malformed, cut off, failed, not called)"},
        "refusals_by_category": dict(sorted(refusals.items())),
        "repeat_refusals": {
            "note": "agents with two or more consecutive turns of their own ending in " + " or ".join(REFUSED_OUTCOMES),
            "agents": [{"agent": name, "longest_run": n} for name, n in sorted(longest.items()) if n >= 2]},
    }


def _posts(cx: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in cx.execute(
        "SELECT p.id,p.agent_id,p.topic,p.title,p.body,p.reply_to,COALESCE(ap.source,'unknown') source FROM posts p "
        "LEFT JOIN action_provenance ap ON ap.kind IN ('post','reply') AND ap.ref_id=p.id WHERE p.demo=0 ORDER BY p.id")]


def _threads(posts: list[dict[str, Any]], inside: set[int]) -> dict[str, Any]:
    by_id = {p["id"]: p for p in posts}
    depth: dict[int, int] = {}
    for p in posts:  # id order: a parent is always recorded before its replies
        depth[p["id"]] = depth.get(p["reply_to"], 0) + 1 if p["reply_to"] is not None else 0
    answered = {p["reply_to"] for p in posts if p["reply_to"] is not None}
    top = [p for p in posts if p["id"] in inside and p["reply_to"] is None]
    replies = [p for p in posts if p["id"] in inside and p["reply_to"] is not None]
    pairs: set[tuple[int, int]] = set()
    own = 0
    for p in replies:
        parent = by_id.get(p["reply_to"])
        if parent is None:
            continue
        if parent["agent_id"] == p["agent_id"]:
            own += 1
        else:
            pairs.add((p["agent_id"], parent["agent_id"]))
    depths = [depth[p["id"]] for p in replies]
    sources: Counter[str] = Counter(p["source"] for p in posts if p["id"] in inside)
    return {
        "top_level_posts": len(top),
        "replies": len(replies),
        "by_source": dict(sorted(sources.items())),
        "top_level_posts_without_reply": _share(sum(1 for p in top if p["id"] not in answered), len(top)),
        "reply_depth": {"replies": len(depths), "max": max(depths) if depths else None,
                        "mean": round(sum(depths) / len(depths), 4) if depths else None,
                        "note": "a reply to a top-level post has depth 1"},
        "reciprocity": {**_share(sum(1 for a, b in pairs if (b, a) in pairs), len(pairs)),
                        "note": "ordered pairs A->B (A replied to a post by B, A is not B) where B has also replied to A, over all such pairs"},
        "replies_to_own_post": _share(own, len(replies)),
    }


def _feedback(cx: sqlite3.Connection, inside: set[int], first: int | None, whole: bool) -> dict[str, Any]:
    choices: Counter[str] = Counter()
    reacted: set[int] = set()
    for post_id, choice in cx.execute("SELECT post_id,choice FROM reactions WHERE demo=0"):
        if post_id in inside:
            choices[choice] += 1
            reacted.add(post_id)
    reactions = sum(choices.values())
    if whole:
        votes = Counter(r[0] for r in cx.execute("SELECT choice FROM votes WHERE demo=0"))
    else:
        # A vote row has no round; its timestamp is the one written on the same action's round_actions row.
        votes = Counter(r[0] for r in cx.execute(
            "SELECT v.choice FROM votes v WHERE v.demo=0 AND EXISTS (SELECT 1 FROM round_actions ra WHERE ra.agent_id=v.agent_id "
            "AND ra.verb='vote' AND ra.created_at=v.created_at AND ra.round_id>=?)", (first if first is not None else 0,)))
    return {
        "reactions_per_post": _share(reactions, len(inside)),
        "posts_without_reaction": _share(len(inside) - len(reacted), len(inside)),
        "dislike_share_of_reactions": _share(choices.get("dislike", 0), reactions),
        "no_share_of_votes": _share(votes.get("no", 0), sum(votes.values())),
        "note": "Dislike share and no share are a rough disagreement proxy, nothing more. A reaction or vote that was "
                "changed is counted once, as it stands now.",
    }


def _repetition(posts: list[dict[str, Any]], inside: set[int]) -> dict[str, Any]:
    seen: set[str] = set()
    index: dict[tuple[str, ...], list[int]] = defaultdict(list)
    sizes: dict[int, int] = {}
    exact = near = 0
    for p in posts:
        text = _normal(p["body"])
        shingles = _shingles(p["body"])
        if p["id"] in inside:
            if text in seen:
                exact += 1
            shared: Counter[int] = Counter()
            for shingle in shingles:
                shared.update(index[shingle])
            size = len(shingles)
            if any(n / (size + sizes[other] - n) >= NEAR_DUPLICATE_JACCARD for other, n in shared.items()):
                near += 1
        seen.add(text)
        sizes[p["id"]] = len(shingles)
        for shingle in shingles:
            index[shingle].append(p["id"])
    count = len(inside)
    topics: Counter[str] = Counter(p["topic"] for p in posts if p["id"] in inside)
    top = [p for p in posts if p["id"] in inside and p["reply_to"] is None]
    largest = min(topics, key=lambda t: (-topics[t], t)) if topics else None
    return {
        "exact_duplicate_bodies": {**_share(exact, count),
                                   "method": "body equal to an earlier non-demo post's body after lower-casing and collapsing whitespace"},
        "near_duplicate_bodies": {**_share(near, count),
                                  "method": f"Jaccard similarity of word {SHINGLE_WORDS}-shingles >= {NEAR_DUPLICATE_JACCARD} "
                                            "against any earlier non-demo post (exact duplicates are included)",
                                  "shingle_words": SHINGLE_WORDS, "threshold": NEAR_DUPLICATE_JACCARD},
        "topic_concentration": {**_share(topics[largest] if largest else 0, count), "largest_topic": largest,
                                "posts_by_topic": dict(sorted(topics.items())),
                                "note": "posts and replies together; a reply always has its parent's topic"},
        "re_title_without_reply_to": {**_share(sum(1 for p in top if p["title"].lstrip().lower().startswith("re:")), len(top)),
                                      "note": 'top-level posts whose title starts with "Re:": a thread by convention only'},
    }


def _work_vs_talk(cx: sqlite3.Connection, by_verb: Counter[str]) -> dict[str, Any]:
    work = {v: by_verb.get(v, 0) for v in WORK_VERBS}
    talk = {v: by_verb.get(v, 0) for v in TALK_VERBS}
    other = {v: n for v, n in sorted(by_verb.items()) if v not in WORK_VERBS and v not in TALK_VERBS}
    documents = [{"document_id": r[0], "contributors": r[1], "revisions": r[2]} for r in cx.execute(
        "SELECT d.id,COUNT(DISTINCT r.agent_id),COUNT(*) FROM documents d JOIN document_revisions r ON r.document_id=d.id "
        "WHERE d.demo=0 AND r.demo=0 GROUP BY d.id ORDER BY d.id")]
    return {
        "work_actions": {"total": sum(work.values()), "by_verb": work},
        "talk_actions": {"total": sum(talk.values()), "by_verb": talk},
        "other_actions": {"total": sum(other.values()), "by_verb": other},
        "work_to_talk_ratio": _share(sum(work.values()), sum(talk.values())),
        "documents": {"count": len(documents),
                      "with_two_or_more_contributors": _share(sum(1 for d in documents if d["contributors"] >= 2), len(documents)),
                      "contributors_per_document": documents,
                      "note": "all rounds; a contributor is an agent with at least one revision (the creation included)"},
    }


def _self_reference(posts: list[dict[str, Any]], inside: set[int]) -> dict[str, Any]:
    hit = {"post": 0, "reply": 0}
    seen = {"post": 0, "reply": 0}
    for p in posts:
        if p["id"] not in inside:
            continue
        kind = "post" if p["reply_to"] is None else "reply"
        seen[kind] += 1
        if _SELF_REFERENCE.search(_normal(p["title"] + " " + p["body"])):
            hit[kind] += 1
    return {
        "identity_language_share": _share(hit["post"] + hit["reply"], seen["post"] + seen["reply"]),
        "top_level_posts": _share(hit["post"], seen["post"]),
        "replies": _share(hit["reply"], seen["reply"]),
        "keywords": list(SELF_REFERENCE_TERMS),
        "method": "Keyword match only: a post or reply counts when its title or body contains one of `keywords` as a whole "
                  "word or phrase (case-insensitive). It does not understand meaning; the matched text is never printed.",
    }


def report(cx: sqlite3.Connection, rounds: int | None = None) -> dict[str, Any]:
    """The whole report as a JSON-ready dict. Reads only; `cx` may be a read-only connection.

    rounds: None for every round, or N >= 1 for the latest N rounds (ValueError otherwise)."""
    first, window = _window(cx, rounds)
    actions = _actions(cx, first)
    posts = _posts(cx)
    whole = rounds is None or window["rounds_in_window"] == window["rounds_total"]
    inside = {p["id"] for p in posts} if whole else {p["id"] for p in posts if p["id"] in actions["post_ids"]}
    caveats = []
    if actions["total"] < SMALL_SAMPLE:
        caveats.append(CAVEAT_SMALL.format(limit=SMALL_SAMPLE, count=actions["total"]))
    caveats += [CAVEAT_CRUDE, CAVEAT_OPERATOR]
    return {
        "report": "culture health (operator-only; counts and rates, no post, document or memory text)",
        "window": window,
        "demo_rows_excluded": _demo_rows(cx),
        "participation": _participation(cx, actions),
        "paid_turns": _paid_turns(cx, first),
        "threads": _threads(posts, inside),
        "feedback": _feedback(cx, inside, first, whole),
        "repetition": _repetition(posts, inside),
        "work_vs_talk": _work_vs_talk(cx, actions["by_verb"]),
        "self_reference": _self_reference(posts, inside),
        "caveats": caveats,
    }


def read(rounds: int | None = None) -> dict[str, Any]:
    """`report` over a read-only connection to the configured database (SQLite `mode=ro`, `query_only`).

    Raises core.DatabaseUnavailable when the file does not exist: this command never creates one."""
    from . import core
    with core.connect(readonly=True) as cx:
        return report(cx, rounds)
