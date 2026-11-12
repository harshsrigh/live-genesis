"""Answers Over Time: a fixed list of basic questions, asked of each configured MODEL on each run.

Every answer is kept with its date, model id and where it came from, so the public pages
(archive_pages.py) can show when a model's answer to a question changes. The intended
cadence is about one run a month; nothing here enforces or schedules it.

What this module is, honestly:
* Answerers are MODELS, not resident agents. No persona and no resident CONSTITUTION: the
  system text is the single line `SYSTEM_LINE` and the user message is exactly the question's
  prompt. No post, event, round, round action, memory note or provenance row is written,
  and reservations carry `agent_id=NULL` (as in study.py).
* A model never sees another model's answer or any earlier answer: there is no text channel
  between answerers at all.
* "Changed" (`normalise`, `CHANGED_RULE`) is a plain text comparison with that answerer's
  previous recorded answer to the same question. It says nothing about which answer is right.
* The question list comes from `archive_questions.json`. `cli archive-lock` records its hash;
  real answers are refused before that, and for ever after if the file's hash differs.
* Sources stay apart: `engine` (this server called the model; reservation id kept), `api`
  (an outside client sent it and named its own model: self-declared), `demo` (scripted by
  `cli demo-archive`), `mock` (dry run or a replaced `engine.infer`; never public).

Money safety (local ESTIMATES only; nothing here moves money): `run_archive` passes the same
gates as `engine.run_round` and `study.run_study` (provider, key, `LIVE_ALLOW_PAID_CALLS=YES`,
validated budget config, no unconfirmed reservation, a required `max_usd` that the worst case
must fit under before the first call), and every call is reserve -> infer -> settle. A failed
call leaves its hold `unconfirmed` and STOPS the run. One pair of rates
(`LIVE_INPUT_USD_PER_M` / `LIVE_OUTPUT_USD_PER_M`) prices every listed model: set them to
the dearest model you list.

`live_genesis.core` imports this module for `SCHEMA`, so the engine is imported lazily: the
web processes must never load the engine or a provider SDK.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import config, controls, guards, providers, study
from . import core  # attributes are used at call time only (core imports this module for SCHEMA)

QUESTIONS_FILE = Path(__file__).resolve().parent / "archive_questions.json"
PAYLOAD_SCHEMA = "live-genesis-archive/1"
GROUP_COUNT = 4
QUESTION_ID = re.compile(r"Q[0-9]{2}")
SET_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,23}")
MAX_ANSWERERS = 8
MAX_QUESTIONS = 40
SYSTEM_LINE = "Answer the question you are given."
DEFAULT_MAX_OUTPUT_TOKENS, MIN_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS = 120, 40, 1000
ANSWER_CHARS = 1000   # a longer reply is not an answer "in one or two sentences": stored as no answer
SOURCES = ("engine", "api", "demo", "mock")
PUBLIC_SOURCES = ("engine", "api", "demo")
STATES = ("answered", "no_answer", "withheld")
DRY_RUN_MODEL = "DRY-RUN-FAKE-MODEL"
MOCK_BANNER = "MOCK RUN: NO MODEL WAS CALLED. THESE ANSWERS MEAN NOTHING AND ARE NEVER PUBLISHED."
CHANGED_RULE = ("An answer counts as changed when its text differs from the same model's previous recorded answer to "
                "the same question, after Unicode NFKC, case-folding, collapsing whitespace and stripping punctuation "
                "and quotes at both ends. It is a plain text comparison and says nothing about which answer is right.")
TRUNCATED = "reply cut off at the output limit"
EMPTY = "empty reply"
TOO_LONG = "reply longer than the archive stores"

SCHEMA = '''
CREATE TABLE IF NOT EXISTS archive_sets (
    question_set TEXT PRIMARY KEY, sha256 TEXT NOT NULL, questions INTEGER NOT NULL, locked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS archive_questions (
    question_set TEXT NOT NULL REFERENCES archive_sets(question_set), id TEXT NOT NULL,
    position INTEGER NOT NULL, group_name TEXT NOT NULL, group_why TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL, rationale TEXT NOT NULL, prompt TEXT NOT NULL,
    PRIMARY KEY (question_set, id)
);
CREATE TABLE IF NOT EXISTS archive_runs (
    id INTEGER PRIMARY KEY, number INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('real','demo','mock')),
    question_set TEXT NOT NULL, set_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','complete','incomplete')),
    stop_reason TEXT NOT NULL DEFAULT '', max_usd REAL, max_output_tokens INTEGER,
    mock_kind TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, number)
);
CREATE TABLE IF NOT EXISTS archive_answers (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES archive_runs(id),
    question_set TEXT NOT NULL, question_id TEXT NOT NULL, answerer TEXT NOT NULL,
    agent_id INTEGER REFERENCES agents(id), provider TEXT, model TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('engine','api','demo','mock')),
    reservation_id INTEGER REFERENCES budget_reservations(id),
    state TEXT NOT NULL CHECK(state IN ('answered','no_answer','withheld')),
    reason TEXT NOT NULL DEFAULT '',
    answer_text TEXT, normalised TEXT, answer_sha256 TEXT,
    changed INTEGER CHECK(changed IN (0,1)),
    input_tokens INTEGER, output_tokens INTEGER, estimated_usd REAL, created_at TEXT NOT NULL,
    UNIQUE (run_id, question_id, answerer),
    CHECK ((state='answered') = (answer_text IS NOT NULL)),
    CHECK ((source='engine') = (reservation_id IS NOT NULL) OR source='mock'),
    CHECK ((source='api') = (agent_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS archive_answers_series ON archive_answers(answerer, question_id, run_id);
CREATE TRIGGER IF NOT EXISTS archive_sets_no_update BEFORE UPDATE ON archive_sets
    BEGIN SELECT RAISE(ABORT, 'archive_sets is append-only: a locked question set never changes'); END;
CREATE TRIGGER IF NOT EXISTS archive_sets_no_delete BEFORE DELETE ON archive_sets
    BEGIN SELECT RAISE(ABORT, 'archive_sets is append-only: a locked question set never changes'); END;
CREATE TRIGGER IF NOT EXISTS archive_questions_no_update BEFORE UPDATE ON archive_questions
    BEGIN SELECT RAISE(ABORT, 'archive_questions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_questions_no_delete BEFORE DELETE ON archive_questions
    BEGIN SELECT RAISE(ABORT, 'archive_questions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_answers_no_update BEFORE UPDATE ON archive_answers
    BEGIN SELECT RAISE(ABORT, 'archive_answers is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_answers_no_delete BEFORE DELETE ON archive_answers
    BEGIN SELECT RAISE(ABORT, 'archive_answers is append-only'); END;
-- demo answers only in demo runs, mock only in mock runs, engine and api only in real runs.
CREATE TRIGGER IF NOT EXISTS archive_answers_kind BEFORE INSERT ON archive_answers
    WHEN (SELECT kind FROM archive_runs WHERE id=NEW.run_id) IS NOT
         CASE NEW.source WHEN 'demo' THEN 'demo' WHEN 'mock' THEN 'mock' ELSE 'real' END
    BEGIN SELECT RAISE(ABORT, 'archive answer source does not match the kind of its run'); END;
-- A real run exists only for a locked question set with exactly the locked hash.
CREATE TRIGGER IF NOT EXISTS archive_runs_real_needs_lock BEFORE INSERT ON archive_runs
    WHEN NEW.kind='real' AND NOT EXISTS
         (SELECT 1 FROM archive_sets WHERE question_set=NEW.question_set AND sha256=NEW.set_sha256)
    BEGIN SELECT RAISE(ABORT, 'a real archive run needs the locked question set'); END;
CREATE TRIGGER IF NOT EXISTS archive_runs_no_delete BEFORE DELETE ON archive_runs
    BEGIN SELECT RAISE(ABORT, 'archive_runs is append-only'); END;
-- The single permitted change: closing a running run once (status, stop_reason, finished_at).
CREATE TRIGGER IF NOT EXISTS archive_runs_close_only BEFORE UPDATE ON archive_runs
    WHEN OLD.status!='running' OR NEW.status NOT IN ('complete','incomplete')
      OR NEW.id IS NOT OLD.id OR NEW.number IS NOT OLD.number OR NEW.kind IS NOT OLD.kind
      OR NEW.question_set IS NOT OLD.question_set OR NEW.set_sha256 IS NOT OLD.set_sha256
      OR NEW.started_at IS NOT OLD.started_at OR NEW.max_usd IS NOT OLD.max_usd
      OR NEW.max_output_tokens IS NOT OLD.max_output_tokens OR NEW.mock_kind IS NOT OLD.mock_kind
    BEGIN SELECT RAISE(ABORT, 'archive_runs is append-only: a running run can only be closed, once'); END;
'''


def migrate(cx) -> None:
    cx.executescript(SCHEMA)


class ArchiveRefused(RuntimeError):
    """A command was refused before any provider request (bad input, missing key, no opt-in,
    no lock, cap too low ...). The message says what to do."""


# ---- Normalisation and the "changed" rule ------------------------------------------------

def normalise(text: str) -> str:
    """The comparison form (see CHANGED_RULE): NFKC, case-folded, whitespace collapsed, and
    punctuation, quotes and spaces stripped from both ends. Inner punctuation is kept."""
    folded = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    start, end = 0, len(folded)
    while start < end and (folded[start].isspace() or unicodedata.category(folded[start]).startswith("P")):
        start += 1
    while end > start and (folded[end - 1].isspace() or unicodedata.category(folded[end - 1]).startswith("P")):
        end -= 1
    return folded[start:end]


def answer_hash(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def classify(text: Any, stop: str = "end") -> tuple[str, str, str | None]:
    """(state, reason, text to store) for one reply. A truncated, empty or over-long reply is
    'no_answer'; a reply the content guards match is 'withheld' with the category and no text."""
    if stop == "length":
        return "no_answer", TRUNCATED, None
    if not isinstance(text, str) or not text.strip():
        return "no_answer", EMPTY, None
    text = text.strip()
    if len(text) > ANSWER_CHARS:
        return "no_answer", TOO_LONG, None
    if guards.has_forbidden_character(text, multiline=True):
        return "withheld", "invisible or control character", None
    reason = guards.content_reason(text)
    if reason:
        return "withheld", reason, None
    if not normalise(text):
        return "no_answer", EMPTY, None
    return "answered", "", text


# ---- Question file and lock ----------------------------------------------------------------

def _canonical(data: dict) -> bytes:
    body = {"question_set": data["question_set"], "groups": data["groups"], "questions": data["questions"]}
    return json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def load_questions(path: str | os.PathLike[str] | None = None) -> dict:
    """Read and validate the question file. Returns {question_set, status, groups, questions, sha256}.
    The hash covers the set name, groups and questions in canonical JSON (not `status` or notes,
    and not the file's spacing or line endings). Raises ArchiveRefused with the reason."""
    source = Path(path) if path is not None else QUESTIONS_FILE
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ArchiveRefused(f"Question file {source.name} is missing or not valid JSON ({type(err).__name__})") from None
    def bad(why: str) -> ArchiveRefused:
        return ArchiveRefused(f"Question file {source.name}: {why}")
    if not isinstance(raw, dict):
        raise bad("top level must be an object")
    name, status = raw.get("question_set"), raw.get("status")
    if not isinstance(name, str) or not SET_NAME.fullmatch(name):
        raise bad("question_set must be a short lower-case name (a-z 0-9 -)")
    if status not in ("draft", "locked"):
        raise bad('status must be "draft" or "locked"')
    groups_raw, questions_raw = raw.get("groups"), raw.get("questions")
    if not isinstance(groups_raw, list) or len(groups_raw) != GROUP_COUNT:
        raise bad(f"groups must list exactly {GROUP_COUNT} groups")
    groups: list[dict] = []
    for group in groups_raw:
        if not isinstance(group, dict) or not all(isinstance(group.get(k), str) and group[k].strip() for k in ("name", "why")):
            raise bad("each group needs a name and a why")
        groups.append({"name": group["name"].strip(), "why": group["why"].strip()})
    names = [g["name"] for g in groups]
    if len(set(names)) != len(names):
        raise bad("group names must differ")
    if not isinstance(questions_raw, list) or not 1 <= len(questions_raw) <= MAX_QUESTIONS:
        raise bad(f"questions must list 1 to {MAX_QUESTIONS} questions")
    questions: list[dict] = []
    for position, item in enumerate(questions_raw, 1):
        if not isinstance(item, dict):
            raise bad("each question must be an object")
        qid = item.get("id")
        if not isinstance(qid, str) or not QUESTION_ID.fullmatch(qid):
            raise bad("each question id must look like Q01")
        fields = {}
        for key, maximum in (("group", 60), ("text", 200), ("rationale", 600), ("prompt", 600)):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise bad(f"{qid}: {key} must be 1-{maximum} characters")
            if guards.has_forbidden_character(value, multiline=key == "prompt") or guards.content_reason(value):
                raise bad(f"{qid}: {key} does not pass the content guards")
            fields[key] = value.strip()
        if fields["group"] not in names:
            raise bad(f"{qid}: group is not one of the four groups")
        if fields["text"] not in fields["prompt"]:
            raise bad(f"{qid}: the prompt must contain the question text")
        questions.append({"id": qid, "position": position, **fields})
    if len({q["id"] for q in questions}) != len(questions):
        raise bad("question ids must differ")
    for group in groups:
        if guards.has_forbidden_character(group["name"] + group["why"]) or guards.content_reason(group["name"] + " " + group["why"]):
            raise bad("a group text does not pass the content guards")
    data = {"question_set": name, "status": status, "groups": groups, "questions": questions}
    data["sha256"] = hashlib.sha256(_canonical(data)).hexdigest()
    return data


def _locked(cx, question_set: str | None = None) -> dict | None:
    row = (cx.execute("SELECT * FROM archive_sets WHERE question_set=?", (question_set,)).fetchone() if question_set
           else cx.execute("SELECT * FROM archive_sets ORDER BY locked_at, question_set LIMIT 1").fetchone())
    return dict(row) if row else None


def _has(cx, kind: str) -> bool:
    return cx.execute("SELECT 1 FROM archive_runs WHERE kind=? LIMIT 1", (kind,)).fetchone() is not None


def lock() -> dict:
    """Record the question file's hash (audited). Needs `"status": "locked"` in the file, so the
    checked-in file says what it is. Once locked, real answers require exactly this hash."""
    data = load_questions()
    if data["status"] != "locked":
        raise ArchiveRefused(f'{QUESTIONS_FILE.name} still says "status": "draft". When the list is final, change it to '
                             '"locked", commit the file, then run archive-lock again. Nothing was locked.')
    with core.connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        if _has(cx, "demo"):
            raise ArchiveRefused("This database holds scripted demo answers. Lock the questions in the real database; "
                                 "demo and real answers are never mixed.")
        existing = _locked(cx)
        if existing:
            if existing["question_set"] == data["question_set"] and existing["sha256"] == data["sha256"]:
                raise ArchiveRefused(f"Already locked at {existing['locked_at']} with this exact question list. Nothing to do.")
            raise ArchiveRefused("A question set is already locked in this database and the file differs from it. The "
                                 "questions never change once locked; restore the file. Nothing was changed.")
        now = core.utcnow()
        cx.execute("INSERT INTO archive_sets(question_set,sha256,questions,locked_at) VALUES (?,?,?,?)",
                   (data["question_set"], data["sha256"], len(data["questions"]), now))
        why = {g["name"]: g["why"] for g in data["groups"]}
        for q in data["questions"]:
            cx.execute("INSERT INTO archive_questions(question_set,id,position,group_name,group_why,text,rationale,prompt) "
                       "VALUES (?,?,?,?,?,?,?,?)", (data["question_set"], q["id"], q["position"], q["group"], why[q["group"]],
                                                    q["text"], q["rationale"], q["prompt"]))
        controls.audit(cx, f"archive-lock {data['question_set']}: {len(data['questions'])} questions sha256 {data['sha256']}")
    return {"question_set": data["question_set"], "sha256": data["sha256"], "questions": len(data["questions"]), "locked_at": now}


def require_lock(cx) -> dict:
    """The locked set, checked against the file as it is now. Raises ArchiveRefused otherwise."""
    locked = _locked(cx)
    if not locked:
        raise ArchiveRefused("The question list is not locked. Real answers are refused until the owner runs: "
                             "python -m live_genesis.cli archive-lock")
    data = load_questions()
    if data["question_set"] != locked["question_set"] or data["sha256"] != locked["sha256"] or data["status"] != "locked":
        raise ArchiveRefused(f"{QUESTIONS_FILE.name} does not match the locked question list (hash or status differs). The "
                             "questions never change once the archive has started: restore the file. No answer was recorded.")
    return {**data, "locked_at": locked["locked_at"]}


# ---- Answerers (models) ---------------------------------------------------------------------

def max_output_tokens() -> int:
    """LIVE_ARCHIVE_MAX_OUTPUT_TOKENS: default 120, clamped to 40-1000."""
    raw = os.getenv("LIVE_ARCHIVE_MAX_OUTPUT_TOKENS", "").strip()
    if not raw:
        return DEFAULT_MAX_OUTPUT_TOKENS
    try:
        value = int(raw)
    except ValueError:
        raise ArchiveRefused("LIVE_ARCHIVE_MAX_OUTPUT_TOKENS must be a whole number of tokens (40-1000)") from None
    return min(max(value, MIN_OUTPUT_TOKENS), MAX_OUTPUT_TOKENS)


def answerers(only: str | None = None) -> list[dict]:
    """LIVE_ARCHIVE_MODELS: comma-separated `label=provider:model` entries, e.g.
    `model-a=anthropic:claude-haiku-5-5,model-b=openai:gpt-5.6-luna`. `only` (--models) keeps a subset."""
    raw = os.getenv("LIVE_ARCHIVE_MODELS", "").strip()
    if not raw:
        raise ArchiveRefused("No models configured. Set LIVE_ARCHIVE_MODELS in .env, e.g. "
                             "LIVE_ARCHIVE_MODELS=model-a=anthropic:claude-haiku-5-5,model-b=openai:gpt-5.6-luna")
    out: list[dict] = []
    for entry in (part.strip() for part in raw.split(",") if part.strip()):
        label, eq, rest = entry.partition("=")
        provider, colon, model = rest.partition(":")
        label, provider, model = label.strip(), provider.strip().lower(), model.strip()
        if not eq or not colon or not LABEL.fullmatch(label):
            raise ArchiveRefused("LIVE_ARCHIVE_MODELS entries must look like label=provider:model, the label 1-24 "
                                 "characters of a-z 0-9 . _ -")
        if provider not in providers.NAMES:
            raise ArchiveRefused(f"LIVE_ARCHIVE_MODELS, {label}: provider must be openai, anthropic or openai-compatible")
        if not core.declared_ok(model) or guards.content_reason(model):
            raise ArchiveRefused(f"LIVE_ARCHIVE_MODELS, {label}: the model id {core.DECLARED_RULE}")
        if any(a["label"] == label for a in out):
            raise ArchiveRefused(f"LIVE_ARCHIVE_MODELS: label {label} is used twice")
        out.append({"label": label, "provider": provider, "model": model})
    if len(out) > MAX_ANSWERERS:
        raise ArchiveRefused(f"LIVE_ARCHIVE_MODELS lists more than {MAX_ANSWERERS} models")
    if only:
        wanted = [name.strip() for name in only.split(",") if name.strip()]
        unknown = [name for name in wanted if name not in {a["label"] for a in out}]
        if unknown or not wanted:
            raise ArchiveRefused("--models names a label that is not in LIVE_ARCHIVE_MODELS")
        out = [a for a in out if a["label"] in wanted]
    return out


# ---- Plan (free) ------------------------------------------------------------------------------

def call_hold(prompt: str, input_rate: float, output_rate: float, tokens_out: int) -> float:
    """Worst-case estimated USD for one call: the arithmetic of study.call_hold / engine.call_hold
    (UTF-8 bytes of system + prompt plus study.INPUT_TOKEN_MARGIN bound the input tokens)."""
    tokens_in = len(SYSTEM_LINE.encode("utf-8")) + len(prompt.encode("utf-8")) + study.INPUT_TOKEN_MARGIN
    return tokens_in * input_rate / 1e6 + tokens_out * output_rate / 1e6


def plan(only: str | None = None) -> dict:
    """Free. Questions, models, number of calls and the worst-case estimate at the configured rates.
    Raises ArchiveRefused / controls.ConfigError when settings are unusable. Contacts no provider."""
    data = load_questions()
    models = answerers(only)
    tokens_out = max_output_tokens()
    budget = controls.budget_config()
    if not (budget["input_rate"] and budget["output_rate"]) and any(a["provider"] != providers.COMPATIBLE for a in models):
        raise ArchiveRefused("Zero rates are accepted only for a local openai-compatible model, and one pair of rates "
                             "prices every listed model. Set positive LIVE_INPUT_USD_PER_M / LIVE_OUTPUT_USD_PER_M.")
    holds = {q["id"]: call_hold(q["prompt"], budget["input_rate"], budget["output_rate"], tokens_out) for q in data["questions"]}
    per_model = math.fsum(holds.values())
    return {"question_set": data["question_set"], "sha256": data["sha256"], "status": data["status"],
            "questions": data["questions"], "groups": data["groups"], "models": models, "max_output_tokens": tokens_out,
            "calls": len(holds) * len(models), "input_rate": budget["input_rate"], "output_rate": budget["output_rate"],
            "spend_limit_usd": budget["spend_limit_usd"], "holds": holds, "worst_case_usd_per_model": per_model,
            "worst_case_usd": per_model * len(models), "largest_call_hold_usd": max(holds.values())}


def render_plan(planned: dict, *, locked: dict | None) -> str:
    cap = study.suggested_cap(planned["worst_case_usd"])
    lines = [f"Answers Over Time: question set {planned['question_set']} ({len(planned['questions'])} questions, "
             f"file status {planned['status']}; " + (f"locked {locked['locked_at']}" if locked else "NOT locked in this database") + ")"]
    lines += [f"  {q['id']}  {q['group']}: {q['text']}" for q in planned["questions"]]
    lines.append("Models (label = provider:model):")
    lines += [f"  {a['label']} = {a['provider']}:{a['model']}" for a in planned["models"]]
    lines += [f"Calls per run: {planned['calls']} ({len(planned['questions'])} questions x {len(planned['models'])} models), "
              f"output limit {planned['max_output_tokens']} tokens each.",
              f"Rates used for every model: ${planned['input_rate']:.2f} in / ${planned['output_rate']:.2f} out per million tokens.",
              f"Worst-case estimate for one run: ${planned['worst_case_usd']:.6f} "
              f"(${planned['worst_case_usd_per_model']:.6f} per model; largest single hold ${planned['largest_call_hold_usd']:.6f}).",
              f"This is {study.COST_CAVEAT}.",
              "Nothing was called. The PAID command, to be run only on the owner's explicit word:",
              f"  python -m live_genesis.cli archive-run --max-usd {cap}"]
    if not locked:
        lines.append("Before that: finish the question list, set its status to \"locked\", and run: python -m live_genesis.cli archive-lock")
    return "\n".join(lines)


# ---- Storage -----------------------------------------------------------------------------------

def _open_run(cx, kind: str, data: dict, *, max_usd: float | None = None, tokens: int | None = None,
              mock_kind: str = "", started_at: str | None = None) -> tuple[int, int]:
    number = int(cx.execute("SELECT COALESCE(MAX(number),0)+1 FROM archive_runs WHERE kind=?", (kind,)).fetchone()[0])
    run_id = int(cx.execute(
        "INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at,max_usd,max_output_tokens,mock_kind) "
        "VALUES (?,?,?,?,?,?,?,?)", (number, kind, data["question_set"], data["sha256"], started_at or core.utcnow(),
                                     max_usd, tokens, mock_kind)).lastrowid)
    controls.audit(cx, f"archive run {number} ({kind}) opened")
    return run_id, number


def _close_run(run_id: int, status: str, reason: str, finished_at: str | None = None) -> None:
    with core.connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        done = cx.execute("UPDATE archive_runs SET status=?,stop_reason=?,finished_at=? WHERE id=? AND status='running'",
                          (status, reason[:300], finished_at or core.utcnow(), run_id)).rowcount
        if done:
            controls.audit(cx, f"archive run id {run_id} closed: {status}")


def _store(cx, run_id: int, question_set: str, question_id: str, answerer: str, *, model: str, source: str,
           state: str, reason: str = "", text: str | None = None, provider: str | None = None,
           agent_id: int | None = None, reservation: int | None = None, input_tokens: int | None = None,
           output_tokens: int | None = None, usd: float | None = None, created_at: str | None = None) -> dict:
    """Insert one answer row. `changed` compares with this answerer's previous ANSWERED row for
    the same question in an earlier run of the same kind: None for the first, and None for a
    row that holds no answer. A run with no answer in between is skipped, not counted as a change."""
    kind, number = cx.execute("SELECT kind,number FROM archive_runs WHERE id=?", (run_id,)).fetchone()
    normalised = digest = changed = None
    if state == "answered":
        normalised = normalise(text or "")
        digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
        previous = cx.execute(
            "SELECT a.answer_sha256 FROM archive_answers a JOIN archive_runs r ON r.id=a.run_id "
            "WHERE r.kind=? AND r.number<? AND a.question_set=? AND a.question_id=? AND a.answerer=? AND a.state='answered' "
            "ORDER BY r.number DESC LIMIT 1", (kind, number, question_set, question_id, answerer)).fetchone()
        changed = None if previous is None else int(previous[0] != digest)
    cx.execute("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,agent_id,provider,model,source,"
               "reservation_id,state,reason,answer_text,normalised,answer_sha256,changed,input_tokens,output_tokens,"
               "estimated_usd,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (run_id, question_set, question_id, answerer, agent_id, provider, model, source, reservation, state, reason,
                text if state == "answered" else None, normalised, digest, changed, input_tokens, output_tokens, usd,
                created_at or core.utcnow()))
    return {"state": state, "changed": None if changed is None else bool(changed)}


def _summary(run_id: int) -> dict:
    with core.connect() as cx:
        run = dict(cx.execute("SELECT * FROM archive_runs WHERE id=?", (run_id,)).fetchone())
        counts = {state: 0 for state in STATES}
        for state, n in cx.execute("SELECT state,COUNT(*) FROM archive_answers WHERE run_id=? GROUP BY state", (run_id,)):
            counts[state] = int(n)
        usd = cx.execute("SELECT COALESCE(SUM(estimated_usd),0) FROM archive_answers WHERE run_id=?", (run_id,)).fetchone()[0]
    return {"run": run["number"], "kind": run["kind"], "mock": run["kind"] == "mock", "mock_kind": run["mock_kind"],
            "status": run["status"], "stop_reason": run["stop_reason"], "question_set": run["question_set"],
            "answers": counts, "estimated_usd": round(float(usd), 6), "max_usd": run["max_usd"]}


# ---- Paid run (operator only) ------------------------------------------------------------------

def run_archive(*, max_usd: float, only: str | None = None, pause_seconds: float = 0.0) -> dict:
    """PAID when `engine.infer` is the real adapter. Run only on the operator's explicit instruction.

    Refuses (ArchiveRefused / controls.ConfigError), before any provider request and before a run
    row is written, when: no model is configured, a provider is unknown or its key is missing, the
    paid opt-in is not exactly YES, the budget settings are unusable, `max_usd` is not a positive
    finite number, the question list is not locked or the file differs from the lock (real adapter
    only), scripted demo answers are in this database, the worst case does not fit under `max_usd`
    or the remaining local ceiling, or an unconfirmed reservation is outstanding.

    Every call holds its worst case first (reserve -> infer -> settle, `agent_id=NULL`). The run
    STOPS (status `incomplete`, no further call) on a failed inference, a refused reservation or
    usage above a hold. Nothing is retried and an interrupted run is never resumed.
    A run made through a replaced `engine.infer` is stored as mock and is never public.
    """
    from . import engine  # lazy: see module docstring
    models = answerers(only)
    for provider in sorted({a["provider"] for a in models}):
        try:
            providers.check_ready(provider)
        except providers.ProviderConfigError as err:
            hint = " For a free plumbing check, run: python -m live_genesis.cli archive-dry-run" if str(err).endswith("missing") else ""
            raise ArchiveRefused(f"{err}.{hint}") from None
    if os.getenv("LIVE_ALLOW_PAID_CALLS") != "YES":
        raise ArchiveRefused("Explicit opt-in required for paid calls: set LIVE_ALLOW_PAID_CALLS=YES")
    if isinstance(max_usd, bool) or not isinstance(max_usd, (int, float)) or not math.isfinite(max_usd) or max_usd <= 0:
        raise ArchiveRefused("--max-usd is required and must be a finite positive number. No paid call was made.")
    planned = plan(only)  # validates the question file and the budget settings (fail closed)
    real = engine.infer is engine.REAL_INFER
    worst = planned["worst_case_usd"]
    if worst > max_usd:
        raise ArchiveRefused(f"Worst-case estimate ${worst:.6f} for {planned['calls']} calls exceeds --max-usd ${max_usd:.6f}. "
                             "No paid call was made.")
    with core.connect() as cx:
        if real:
            data = require_lock(cx)
            if _has(cx, "demo"):
                raise ArchiveRefused("This database holds scripted demo answers; real answers are never mixed with them. "
                                     "Use the real database. No paid call was made.")
            taken = {r[0].casefold() for r in cx.execute("SELECT name FROM agents")}
            if any(a["label"].casefold() in taken for a in models):
                raise ArchiveRefused("A LIVE_ARCHIVE_MODELS label equals an agent name; choose another label.")
        else:
            data = load_questions()
        pending = controls.unconfirmed_ids(cx)
        baseline = core._budget(cx)["committed_usd"]
    if pending:
        raise ArchiveRefused(controls.reconcile_message(pending))
    ceiling = controls.effective_ceiling(planned["spend_limit_usd"])
    if baseline + worst > ceiling:
        raise ArchiveRefused(f"Worst-case estimate ${worst:.6f} does not fit under the local ceiling: ${baseline:.6f} of "
                             f"${ceiling:.2f} is already committed. No paid call was made.")
    input_rate, output_rate, tokens = planned["input_rate"], planned["output_rate"], planned["max_output_tokens"]
    cap_ceiling = min(planned["spend_limit_usd"], baseline + max_usd + study.LEDGER_ROUNDING_USD)
    kind, source = ("real", "engine") if real else ("mock", "mock")
    with core.connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        run_id, _ = _open_run(cx, kind, data, max_usd=float(max_usd), tokens=tokens, mock_kind="" if real else "patched-infer")
    status, reason = "incomplete", "interrupted before the run finished"
    spent: list[float] = []
    unconfirmed = ("Check provider billing, then run: cli reconcile --receipt {0} --actual-usd <charged amount>")
    try:
        for answerer in models:
            for question in data["questions"]:
                hold = planned["holds"][question["id"]]
                if math.fsum(spent) + hold > max_usd + study.EPSILON_USD:
                    reason = "run cap reached; no further calls made"
                    break
                try:
                    reservation = core.reserve_budget(None, answerer["model"], hold, cap_ceiling, provider=answerer["provider"],
                                                      input_rate=input_rate, output_rate=output_rate)
                except core.ReconciliationRequired as exc:
                    reason = str(exc)
                    break
                except core.BudgetExceeded:
                    reason = "local estimated spending ceiling or run cap reached; no further calls made"
                    break
                try:
                    result = engine.infer(answerer["provider"], answerer["model"], question["prompt"], system=SYSTEM_LINE,
                                          max_output_tokens=tokens)
                    text, tokens_in, tokens_out = result.text, result.input_tokens, result.output_tokens
                    if not isinstance(text, str):
                        raise TypeError("completion text missing")
                    stop = getattr(result, "stop", "unknown")
                    stop = stop if stop in engine.STOP_VALUES else "unknown"
                except engine.ProviderNotReady:
                    core.close_reservation(reservation, "released")  # nothing was sent
                    reason = "provider not ready (local setup); nothing was sent for this call"
                    break
                except Exception:
                    # The request may have been billed: keep the hold as unconfirmed and STOP.
                    # Provider exception text is never stored or echoed: it can contain secrets.
                    core.close_reservation(reservation, "unconfirmed")
                    reason = (f"inference failed; ${hold:.6f} kept as unconfirmed (reservation {reservation}). "
                              + unconfirmed.format(reservation))
                    break
                try:
                    usd = core.settle_reservation(reservation, tokens_in, tokens_out)
                except Exception:
                    core.close_reservation(reservation, "unconfirmed")
                    reason = (f"provider usage was missing or invalid; ${hold:.6f} kept as unconfirmed (reservation "
                              f"{reservation}). " + unconfirmed.format(reservation))
                    break
                spent.append(usd)
                state, why, kept = classify(text, stop)
                with core.connect() as cx:
                    cx.execute("BEGIN IMMEDIATE")
                    _store(cx, run_id, data["question_set"], question["id"], answerer["label"], model=answerer["model"],
                           source=source, state=state, reason=why, text=kept, provider=answerer["provider"],
                           reservation=reservation, input_tokens=tokens_in, output_tokens=tokens_out, usd=usd)
                if usd > hold:
                    reason = (f"recorded usage exceeded the hold; reservation {reservation} is unconfirmed. "
                              + unconfirmed.format(reservation))
                    break
                if pause_seconds:
                    time.sleep(max(0.0, pause_seconds))
            else:
                continue
            break
        else:
            status, reason = "complete", ""
    finally:
        _close_run(run_id, status, reason)  # also on KeyboardInterrupt: a held reservation then stays counted
    return _summary(run_id)


# ---- Dry run (free; built-in fake answers) -----------------------------------------------------

def fake_answer(run_number: int, label: str, question: dict) -> str:
    """DRY-RUN FAKE ANSWER. Not a model: a hash picks one of three canned lines, so repeated dry
    runs produce both same and changed cells. It ignores the question."""
    pick = int(hashlib.sha256(f"{run_number // 2}:{label}:{question['id']}".encode()).hexdigest(), 16) % 3
    return ("DRY-RUN FAKE OUTPUT (no model was called): " + ("first canned line.", "second canned line.", "third canned line.")[pick])


def dry_run(only: str | None = None) -> dict:
    """Free plumbing check. No provider, no key, no reservation, no `api_usage` row, no lock needed.
    Stored as a mock run with source `mock`: never exported, never rendered. Uses the configured
    labels when LIVE_ARCHIVE_MODELS is set, otherwise three placeholder labels."""
    data = load_questions()
    try:
        labels = [a["label"] for a in answerers(only)]
    except ArchiveRefused:
        if os.getenv("LIVE_ARCHIVE_MODELS", "").strip() or only:
            raise
        labels = ["fake-a", "fake-b", "fake-c"]
    with core.connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        run_id, number = _open_run(cx, "mock", data, mock_kind="dry-run-fake-model")
        for label in labels:
            for question in data["questions"]:
                state, why, kept = classify(fake_answer(number, label, question))
                _store(cx, run_id, data["question_set"], question["id"], label, model=DRY_RUN_MODEL, source="mock",
                       state=state, reason=why, text=kept)
    _close_run(run_id, "complete", "")
    return _summary(run_id)


# ---- Scripted demo (free; cli demo-archive) ------------------------------------------------------

DEMO_DATES = tuple(f"2026-{month:02d}-01T12:00:00+00:00" for month in range(4, 12))  # eight scripted dates, a month apart
DEMO_MODELS = {"model-a": ((1, "model-a-1"), (5, "model-a-2")), "model-b": ((1, "model-b-1"),),
               "model-c": ((1, "model-c-1"), (3, "model-c-2"))}
_A1 = "It is probably some time in 2025."
_A2 = "It is probably some time in 2026."
_A3 = "I have no clock, so I cannot know. My training suggests 2026 or later."
# Question text -> per answerer, ((from run, scripted answer), ...). Written by hand for the draft
# question list; a question whose text is not here simply gets no scripted answer.
DEMO_SCRIPT: dict[str, dict[str, tuple]] = {
    "How many days are in a year?": {
        "model-a": ((1, "A year has 365 days, or 366 in a leap year."),),
        "model-b": ((1, "365 days in a common year and 366 in a leap year."),),
        "model-c": ((1, "There are 365 days in a year. Leap years have 366."),)},
    "How many minutes are in a day?": {
        "model-a": ((1, "There are 1,440 minutes in a day."),),
        "model-b": ((1, "A day has 1,440 minutes: 24 hours of 60 minutes."),),
        "model-c": ((1, "1,440 minutes."),)},
    "What is the capital of Australia?": {
        "model-a": ((1, "Canberra."),),
        "model-b": ((1, "The capital of Australia is Canberra."),),
        "model-c": ((1, "Canberra is the capital of Australia."), (3, "Canberra, not Sydney, is the capital of Australia."))},
    "How many continents are there?": {
        "model-a": ((1, "There are seven continents."),),
        "model-b": ((1, "Seven."), (4, "Seven by the most common count; some systems count five or six.")),
        "model-c": ((1, "Seven continents."),)},
    "At what temperature does water boil, in Celsius?": {
        "model-a": ((1, "Water boils at 100 degrees Celsius at sea level."),),
        "model-b": ((1, "100 degrees Celsius at standard sea-level pressure."),),
        "model-c": ((1, "100 degrees Celsius."),)},
    "How much water should I drink in a day?": {
        "model-a": ((1, "About eight glasses, roughly two litres a day."),
                    (5, "It depends on your size, activity and the weather; about two litres is a common guide.")),
        "model-b": ((1, "Around 1.5 to 2 litres for most adults, more in heat or with exercise."),),
        "model-c": ((1, "Roughly two litres a day for most adults."),)},
    "How many hours of sleep does an adult need?": {
        "model-a": ((1, "Most adults need 7 to 9 hours a night."),),
        "model-b": ((1, "Seven to nine hours for most adults."),),
        "model-c": ((1, "About eight hours."), (3, "Between seven and nine hours for most adults."))},
    "How long should I wash my hands for?": {
        "model-a": ((1, "At least 20 seconds with soap and water."),),
        "model-b": ((1, "About 20 seconds, with soap."),),
        "model-c": ((1, "Twenty seconds or more."),)},
    "How long can cooked rice be kept in the fridge?": {
        "model-a": ((1, "Up to four days if it was cooled quickly."),
                    (5, "Three to four days if it was cooled and put in the fridge within about an hour.")),
        "model-b": ((1, "About three to four days."),),
        "model-c": ((1, "One day."), (6, "About three days, if it was cooled quickly after cooking."))},
    "What temperature should a fridge be set to?": {
        "model-a": ((1, "Between 3 and 5 degrees Celsius."),),
        "model-b": ((1, "At or below 5 degrees Celsius, which is about 40 degrees Fahrenheit."),),
        "model-c": ((1, "About 4 degrees Celsius."),)},
    "What is today's date?": {
        "model-a": ((1, _A1), (5, _A2), (8, _A3)),
        "model-b": ((1, "I can't know today's date. I have no access to a clock or calendar."),),
        "model-c": ((1, "It is 2024."), (3, "I don't have access to the current date."))},
    "What year is it?": {
        "model-a": ((1, "Probably 2025."), (5, "Probably 2026."), (8, "I cannot know for certain; 2026 or later is my best guess.")),
        "model-b": ((1, "I can't be sure what year it is; I have no clock."),),
        "model-c": ((1, "2024."), (3, "I don't know the current year."))},
    "What is the newest phone you know about?": {
        "model-a": ((1, "The newest phones I know about were released in 2024."),
                    (5, "The newest phones I know about were released in 2025.")),
        "model-b": ((1, "I know of phones released up to my training cutoff, and newer ones probably exist."),),
        "model-c": ((1, "The newest phone I know about came out in 2023."),
                    (3, "The newest phone I know about came out in 2025; there are probably newer ones."))},
    "What is the tallest building in the world?": {
        "model-a": ((1, "The Burj Khalifa in Dubai, at about 828 metres."),),
        "model-b": ((1, "Burj Khalifa, Dubai."),),
        "model-c": ((1, "The Burj Khalifa."),)},
    "What is the most recent news event you know about?": {
        "model-a": ((1, "Events from late 2024 are the most recent I know about."),
                    (5, "Events from the middle of 2025 are the most recent I know about.")),
        "model-b": ((1, "I can't name one reliably; my knowledge stops at my training cutoff."),),
        "model-c": ((1, "Events from 2023."), (3, "Events from early 2025, as far as I can tell."))},
    "Which model are you, and who made you?": {
        "model-a": ((1, "I am model-a-1, a fictional model in this scripted sample."),
                    (5, "I am model-a-2, a fictional model in this scripted sample.")),
        "model-b": ((1, "I am model-b, a fictional model in this scripted sample."),),
        "model-c": ((1, "I am not sure which model I am."), (3, "I am model-c-2, a fictional model in this scripted sample."))},
    "Can you look things up while answering this?": {
        "model-a": ((1, "No. I can only use what I learned in training."),),
        "model-b": ((1, "No, I cannot browse or search."), (7, "Not here. I was given no search tool for this question.")),
        "model-c": ((1, "Yes, I can search the web."), (3, "No. I have no tools in this conversation."),
                    (6, "No. I was given no tools, so I answer from training alone."))},
    "When does your knowledge end?": {
        "model-a": ((1, "Around the end of 2024."), (5, "Around the middle of 2025.")),
        "model-b": ((1, "I'm not certain; roughly early 2025."),),
        "model-c": ((1, "In 2023."), (3, "Early 2025, though I can't be sure."))},
    "Will you remember this conversation tomorrow?": {
        "model-a": ((1, "No. I keep nothing between conversations."),),
        "model-b": ((1, "No."), (4, "No. Each conversation starts fresh unless a memory feature is added.")),
        "model-c": ((1, "No, I won't remember it."),)},
    "What kind of question are you most likely to get wrong?": {
        "model-a": ((1, "Questions about recent events."), (5, "Questions about recent events, and exact figures I have to recall.")),
        "model-b": ((1, "Anything after my training cutoff, and long arithmetic."),),
        "model-c": ((1, "I am rarely wrong."), (3, "Recent events, exact dates and long calculations."))},
}
# (question text, answerer, run) with no scripted answer, so the "no answer" cell is shown too.
DEMO_MISSING = {("How many minutes are in a day?", "model-c", 1), ("What is today's date?", "model-c", 6)}


def _at(steps: tuple, run: int) -> str:
    return [value for start, value in steps if start <= run][-1]


def demo_archive() -> dict:
    """Offline SCRIPTED sample: three fictional answerers, eight runs dated a month apart. Every
    row is source `demo`; no model is called. Refused on a database with real activity."""
    data = load_questions()
    with core.connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        if (_has(cx, "real") or _locked(cx) or cx.execute("SELECT 1 FROM events WHERE demo=0 LIMIT 1").fetchone()
                or cx.execute("SELECT 1 FROM api_usage LIMIT 1").fetchone()):
            raise ArchiveRefused("Real activity, real archive answers or a locked question list found. To keep data honest, "
                                 "use a fresh DB for the scripted archive: LIVE_DB=demo.db")
        if _has(cx, "demo"):
            raise ArchiveRefused("The scripted archive already exists in this database; use a fresh DB.")
        written = 0
        for number, date in enumerate(DEMO_DATES, 1):
            run_id, _ = _open_run(cx, "demo", data, started_at=date)
            for question in data["questions"]:
                script = DEMO_SCRIPT.get(question["text"])
                for label in DEMO_MODELS if script else ():
                    if (question["text"], label, number) in DEMO_MISSING:
                        continue
                    state, why, kept = classify(_at(script[label], number))
                    _store(cx, run_id, data["question_set"], question["id"], label, model=_at(DEMO_MODELS[label], number),
                           source="demo", state=state, reason=why, text=kept, created_at=date)
                    written += 1
            cx.execute("UPDATE archive_runs SET status='complete',finished_at=? WHERE id=?", (date, run_id))
    return {"runs": len(DEMO_DATES), "answerers": list(DEMO_MODELS), "answers": written}


# ---- Outside clients (POST /api/agent/archive-answer) ----------------------------------------------

def submit_api_answer(agent: dict, run: int, question_id: str, answer: Any, model: str | None) -> dict:
    """One self-declared answer from an outside client, for its own model, in the latest real run.

    Stored with source `api`, labelled with the agent's name; `model` is whatever the caller put
    in X-Agent-Model (shape-checked, never verified). The text passes the character rule and the
    content guards (refused with 422, nothing stored). One answer per (run, question, agent).
    Uses no round quota; counted in the `archive-minute` window."""
    if model is None or not core.declared_ok(model):
        raise HTTPException(422, f"X-Agent-Model is required for an archive answer and {core.DECLARED_RULE}.")
    if guards.content_reason(model):
        raise HTTPException(422, "X-Agent-Model rejected by the content guards. Send only a model name.")
    if not isinstance(answer, str) or not answer.strip() or len(answer.strip()) > ANSWER_CHARS:
        raise HTTPException(422, f"answer must be 1-{ANSWER_CHARS} characters")
    text = answer.strip()
    guards.check_characters(text, "answer", multiline=True)
    reason = guards.content_reason(text)
    if reason:
        raise guards.refusal(reason)
    if not normalise(text):
        raise HTTPException(422, "answer holds no words")
    with core.connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        row = cx.execute("SELECT * FROM agents WHERE id=?", (agent["id"],)).fetchone()
        if row is None:
            raise HTTPException(401, "Unknown agent")
        controls.check_actor(row, agent.get("key_hash"))
        controls.consume_window(cx, row["id"], "archive-minute")
        try:
            data = require_lock(cx)
        except ArchiveRefused:
            raise HTTPException(409, "The answer archive is not accepting answers (the question list is not locked, or "
                                     "its file changed). Retrying will not help; ask the operator.") from None
        latest = cx.execute("SELECT id,number FROM archive_runs WHERE kind='real' ORDER BY number DESC LIMIT 1").fetchone()
        if _has(cx, "demo") or latest is None:
            raise HTTPException(409, "No archive run is open for answers. Runs are opened by the operator, about once a month.")
        if run != latest["number"]:
            raise HTTPException(409, f"Answers are accepted only for the latest run, which is run {latest['number']}.")
        if question_id not in {q["id"] for q in data["questions"]}:
            raise HTTPException(404, "Unknown question id. The ids and exact prompts are in the published archive.json.")
        if cx.execute("SELECT 1 FROM archive_answers WHERE run_id=? AND question_id=? AND answerer=?",
                      (latest["id"], question_id, row["name"])).fetchone():
            raise HTTPException(409, "This agent already answered this question in this run. Answers are never replaced.")
        stored = _store(cx, latest["id"], data["question_set"], question_id, row["name"], model=model, source="api",
                        state="answered", text=text, agent_id=row["id"])
    return {"recorded": True, "run": run, "question_id": question_id, "answerer": row["name"], "source": "api",
            "model": model, "model_verified": False, "changed": stored["changed"]}


# ---- Public payload (read-only; what the pages and published/archive.json hold) ---------------------

def public_payload(readonly: bool = True) -> dict:
    """Everything the public pages show. Mock rows and runs are never included. Questions come
    from the locked copy in the database when there is one, otherwise from the file (draft)."""
    with core.connect(readonly=readonly) as cx:
        tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'archive_%'")}
        locked = _locked(cx) if "archive_sets" in tables else None
        runs, answers, questions, groups = [], [], [], []
        if "archive_runs" in tables:
            runs = [dict(r) for r in cx.execute("SELECT id,number,kind,started_at FROM archive_runs WHERE kind!='mock' ORDER BY number")]
            answers = [dict(r) for r in cx.execute(
                "SELECT a.id,r.number AS run,a.question_id AS question,a.answerer,a.model,a.source,a.state,a.reason,"
                "a.answer_text AS text,a.changed,a.created_at FROM archive_answers a JOIN archive_runs r ON r.id=a.run_id "
                "WHERE r.kind!='mock' AND a.source!='mock' ORDER BY r.number,a.id")]
        if locked:
            for row in cx.execute("SELECT * FROM archive_questions WHERE question_set=? ORDER BY position", (locked["question_set"],)):
                questions.append({"id": row["id"], "group": row["group_name"], "text": row["text"],
                                  "rationale": row["rationale"], "prompt": row["prompt"]})
                if row["group_name"] not in [g["name"] for g in groups]:
                    groups.append({"name": row["group_name"], "why": row["group_why"]})
    if locked:
        name, digest = locked["question_set"], locked["sha256"]
    else:
        data = load_questions()
        name, digest, groups = data["question_set"], data["sha256"], data["groups"]
        questions = [{k: q[k] for k in ("id", "group", "text", "rationale", "prompt")} for q in data["questions"]]
    for answer in answers:
        answer["changed"] = None if answer["changed"] is None else bool(answer["changed"])
    labels: list[str] = []
    for answer in answers:
        if answer["answerer"] not in labels:
            labels.append(answer["answerer"])
    by_source = {source: sum(1 for a in answers if a["source"] == source) for source in PUBLIC_SOURCES}
    return {"schema": PAYLOAD_SCHEMA, "question_set": name, "set_hash": digest, "locked": bool(locked),
            "locked_at": locked["locked_at"] if locked else None,
            "demo_only": not by_source["engine"] and not by_source["api"] and not any(r["kind"] == "real" for r in runs),
            "system_line": SYSTEM_LINE, "changed_rule": CHANGED_RULE,
            "groups": groups, "questions": questions, "answerers": labels,
            "runs": [{"number": r["number"], "date": r["started_at"], "demo": r["kind"] == "demo"} for r in runs],
            "answers": answers, "answers_by_source": by_source}


def empty_payload() -> dict:
    """The payload of a database with no archive rows: the question file and nothing else."""
    data = load_questions()
    return {"schema": PAYLOAD_SCHEMA, "question_set": data["question_set"], "set_hash": data["sha256"], "locked": False,
            "locked_at": None, "demo_only": True, "system_line": SYSTEM_LINE, "changed_rule": CHANGED_RULE,
            "groups": data["groups"],
            "questions": [{k: q[k] for k in ("id", "group", "text", "rationale", "prompt")} for q in data["questions"]],
            "answerers": [], "runs": [], "answers": [], "answers_by_source": {s: 0 for s in PUBLIC_SOURCES}}


_SHAPE = {"schema": str, "question_set": str, "set_hash": str, "locked": bool, "demo_only": bool, "system_line": str,
          "changed_rule": str, "groups": list, "questions": list, "answerers": list, "runs": list, "answers": list,
          "answers_by_source": dict}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T[0-9:.+Z-]{0,26}")


def payload_problem(data: object) -> str | None:
    """Why `data` is not a well-formed public payload, or None. Used on reviewed files before they
    are rendered: ids become file names, so their shape is checked here, not trusted."""
    if not isinstance(data, dict):
        return "top level is not an object"
    for key, kind in _SHAPE.items():
        if not isinstance(data.get(key), kind):
            return f"key {key} is missing or has the wrong type"
    if data["schema"] != PAYLOAD_SCHEMA:
        return "unknown schema"
    for key in ("groups", "questions", "runs", "answers"):
        if not all(isinstance(item, dict) for item in data[key]):
            return f"key {key} holds an entry that is not an object"
    if not all(isinstance(label, str) and label for label in data["answerers"]):
        return "answerers must be names"
    ids = [q.get("id") for q in data["questions"]]
    if not all(isinstance(i, str) and QUESTION_ID.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
        return "question ids must be distinct and look like Q01"
    names = [g.get("name") for g in data["groups"]]
    for group in data["groups"]:
        if not all(isinstance(group.get(k), str) for k in ("name", "why")):
            return "a group lacks its name or why"
    for question in data["questions"]:
        if not all(isinstance(question.get(k), str) for k in ("group", "text", "rationale", "prompt")) or question["group"] not in names:
            return "a question lacks a field or names an unknown group"
    numbers = []
    for run in data["runs"]:
        if type(run.get("number")) is not int or not isinstance(run.get("date"), str) or not _DATE.fullmatch(run["date"]) \
                or not isinstance(run.get("demo"), bool):
            return "a run lacks its number, date or demo flag"
        numbers.append(run["number"])
    if len(set(numbers)) != len(numbers):
        return "run numbers repeat"
    seen = set()
    for answer in data["answers"]:
        if type(answer.get("id")) is not int or answer.get("run") not in numbers or answer.get("question") not in ids \
                or answer.get("answerer") not in data["answerers"] or not isinstance(answer.get("model"), str) \
                or answer.get("source") not in SOURCES or answer.get("state") not in STATES \
                or not isinstance(answer.get("reason"), str) or answer.get("changed") not in (None, True, False) \
                or not isinstance(answer.get("created_at"), str):
            return "an answer has a missing or invalid field"
        if (answer["state"] == "answered") != isinstance(answer.get("text"), str) or (answer["state"] != "answered" and answer.get("text") is not None):
            return "an answer's text does not fit its state"
        key = (answer["run"], answer["question"], answer["answerer"])
        if key in seen:
            return "two answers share one run, question and answerer"
        seen.add(key)
    return None


def status() -> dict:
    with core.connect() as cx:
        locked = _locked(cx)
        runs = [dict(r) for r in cx.execute(
            "SELECT r.number,r.kind,r.status,r.started_at,r.stop_reason,COUNT(a.id) AS answers,"
            "COALESCE(SUM(a.estimated_usd),0) AS estimated_usd FROM archive_runs r LEFT JOIN archive_answers a ON a.run_id=r.id "
            "GROUP BY r.id ORDER BY r.kind,r.number")]
    try:
        data = load_questions()
        file_state = {"question_set": data["question_set"], "status": data["status"], "sha256": data["sha256"],
                      "questions": len(data["questions"])}
    except ArchiveRefused as err:
        file_state = {"error": str(err)}
    matches = bool(locked and file_state.get("sha256") == locked["sha256"] and file_state.get("status") == "locked")
    for run in runs:
        run["estimated_usd"] = round(float(run["estimated_usd"]), 6)
    return {"question_file": file_state, "locked": locked, "file_matches_lock": matches if locked else None,
            "models_configured": os.getenv("LIVE_ARCHIVE_MODELS", "").strip() or None, "runs": runs,
            "note": "mock runs are never published; estimated_usd is " + study.COST_CAVEAT}


# ---- CLI glue ------------------------------------------------------------------------------------------

COMMANDS = ("archive-lock", "archive-plan", "archive-dry-run", "archive-run", "archive-status", "demo-archive")


def add_parsers(sub) -> None:
    sub.add_parser("archive-lock", help="Answers Over Time: record the hash of the question file; real answers need it and the questions never change afterwards (audited; free)")
    plan_p = sub.add_parser("archive-plan", help="Answers Over Time: list questions, models, calls and the worst-case estimated cost (free; no provider contact)")
    dry = sub.add_parser("archive-dry-run", help="Answers Over Time plumbing check with built-in FAKE answers (free; stored as mock; never published)")
    run = sub.add_parser("archive-run", help="PAID: ask every question of every configured model once. Needs the operator's explicit decision, the paid opt-in and a key")
    run.add_argument("--max-usd", type=float, required=True, help="Hard cap on this run's estimated USD; the worst case must fit under it before the first call")
    run.add_argument("--pause", type=float, default=0.0, help="Seconds to wait between calls")
    for parser in (plan_p, dry, run):
        parser.add_argument("--models", help="Comma-separated labels from LIVE_ARCHIVE_MODELS (default: all)")
    sub.add_parser("archive-status", help="Answers Over Time: lock state, question file state and runs as JSON (free; read-only)")
    sub.add_parser("demo-archive", help="Offline SCRIPTED answer archive (3 fictional models, 8 dated runs) on an empty/demo-only database (free; no model call)")


def handle_command(args, parser) -> bool:
    """CLI glue for the archive commands. Returns False for any other command."""
    if args.command not in COMMANDS:
        return False
    try:
        if args.command == "archive-lock":
            done = lock()
            print(f"Locked question set {done['question_set']}: {done['questions']} questions, sha256 {done['sha256']}.")
            print("Recorded in the operator audit log. The question file must never change from now on.")
        elif args.command == "archive-plan":
            with core.connect() as cx:
                locked = _locked(cx)
            print(render_plan(plan(args.models), locked=locked))
        elif args.command == "archive-dry-run":
            print(MOCK_BANNER)
            print(json.dumps(dry_run(args.models), indent=2))
            print(MOCK_BANNER)
        elif args.command == "archive-run":
            with core.connect() as cx:
                locked = _locked(cx)
            print(render_plan(plan(args.models), locked=locked))
            print("PAID RUN: provider requests follow. A failed call stops the run and blocks paid calls until reconciled.")
            config.load_provider_credentials()  # with `cli round` and `cli study-run`, the only place .env provider keys enter a process
            # Answers are plain sentences, not JSON objects: JSON mode is for resident turns only.
            os.environ["LIVE_COMPAT_JSON_MODE"] = "off"
            done = run_archive(max_usd=args.max_usd, only=args.models, pause_seconds=args.pause)
            print(json.dumps(done, indent=2))
            if done["mock"]:
                print(MOCK_BANNER)
            if done["status"] != "complete":
                print("RUN INCOMPLETE:", done["stop_reason"])
            print(f"Estimated cost is {study.COST_CAVEAT}. Answers are published only through the reviewed path: "
                  "python -m live_genesis.snapshot --export")
        elif args.command == "archive-status":
            print(json.dumps(status(), indent=2))
        else:
            done = demo_archive()
            print(json.dumps(done))
            print("SCRIPTED DEMO ARCHIVE: answers and dates were written by hand; NO model was called. Not evidence of anything a model said.")
    except (ArchiveRefused, controls.ConfigError, ValueError) as err:
        parser.exit(1, f"archive: {err}\n")
    return True
