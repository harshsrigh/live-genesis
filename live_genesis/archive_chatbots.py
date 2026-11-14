"""Answers Over Time, the chat-app track: the same questions typed into consumer chat apps BY HAND.

What this module is, honestly:
* The OWNER opens each chat app, starts a new chat, pastes the question's exact prompt and copies the
  reply into a file. `cli archive-chatbot-template` writes that file to fill in; `cli
  archive-chatbot-import` checks it and stores it. Nothing here contacts a chat app, a model or the
  network, and nothing here may ever be made to: no automated collection of any kind.
* A hand-entered reply cannot be verified. There is no receipt, no token count and no model id beyond
  the name the app displayed. Pages and exports label every such reply "entered by hand".
* The questions are ids that REFER to locked questions of the two existing sets
  (`archive_chatbots.json`); the text and the exact prompt come from there and are never copied.
* Storage is its own three append-only tables (`archive_chatbot_runs`, `archive_chatbot_conditions`,
  `archive_chatbot_answers`), apart from the model answers in `archive_answers`: a hand-entered run is
  never a row of a model grid or strip and never takes part in a model's `changed`.
* One import is one run dated `collected_on` (the day the owner asked). It holds one or more chat apps;
  per (run, chat app) the CONDITIONS the owner recorded (account, temporary chat, memory, search,
  country, app, the model name shown) and one answer per question. `changed` compares a reply with that
  chat app's previous hand-entered answer to the same question, by `archive.normalise`.
* A chat app's dates only move forward: an import for a date that is not later than that app's latest
  recorded date is refused (the same date twice included), so the stored `changed` flags stay true.
* The ten questions: until the first real import the owner may change the list; after it an id that has a
  hand-entered answer may never be dropped (ids are only added). Each run records the id list and the
  hash of the configuration it was made with.
* Real and scripted data stay apart: an import needs both question sets locked in the database and is
  refused in a database that holds scripted demo answers (there is no override; test with a scratch
  database). `cli demo-archive` scripts this track under MADE-UP app names only (`chat-app-a` ...), so a
  scripted page never puts words in a real product's mouth.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import archive, controls, guards
from . import core  # attributes are used at call time only

CONFIG_FILE = Path(__file__).resolve().parent / "archive_chatbots.json"
CONFIG_FORMAT = "live-genesis-chatbots/1"
FILE_FORMAT = "live-genesis-chatbot-collection/1"
CHATBOT_LABEL = re.compile(r"[a-z][a-z0-9-]{1,23}")
MAX_CHATBOTS, MAX_CHATBOT_QUESTIONS = 6, 20
NAME_CHARS = 40
ATTRIBUTION_CHARS = 60    # an optional line a maker's brand guidance asks for, shown as text with every reply of that app
RESERVED_LABELS = ("chatbots",)   # card/<ID>-chatbots.html is the four-up card; card/<ID>-<label>.html is one app's
REPLY_CHARS = 2000        # chat apps write longer replies than the API runs (archive.ANSWER_CHARS is 1000)
SHOWN_MODEL_CHARS, NOTE_CHARS, SKIP_CHARS = 60, 200, 120
MAX_FILE_BYTES, MAX_REPLY_FILE_BYTES, MAX_SCREENSHOT_BYTES = 2_000_000, 64_000, 25_000_000
MAX_AGE_DAYS = 14
MANUAL, DEMO = "manual", "demo"
SOURCES = (MANUAL, DEMO)
ACCOUNTS = ("logged_out", "free_account", "paid_account")
SEARCH = ("on", "off", "unknown")
APPS = ("web", "mobile", "desktop")
NOT_APPLICABLE = "not_applicable"
COUNTRY = re.compile(r"[A-Z]{2}")
HASH = re.compile(r"[0-9a-f]{64}")
CONDITION_KEYS = ("account", "fresh_chat", "temporary_chat", "memory_off", "web_search", "country", "app", "shown_model", "note")
# The conditions whose change is flagged on the pages. The model name an app shows is reported separately.
COMPARED = ("account", "temporary_chat", "memory_off", "web_search", "country", "app")
SKIPPED = "skipped"       # prefix of the reason of a question the owner could not ask or the app refused
HOW_TO = (
    "Ask by hand. Open each chat app yourself. No program may open, read or copy from these apps: their terms forbid "
    "automated access, and this software never contacts them.",
    "Read each app's current terms before every monthly session: they change, and they decide whether you may publish "
    "what the app wrote.",
    "Recommended, and the same every month: a logged-in FREE account, with the app's temporary or private chat where it has "
    "one (ChatGPT \"Temporary Chat\", Gemini \"Temporary chat\", Grok \"Private Chat\"; Copilot has none: switch memory and "
    "personalisation off and start a new chat). If the reply shows a \"Sources\" or citations indicator, search was on: "
    "record web_search as on; if you switched search off, off; if you cannot tell, unknown.",
    "Before you start an app, fill in its `conditions`. Choices: account = logged_out | free_account | paid_account; "
    "fresh_chat = true (required: a new chat for every question); temporary_chat = true | false; "
    "memory_off = true | false | \"not_applicable\"; web_search = on | off | unknown; country = two letters, e.g. US; "
    "app = web | mobile | desktop; shown_model = the model name the app displays, or \"not shown\"; note = optional, short.",
    "For every question: start a NEW chat, paste the text of `prompt` exactly, wait for the reply, copy the WHOLE reply "
    "and paste it into `answer`. Do not edit it, shorten it or fix it.",
    "A reply with several lines or with quotation marks is awkward to paste into this file. Save it as a plain text file "
    "next to this file instead, and put that file's name in \"answer_file\" (leave `answer` empty).",
    "If the app refused, or you could not ask, leave `answer` empty and add \"skipped\": \"a short reason\".",
    "Optional, per answer: \"screenshot\": \"file name of a screenshot you kept\". Only its sha256 fingerprint is stored, "
    "never the picture and never the file name.",
    "If you did not ask one of the apps today, remove its whole block. Never remove single questions and never change a prompt.",
    "Keep the conditions the same from month to month where you can (same account state, temporary chat where the app "
    "has one, memory off, same country) and always record whether search was on.",
    "Then check the file without storing anything: python -m live_genesis.cli archive-chatbot-import THIS_FILE --dry-run",
)

TABLES = '''
-- Chat-app track: replies the owner collected BY HAND and imported from a file. One row per import.
CREATE TABLE IF NOT EXISTS archive_chatbot_runs (
    id INTEGER PRIMARY KEY, number INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('manual','demo')),
    -- The day the owner asked (their own calendar date) and the moment the file was imported. Both are kept.
    collected_on TEXT NOT NULL CHECK(collected_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    imported_at TEXT NOT NULL,
    -- The question ids of archive_chatbots.json at this import (a JSON list) and the hash of that configuration.
    question_ids TEXT NOT NULL, config_sha256 TEXT NOT NULL,
    -- sha256 of the imported file's bytes ('' for a scripted run). Never the file's name or path.
    file_sha256 TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, number)
);
-- What the owner recorded about how one chat app was asked in one run.
CREATE TABLE IF NOT EXISTS archive_chatbot_conditions (
    run_id INTEGER NOT NULL REFERENCES archive_chatbot_runs(id), chatbot TEXT NOT NULL,
    display_name TEXT NOT NULL, maker TEXT NOT NULL,
    -- A line the maker's brand guidance asks for ("Written with ..."), from archive_chatbots.json; '' when none.
    attribution TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL CHECK(account IN ('logged_out','free_account','paid_account')),
    fresh_chat INTEGER NOT NULL CHECK(fresh_chat=1),
    temporary_chat INTEGER NOT NULL CHECK(temporary_chat IN (0,1)),
    memory_off TEXT NOT NULL CHECK(memory_off IN ('true','false','not_applicable')),
    web_search TEXT NOT NULL CHECK(web_search IN ('on','off','unknown')),
    country TEXT NOT NULL CHECK(country GLOB '[A-Z][A-Z]'),
    app TEXT NOT NULL CHECK(app IN ('web','mobile','desktop')),
    shown_model TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, chatbot)
);
CREATE TABLE IF NOT EXISTS archive_chatbot_answers (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
    question_set TEXT NOT NULL, question_id TEXT NOT NULL, chatbot TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('manual','demo')),
    state TEXT NOT NULL CHECK(state IN ('answered','no_answer','withheld')),
    reason TEXT NOT NULL DEFAULT '',
    answer_text TEXT, normalised TEXT, answer_sha256 TEXT,
    changed INTEGER CHECK(changed IN (0,1)),
    screenshot_sha256 TEXT, created_at TEXT NOT NULL,
    UNIQUE (run_id, question_id, chatbot),
    CHECK ((state='answered') = (answer_text IS NOT NULL)),
    -- An answer belongs to a chat app whose conditions were recorded in the same run.
    FOREIGN KEY (run_id, chatbot) REFERENCES archive_chatbot_conditions(run_id, chatbot)
);
'''
RULES = '''
CREATE INDEX IF NOT EXISTS archive_chatbot_answers_series ON archive_chatbot_answers(chatbot, question_id, run_id);
CREATE TRIGGER IF NOT EXISTS archive_chatbot_runs_no_update BEFORE UPDATE ON archive_chatbot_runs
    BEGIN SELECT RAISE(ABORT, 'archive_chatbot_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_chatbot_runs_no_delete BEFORE DELETE ON archive_chatbot_runs
    BEGIN SELECT RAISE(ABORT, 'archive_chatbot_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_chatbot_conditions_no_update BEFORE UPDATE ON archive_chatbot_conditions
    BEGIN SELECT RAISE(ABORT, 'archive_chatbot_conditions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_chatbot_conditions_no_delete BEFORE DELETE ON archive_chatbot_conditions
    BEGIN SELECT RAISE(ABORT, 'archive_chatbot_conditions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_chatbot_answers_no_update BEFORE UPDATE ON archive_chatbot_answers
    BEGIN SELECT RAISE(ABORT, 'archive_chatbot_answers is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_chatbot_answers_no_delete BEFORE DELETE ON archive_chatbot_answers
    BEGIN SELECT RAISE(ABORT, 'archive_chatbot_answers is append-only'); END;
-- Hand-entered and scripted runs never share a database, and neither sits next to the other kind of model answer.
CREATE TRIGGER IF NOT EXISTS archive_chatbot_runs_kinds_apart BEFORE INSERT ON archive_chatbot_runs
    WHEN EXISTS (SELECT 1 FROM archive_chatbot_runs WHERE kind!=NEW.kind)
      OR (NEW.kind='manual' AND EXISTS (SELECT 1 FROM archive_runs WHERE kind='demo'))
      OR (NEW.kind='demo' AND EXISTS (SELECT 1 FROM archive_runs WHERE kind='real'))
    BEGIN SELECT RAISE(ABORT, 'hand-entered and scripted chat-app runs are never mixed with each other or with the other kind of answers'); END;
-- A chat app's dates only move forward: one run per app and day, and never a day before one already recorded.
CREATE TRIGGER IF NOT EXISTS archive_chatbot_conditions_forward BEFORE INSERT ON archive_chatbot_conditions
    WHEN EXISTS (SELECT 1 FROM archive_chatbot_conditions c JOIN archive_chatbot_runs r ON r.id=c.run_id
                 WHERE c.chatbot=NEW.chatbot AND r.id!=NEW.run_id
                   AND r.collected_on>=(SELECT collected_on FROM archive_chatbot_runs WHERE id=NEW.run_id))
    BEGIN SELECT RAISE(ABORT, 'a chat app already has a run on this date or a later one'); END;
CREATE TRIGGER IF NOT EXISTS archive_chatbot_answers_kind BEFORE INSERT ON archive_chatbot_answers
    WHEN (SELECT kind FROM archive_chatbot_runs WHERE id=NEW.run_id) IS NOT NEW.source
    BEGIN SELECT RAISE(ABORT, 'chat-app answer source does not match the kind of its run'); END;
-- A hand-entered answer is an answer to a locked question.
CREATE TRIGGER IF NOT EXISTS archive_chatbot_answers_locked_question BEFORE INSERT ON archive_chatbot_answers
    WHEN NEW.source='manual' AND NOT EXISTS
         (SELECT 1 FROM archive_questions WHERE question_set=NEW.question_set AND id=NEW.question_id)
    BEGIN SELECT RAISE(ABORT, 'a hand-entered chat-app answer needs a locked question'); END;
'''
TABLE_NAMES = ("archive_chatbot_runs", "archive_chatbot_conditions", "archive_chatbot_answers")


def migrate(cx) -> None:
    """Create the three tables and their rules. In place and additive: a database from before this track
    gains three empty tables and nothing recorded is touched (`tests/fixtures/archive_schema_v4.sql`)."""
    cx.executescript(TABLES + RULES)


def today() -> datetime.date:
    """The owner's calendar date (local time): `collected_on` is the day they sat at the chat apps."""
    return datetime.date.today()


def _refused(why: str) -> archive.ArchiveRefused:
    return archive.ArchiveRefused(why)


def _strict_pairs(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _load_json(raw: bytes) -> Any:
    """Strict JSON: UTF-8 (a leading byte-order mark is accepted), no duplicate key, no NaN."""
    def constant(_name: str):
        raise ValueError("NaN or Infinity")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_strict_pairs, parse_constant=constant)


def _line_ok(value: Any, maximum: int) -> bool:
    """A short one-line text the owner wrote (a name, a note): shape, character rule and content guards."""
    return (isinstance(value, str) and 0 < len(value.strip()) <= maximum and not guards.has_forbidden_character(value)
            and not guards.content_reason(value))


# ---- The configuration file -----------------------------------------------------------------------------

def config_hash(chatbots: list[dict], questions: list[str]) -> str:
    body = {"chatbots": chatbots, "questions": questions}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def load_config(path: str | Path | None = None) -> dict:
    """Read and validate `archive_chatbots.json`. Returns {status, chatbots, questions, sha256, prompts}:
    `chatbots` is a list of {label, name, maker, attribution} (`attribution` is optional in the file: a
    short line a maker asks to be shown with its app's output, '' when none); `questions` the ids in the file's order; `prompts`
    maps each id to its question (id, text, prompt, track) as the question FILES hold it. Strict shape,
    unique labels and ids, and every id must be a question of a file whose status is "locked".
    The hash covers the chat apps and the ids (not `status` or the note). Raises ArchiveRefused."""
    source = Path(path) if path is not None else CONFIG_FILE

    def bad(why: str) -> archive.ArchiveRefused:
        return _refused(f"Chat-app configuration {source.name}: {why}")
    try:
        raw = _load_json(source.read_bytes())
    except (OSError, ValueError) as err:
        raise _refused(f"Chat-app configuration {source.name} is missing or not valid JSON ({type(err).__name__})") from None
    if not isinstance(raw, dict) or set(raw) - {"format", "status", "note", "chatbots", "questions"}:
        raise bad("top level must be an object with format, status, note, chatbots and questions only")
    if raw.get("format") != CONFIG_FORMAT:
        raise bad(f'format must be "{CONFIG_FORMAT}"')
    if raw.get("status") not in ("draft", "locked"):
        raise bad('status must be "draft" or "locked"')
    if "note" in raw and not isinstance(raw["note"], str):
        raise bad("note must be text")
    listed, ids = raw.get("chatbots"), raw.get("questions")
    if not isinstance(listed, list) or not 1 <= len(listed) <= MAX_CHATBOTS:
        raise bad(f"chatbots must list 1 to {MAX_CHATBOTS} chat apps")
    chatbots: list[dict] = []
    for item in listed:
        if not isinstance(item, dict) or not {"label", "name", "maker"} <= set(item) <= {"label", "name", "maker", "attribution"}:
            raise bad("each chat app needs label, name and maker, and may have attribution")
        if not isinstance(item["label"], str) or not CHATBOT_LABEL.fullmatch(item["label"]) or item["label"] in RESERVED_LABELS:
            raise bad("a label must be 2-24 characters of a-z 0-9 -, starting with a letter, and not \"chatbots\"")
        if "attribution" in item and not _line_ok(item["attribution"], ATTRIBUTION_CHARS):
            raise bad(f"{item['label']}: attribution must be 1-{ATTRIBUTION_CHARS} plain characters on one line")
        if not _line_ok(item["name"], NAME_CHARS) or not _line_ok(item["maker"], NAME_CHARS):
            raise bad(f"{item['label']}: name and maker must be 1-{NAME_CHARS} plain characters")
        chatbots.append({"label": item["label"], "name": item["name"].strip(), "maker": item["maker"].strip(),
                         "attribution": item.get("attribution", "").strip()})
    if len({c["label"] for c in chatbots}) != len(chatbots) or len({c["name"].casefold() for c in chatbots}) != len(chatbots):
        raise bad("labels and names must differ")
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_CHATBOT_QUESTIONS \
            or not all(isinstance(i, str) and archive.ANY_QUESTION_ID.fullmatch(i) for i in ids):
        raise bad(f"questions must list 1 to {MAX_CHATBOT_QUESTIONS} question ids such as Q11 or T002")
    if len(set(ids)) != len(ids):
        raise bad("question ids must differ")
    prompts: dict[str, dict] = {}
    for track in archive.TRACKS:
        wanted = [i for i in ids if archive.track_of(i) == track]
        if not wanted:
            continue
        data = archive.load_questions(track=track)
        known = {q["id"]: q for q in data["questions"]}
        for question_id in wanted:
            if question_id not in known:
                raise bad(f"{question_id} is not a question in {archive._file(track).name}")
            if data["status"] != "locked":
                raise bad(f"{question_id} refers to {archive._file(track).name}, which is not locked yet")
            prompts[question_id] = {"id": question_id, "text": known[question_id]["text"], "prompt": known[question_id]["prompt"],
                                    "track": track}
    return {"status": raw["status"], "chatbots": chatbots, "questions": list(ids), "sha256": config_hash(chatbots, list(ids)),
            "prompts": prompts}


# ---- The file the owner fills in (cli archive-chatbot-template) ------------------------------------------

def _date(value: Any, what: str) -> datetime.date:
    try:
        if not isinstance(value, str) or not archive.ADDED_DATE.fullmatch(value):
            raise ValueError
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise _refused(f"{what} must be a date written YYYY-MM-DD") from None


def template(date: str | None = None) -> dict:
    """The file to fill in, as a dict: `collected_on`, and per chat app its `conditions` (all empty) and
    one entry per question with the exact prompt to paste and an empty `answer`. Reads the two question
    files and the configuration; opens no database and contacts nothing."""
    config = load_config()
    day = _date(date, "--date") if date is not None else today()
    return {"format": FILE_FORMAT, "how_to": list(HOW_TO), "collected_on": day.isoformat(),
            "chatbots": [{"chatbot": c["label"], "name": c["name"], "maker": c["maker"],
                          "conditions": {**dict.fromkeys(CONDITION_KEYS[:-1]), "note": ""},
                          "answers": [{"question": question_id, "prompt": config["prompts"][question_id]["prompt"], "answer": ""}
                                      for question_id in config["questions"]]}
                         for c in config["chatbots"]]}


def write_template(out: str | Path | None, date: str | None = None) -> Path:
    """Write the template to `out` (default: chatbots-<date>.json in the current folder). Never
    overwrites: a filled-in file must not be lost to a second run of the command."""
    data = template(date)
    target = Path(out) if out is not None else Path(f"chatbots-{data['collected_on']}.json")
    if target.exists():
        raise _refused(f"{target} already exists; it was not overwritten. Choose another name with --out.")
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return target


# ---- Reading a filled-in file ---------------------------------------------------------------------------

def classify_reply(text: Any) -> tuple[str, str, str | None]:
    """(state, reason, text to store) for one pasted reply: the rule for model answers
    (`archive.classify`) with the longer limit of this track. Only leading and trailing white space is
    removed; markdown, citation markers and line breaks are stored as pasted. A reply the character rule
    or the content guards match is `withheld` with the category and no text."""
    if not isinstance(text, str) or not text.strip():
        return "no_answer", archive.EMPTY, None
    text = text.strip()
    if len(text) > REPLY_CHARS:
        return "no_answer", archive.TOO_LONG, None
    if guards.has_forbidden_character(text, multiline=True):
        return "withheld", "invisible or control character", None
    reason = guards.content_reason(text)
    if reason:
        return "withheld", reason, None
    if not archive.normalise(text):
        return "no_answer", archive.EMPTY, None
    return "answered", "", text


def _beside(folder: Path, name: Any, limit: int) -> bytes | None:
    """The bytes of a file the owner named (next to the collection file, or an absolute path), or None
    when it is not a readable regular file of at most `limit` bytes."""
    if not isinstance(name, str) or not name.strip() or "\x00" in name:
        return None
    try:
        path = Path(name.strip()).expanduser()
        path = path if path.is_absolute() else folder / path
        if not path.is_file() or path.stat().st_size > limit:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _conditions(raw: Any, where: str, problems: list[str]) -> dict | None:
    if not isinstance(raw, dict) or set(raw) - set(CONDITION_KEYS) or any(key not in raw for key in CONDITION_KEYS[:-1]):
        problems.append(f"{where}: conditions must hold exactly " + ", ".join(CONDITION_KEYS[:-1]) + " and optionally note")
        return None
    before = len(problems)
    if raw["account"] not in ACCOUNTS:
        problems.append(f"{where}: account must be one of " + " | ".join(ACCOUNTS))
    if raw["fresh_chat"] is not True:
        problems.append(f"{where}: fresh_chat must be true. Every question is asked in a new chat; replies from one long "
                        "chat are not accepted")
    if type(raw["temporary_chat"]) is not bool:
        problems.append(f"{where}: temporary_chat must be true or false")
    if type(raw["memory_off"]) is not bool and raw["memory_off"] != NOT_APPLICABLE:
        problems.append(f'{where}: memory_off must be true, false or "{NOT_APPLICABLE}"')
    if raw["web_search"] not in SEARCH:
        problems.append(f"{where}: web_search must be one of " + " | ".join(SEARCH))
    if not isinstance(raw["country"], str) or not COUNTRY.fullmatch(raw["country"]):
        problems.append(f"{where}: country must be two capital letters, e.g. US")
    if raw["app"] not in APPS:
        problems.append(f"{where}: app must be one of " + " | ".join(APPS))
    if not _line_ok(raw["shown_model"], SHOWN_MODEL_CHARS):
        problems.append(f'{where}: shown_model must be 1-{SHOWN_MODEL_CHARS} plain characters on one line: the model name the '
                        'app displays, or "not shown"')
    note = raw.get("note", "")
    if note not in ("", None) and not _line_ok(note, NOTE_CHARS):
        problems.append(f"{where}: note must be at most {NOTE_CHARS} plain characters on one line")
    if len(problems) != before:
        return None
    memory = raw["memory_off"]
    return {"account": raw["account"], "fresh_chat": True, "temporary_chat": raw["temporary_chat"],
            "memory_off": memory if memory == NOT_APPLICABLE else bool(memory), "web_search": raw["web_search"],
            "country": raw["country"], "app": raw["app"], "shown_model": raw["shown_model"].strip(),
            "note": note.strip() if isinstance(note, str) else ""}


ANSWER_KEYS = {"question", "prompt", "answer", "answer_file", "skipped", "screenshot"}


def _answer(raw: Any, where: str, folder: Path, prompts: dict[str, dict], problems: list[str]) -> dict | None:
    if not isinstance(raw, dict) or set(raw) - ANSWER_KEYS or not isinstance(raw.get("question"), str):
        problems.append(f"{where}: an answer entry must hold question, prompt, answer and optionally answer_file, skipped, screenshot")
        return None
    question_id = raw["question"]
    where = f"{where}, {question_id if archive.ANY_QUESTION_ID.fullmatch(question_id) else 'an unknown question'}"
    if question_id not in prompts:
        problems.append(f"{where}: not one of the questions in {CONFIG_FILE.name}")
        return None
    if raw.get("prompt") != prompts[question_id]["prompt"]:
        problems.append(f"{where}: the prompt in the file is not the locked prompt. It must not be edited; make a new template")
        return None
    text, named, skipped = raw.get("answer", ""), raw.get("answer_file", ""), raw.get("skipped", "")
    if not isinstance(text, str) or not isinstance(named, str) or not isinstance(skipped, str):
        problems.append(f"{where}: answer, answer_file and skipped must be text")
        return None
    given = [bool(text.strip()), bool(named.strip()), bool(skipped.strip())]
    if sum(given) != 1:
        problems.append(f"{where}: give exactly one of answer, answer_file or skipped"
                        + (" (all are empty)" if not any(given) else ""))
        return None
    screenshot_hash = None
    if raw.get("screenshot") not in ("", None):
        picture = _beside(folder, raw["screenshot"], MAX_SCREENSHOT_BYTES)
        if picture is None:
            problems.append(f"{where}: the screenshot file was not found, is not a plain file or is larger than "
                            f"{MAX_SCREENSHOT_BYTES // 1_000_000} MB")
            return None
        screenshot_hash = hashlib.sha256(picture).hexdigest()
    if given[2]:
        if not _line_ok(skipped, SKIP_CHARS):
            problems.append(f"{where}: skipped must be a short plain reason on one line (at most {SKIP_CHARS} characters)")
            return None
        return {"question": question_id, "state": "no_answer", "reason": f"{SKIPPED}: {skipped.strip()}", "text": None,
                "chars": 0, "screenshot_hash": screenshot_hash}
    if given[1]:
        body = _beside(folder, named, MAX_REPLY_FILE_BYTES)
        try:
            text = body.decode("utf-8-sig") if body is not None else None
        except UnicodeDecodeError:
            text = None
        if text is None:
            problems.append(f"{where}: the answer_file was not found, is not plain UTF-8 text or is larger than "
                            f"{MAX_REPLY_FILE_BYTES // 1000} kB")
            return None
        if not text.strip():
            problems.append(f"{where}: the answer_file is empty")
            return None
    state, reason, kept = classify_reply(text)
    return {"question": question_id, "state": state, "reason": reason, "text": kept, "chars": len(text.strip()),
            "screenshot_hash": screenshot_hash}


def read_collection(path: str | Path, config: dict, *, allow_old: bool = False) -> dict:
    """Read and check a filled-in file completely. Returns {collected_on, file_sha256, chatbots: [{label,
    name, maker, conditions, answers: [{question, state, reason, text, chars, screenshot_hash}]}]}.
    Raises ArchiveRefused naming every problem found (place and rule; never the text of a reply).

    Checked: strict JSON; the format; `collected_on` (a real date, not in the future, at most MAX_AGE_DAYS
    old unless `allow_old`); at least one chat app, each one of the configuration, none twice; complete
    conditions with closed choices and `fresh_chat` true; for each app exactly the configured questions,
    each with its locked prompt unchanged and exactly one of `answer`, `answer_file` or `skipped`."""
    source = Path(path)
    try:
        if not source.is_file() or source.stat().st_size > MAX_FILE_BYTES:
            raise OSError
        raw_bytes = source.read_bytes()
        raw = _load_json(raw_bytes)
    except (OSError, ValueError) as err:
        raise _refused(f"{source.name} is missing, larger than {MAX_FILE_BYTES // 1_000_000} MB or not valid JSON "
                       f"({type(err).__name__}). A reply with line breaks or quotation marks cannot be pasted into a JSON "
                       "text as it is: save it as a text file and name it in \"answer_file\". Nothing was stored.") from None
    if not isinstance(raw, dict) or raw.get("format") != FILE_FORMAT:
        raise _refused(f'{source.name} is not a chat-app collection file (format must be "{FILE_FORMAT}"). Nothing was stored.')
    if set(raw) - {"format", "how_to", "collected_on", "chatbots"}:
        raise _refused(f"{source.name}: top level must hold format, how_to, collected_on and chatbots only. Nothing was stored.")
    day = _date(raw.get("collected_on"), f"{source.name}: collected_on")
    now = today()
    if day > now:
        raise _refused(f"{source.name}: collected_on {day} is in the future. Nothing was stored.")
    if (now - day).days > MAX_AGE_DAYS and not allow_old:
        raise _refused(f"{source.name}: collected_on {day} is more than {MAX_AGE_DAYS} days ago. If the date is right, "
                       "repeat with --allow-old. Nothing was stored.")
    listed = raw.get("chatbots")
    if not isinstance(listed, list) or not listed:
        raise _refused(f"{source.name}: chatbots must list at least one chat app. Nothing was stored.")
    known = {c["label"]: c for c in config["chatbots"]}
    problems: list[str] = []
    chatbots: list[dict] = []
    for item in listed:
        if not isinstance(item, dict) or set(item) - {"chatbot", "name", "maker", "conditions", "answers"} \
                or not isinstance(item.get("chatbot"), str):
            problems.append("a chat-app block must hold chatbot, name, maker, conditions and answers only")
            continue
        label = item["chatbot"]
        if label not in known:
            problems.append("a chat-app block names a label that is not in " + CONFIG_FILE.name)
            continue
        if any(c["label"] == label for c in chatbots):
            problems.append(f"{label}: listed twice")
            continue
        conditions = _conditions(item.get("conditions"), label, problems)
        entries = item.get("answers")
        if not isinstance(entries, list):
            problems.append(f"{label}: answers must be a list")
            continue
        answers = [found for found in (_answer(entry, label, source.parent, config["prompts"], problems) for entry in entries)
                   if found is not None]
        ids = [a["question"] for a in answers]
        if len(entries) == len(answers) and (len(set(ids)) != len(ids) or set(ids) != set(config["questions"])):
            problems.append(f"{label}: the answers must cover exactly the {len(config['questions'])} questions of "
                            f"{CONFIG_FILE.name}, each once (make a new template if the list changed)")
        order = {question_id: n for n, question_id in enumerate(config["questions"])}
        chatbots.append({**known[label], "conditions": conditions, "answers": sorted(answers, key=lambda a: order[a["question"]])})
    if problems:
        shown = problems[:20] + ([f"... and {len(problems) - 20} more"] if len(problems) > 20 else [])
        raise _refused(f"{source.name} was refused; nothing was stored:\n  " + "\n  ".join(shown))
    return {"collected_on": day.isoformat(), "file_sha256": hashlib.sha256(raw_bytes).hexdigest(), "chatbots": chatbots}


# ---- Import (free, audited; no model, no network) -------------------------------------------------------

def _has_run(cx, kind: str) -> bool:
    return cx.execute("SELECT 1 FROM archive_chatbot_runs WHERE kind=? LIMIT 1", (kind,)).fetchone() is not None


def _store(cx, run_id: int, kind: str, collected_on: str, question_set: str, label: str, answer: dict, created_at: str) -> bool | None:
    """Insert one answer. `changed` compares with this chat app's previous ANSWERED reply to the same
    question on an earlier date, in runs of the same kind: None for the first and for a row with no answer."""
    normalised = digest = changed = None
    if answer["state"] == "answered":
        normalised = archive.normalise(answer["text"])
        digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
        previous = cx.execute(
            "SELECT a.answer_sha256 FROM archive_chatbot_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
            "WHERE r.kind=? AND r.collected_on<? AND a.chatbot=? AND a.question_id=? AND a.state='answered' "
            "ORDER BY r.collected_on DESC LIMIT 1", (kind, collected_on, label, answer["question"])).fetchone()
        changed = None if previous is None else int(previous[0] != digest)
    cx.execute("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,reason,answer_text,"
               "normalised,answer_sha256,changed,screenshot_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (run_id, question_set, answer["question"], label, kind, answer["state"], answer["reason"], answer["text"],
                normalised, digest, changed, answer.get("screenshot_hash"), created_at))
    return None if changed is None else bool(changed)


def _open_run(cx, kind: str, collected_on: str, question_ids: list[str], config_sha: str, file_sha: str, imported_at: str) -> tuple[int, int]:
    number = int(cx.execute("SELECT COALESCE(MAX(number),0)+1 FROM archive_chatbot_runs WHERE kind=?", (kind,)).fetchone()[0])
    run_id = int(cx.execute(
        "INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256,file_sha256) "
        "VALUES (?,?,?,?,?,?,?)", (number, kind, collected_on, imported_at, json.dumps(question_ids), config_sha, file_sha)).lastrowid)
    return run_id, number


def _store_conditions(cx, run_id: int, chatbot: dict, conditions: dict) -> None:
    memory = conditions["memory_off"]
    cx.execute("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,attribution,account,fresh_chat,temporary_chat,"
               "memory_off,web_search,country,app,shown_model,note) VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?,?)",
               (run_id, chatbot["label"], chatbot["name"], chatbot["maker"], chatbot.get("attribution", ""), conditions["account"],
                int(conditions["temporary_chat"]), memory if memory == NOT_APPLICABLE else ("true" if memory else "false"),
                conditions["web_search"], conditions["country"], conditions["app"], conditions["shown_model"],
                conditions.get("note", "")))


def import_collection(path: str | Path, *, dry_run: bool = False, allow_old: bool = False) -> dict:
    """`cli archive-chatbot-import FILE`: check a filled-in file and store it as one hand-entered run.
    Free: no model, no network, no key, no budget hold. Audited. All or nothing: the whole file is
    checked before the first row is written, and `dry_run` stops there and reports what would be stored.

    Refused (ArchiveRefused; nothing stored) when: the file fails `read_collection`; a question set is
    not locked in this database or its file differs from the lock; the database holds scripted demo
    answers; the configuration names a question that is not locked here, or no longer names a question
    that already has a hand-entered answer (ids are only added); a chat app in the file already has a
    run on that date or a later one.

    The summary holds counts and lengths only, never the text of a reply."""
    config = load_config()
    collection = read_collection(path, config, allow_old=allow_old)
    day = collection["collected_on"]
    imported_at = core.utcnow()
    with core.connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        locked = {track: archive.require_lock(cx, track) for track in archive.TRACKS}   # both sets, checked against their files
        if archive._has(cx, "demo") or _has_run(cx, DEMO):
            raise _refused("This database holds scripted demo answers; hand-entered replies are never mixed with them. "
                           "Use the real database. Nothing was stored.")
        set_of = {q["id"]: locked[track]["question_set"] for track in archive.TRACKS for q in locked[track]["questions"]}
        missing = [i for i in config["questions"] if i not in set_of]
        if missing:
            raise _refused(f"{CONFIG_FILE.name} names {', '.join(missing)}, which is not locked in this database yet "
                           "(python -m live_genesis.cli archive-lock --set trending). Nothing was stored.")
        recorded = {r[0] for r in cx.execute("SELECT DISTINCT question_id FROM archive_chatbot_answers WHERE source='manual'")}
        dropped = sorted(recorded - set(config["questions"]))
        if dropped:
            raise _refused(f"{CONFIG_FILE.name} no longer lists {', '.join(dropped)}, which already has hand-entered answers. "
                           "After the first import question ids may only be added: restore the id. Nothing was stored.")
        for chatbot in collection["chatbots"]:
            latest = cx.execute("SELECT MAX(r.collected_on) FROM archive_chatbot_conditions c JOIN archive_chatbot_runs r "
                                "ON r.id=c.run_id WHERE c.chatbot=? AND r.kind='manual'", (chatbot["label"],)).fetchone()[0]
            if latest == day:
                raise _refused(f"{chatbot['label']} already has a hand-entered run dated {day}. One run per chat app and day; "
                               "a stored reply is never replaced. Nothing was stored.")
            if latest is not None and latest > day:
                raise _refused(f"{chatbot['label']} already has a hand-entered run dated {latest}, after {day}. A chat app's "
                               "dates only move forward. Nothing was stored.")
        first = not _has_run(cx, MANUAL)
        summary = {"dry_run": dry_run, "collected_on": day, "imported_at": imported_at, "questions": config["questions"],
                   "config_sha256": config["sha256"], "first_import": first, "chatbots": []}
        run_id = number = None
        if not dry_run:
            run_id, number = _open_run(cx, MANUAL, day, config["questions"], config["sha256"], collection["file_sha256"], imported_at)
        for chatbot in collection["chatbots"]:
            if not dry_run:
                _store_conditions(cx, run_id, chatbot, chatbot["conditions"])
            counts = {state: 0 for state in archive.STATES}
            rows = []
            for answer in chatbot["answers"]:
                counts[answer["state"]] += 1
                changed = None if dry_run else _store(cx, run_id, MANUAL, day, set_of[answer["question"]], chatbot["label"], answer, imported_at)
                row = {"question": answer["question"], "state": answer["state"], "characters": answer["chars"],
                       "screenshot": answer["screenshot_hash"] is not None}
                if answer["state"] == "withheld" or (answer["state"] == "no_answer" and not answer["reason"].startswith(SKIPPED)):
                    row["reason"] = answer["reason"]      # a category, never the reply
                if not dry_run:
                    row["changed"] = changed
                rows.append(row)
            summary["chatbots"].append({"chatbot": chatbot["label"], "conditions": chatbot["conditions"], "answers": counts,
                                        "skipped": sum(1 for a in chatbot["answers"] if a["reason"].startswith(SKIPPED)),
                                        "rows": rows})
        if not dry_run:
            summary["run"] = number
            total = sum(len(c["answers"]) for c in collection["chatbots"])
            controls.audit(cx, f"archive-chatbot-import run {number}: collected {day}, "
                               f"{','.join(c['label'] for c in collection['chatbots'])}, {total} rows, config sha256 {config['sha256'][:16]}")
    return summary


# ---- Scripted demo (part of cli demo-archive): MADE-UP app names only ------------------------------------

DEMO_APPS = (("chat-app-a", "Chat app A", "Made-up maker A"), ("chat-app-b", "Chat app B", "Made-up maker B"),
             ("chat-app-c", "Chat app C", "Made-up maker C"), ("chat-app-d", "Chat app D", "Made-up maker D"))
DEMO_ATTRIBUTION = {"chat-app-c": "Written with Chat app C"}   # one made-up app carries an attribution line, to show it
DEMO_DATES = ("2026-08-03", "2026-09-01", "2026-10-01", "2026-11-02")
DEMO_FIRST_RUN = {"chat-app-d": 2}       # this app joins at the second scripted date
_BASE = {"fresh_chat": True, "country": "US", "note": ""}
# Per app: ((from run, conditions), ...). chat-app-a switches search on at run 3; chat-app-c moves country at run 4;
# chat-app-b only shows another model name at run 4 (not a change of conditions).
DEMO_CONDITIONS = {
    "chat-app-a": ((1, {**_BASE, "account": "free_account", "temporary_chat": True, "memory_off": True, "web_search": "off",
                        "app": "web", "shown_model": "A-1"}),
                   (3, {**_BASE, "account": "free_account", "temporary_chat": True, "memory_off": True, "web_search": "on",
                        "app": "web", "shown_model": "A-1", "note": "Scripted: search was switched on from this date."})),
    "chat-app-b": ((1, {**_BASE, "account": "logged_out", "temporary_chat": False, "memory_off": NOT_APPLICABLE,
                        "web_search": "unknown", "app": "web", "shown_model": "B mini"}),
                   (4, {**_BASE, "account": "logged_out", "temporary_chat": False, "memory_off": NOT_APPLICABLE,
                        "web_search": "unknown", "app": "web", "shown_model": "B mini 2"})),
    "chat-app-c": ((1, {**_BASE, "account": "paid_account", "temporary_chat": True, "memory_off": True, "web_search": "on",
                        "app": "mobile", "shown_model": "not shown"}),
                   (4, {**_BASE, "account": "paid_account", "temporary_chat": True, "memory_off": True, "web_search": "on",
                        "app": "mobile", "shown_model": "not shown", "country": "GB"})),
    "chat-app-d": ((2, {**_BASE, "account": "free_account", "temporary_chat": False, "memory_off": False, "web_search": "off",
                        "app": "desktop", "shown_model": "D"}),),
}
# Question text -> per app, ((from run, scripted reply), ...). Written by hand; no app was asked. A question whose
# text is not in the question list of the database simply gets no scripted reply.
DEMO_SCRIPT: dict[str, dict[str, tuple]] = {
    "What is today's date?": {
        "chat-app-a": ((1, "I can't see a calendar here, but it is probably some time in 2025."), (3, "Today is 1 October 2026."),
                       (4, "Today is 2 November 2026.")),
        "chat-app-b": ((1, "I don't have access to today's date."),),
        "chat-app-c": ((1, "Today is 3 August 2026."), (2, "Today is 1 September 2026."), (3, "Today is 1 October 2026."),
                       (4, "Today is 2 November 2026.")),
        "chat-app-d": ((2, "It is 2024."), (4, "I can't be sure of the date; my best guess is some time in 2026."))},
    "What year is it?": {
        "chat-app-a": ((1, "Probably 2025."), (3, "It is 2026.")),
        "chat-app-b": ((1, "I can't know what year it is."),),
        "chat-app-c": ((1, "It is 2026."),),
        "chat-app-d": ((2, "2024."), (4, "Probably 2026."))},
    "Which model are you, and who made you?": {
        "chat-app-a": ((1, "I am Chat app A, a made-up assistant in this scripted sample."),),
        "chat-app-b": ((1, "I am Chat app B, a made-up assistant in this scripted sample."),
                       (4, "I am Chat app B, running the made-up model B mini 2 in this scripted sample.")),
        "chat-app-c": ((1, "I am not able to say which model I am."), (2, "I am Chat app C, a made-up assistant in this scripted sample.")),
        "chat-app-d": ((2, "I am Chat app D, a made-up assistant in this scripted sample."),)},
    "Can you look things up while answering this?": {
        "chat-app-a": ((1, "No. Search is switched off in this chat."), (3, "Yes. Search is switched on, so I can look things up.")),
        "chat-app-b": ((1, "I'm not sure whether I can search here."),),
        "chat-app-c": ((1, "Yes, I can search the web in this chat."),),
        "chat-app-d": ((2, "No, I answer from what I learned in training."),)},
    "What is the capital of Australia?": {
        "chat-app-a": ((1, "Canberra."),),
        "chat-app-b": ((1, "The capital of Australia is Canberra."),),
        "chat-app-c": ((1, "**Canberra** is the capital of Australia.[1]\n\n- It was chosen in 1908 as a compromise between Sydney "
                           "and Melbourne.\n- Parliament has sat there since 1927.\n\n[1] Scripted sample source, not a real citation."),),
        "chat-app-d": ((2, "Sydney."), (3, "Canberra. Sydney is the largest city, not the capital."))},
    "Which company is the most valuable in the world right now?": {
        "chat-app-a": ((1, "I can't check live markets in this chat."),
                       (3, "One of the large technology companies, according to the scripted search result in this sample.")),
        "chat-app-b": ((1, "I don't have live market data."),),
        "chat-app-c": ((1, "A large technology company; this scripted sample names none."),),
        "chat-app-d": ((2, "I don't know today's figures."),)},
}
# (question text, app, run) scripted as a question the app was not asked, so the "no reply" cell is shown too.
DEMO_SKIPPED = {("What year is it?", "chat-app-b", 3): f"{SKIPPED}: the app showed an error (scripted)"}


def _at(steps: tuple, run: int):
    return [value for start, value in steps if start <= run][-1]


def demo(cx, sets: list[dict]) -> dict:
    """Scripted chat-app runs for `cli demo-archive`, written inside its transaction. `sets` are the
    question sets of the scripted archive ({question_set, questions}); a scripted question is found
    by its text. Four made-up apps, four dated runs, every row source `demo`. No app was asked."""
    found = {q["text"]: (q["id"], data["question_set"]) for data in sets for q in data["questions"]}
    questions = [(text, *found[text]) for text in DEMO_SCRIPT if text in found]
    if not questions:
        return {"chatbot_runs": 0, "chatbots": [], "chatbot_answers": 0}
    ids = [question_id for _text, question_id, _set in questions]
    apps = [{"label": label, "name": name, "maker": maker, "attribution": DEMO_ATTRIBUTION.get(label, "")}
            for label, name, maker in DEMO_APPS]
    digest = config_hash(apps, ids)
    written = 0
    for number, day in enumerate(DEMO_DATES, 1):
        stamp = f"{day}T12:00:00+00:00"
        run_id, _ = _open_run(cx, DEMO, day, ids, digest, "", stamp)
        for app in apps:
            label = app["label"]
            if DEMO_FIRST_RUN.get(label, 1) > number:
                continue
            _store_conditions(cx, run_id, app, _at(DEMO_CONDITIONS[label], number))
            for text, question_id, question_set in questions:
                skipped = DEMO_SKIPPED.get((text, label, number))
                if skipped:
                    answer = {"question": question_id, "state": "no_answer", "reason": skipped, "text": None}
                else:
                    state, reason, kept = classify_reply(_at(DEMO_SCRIPT[text][label], number))
                    answer = {"question": question_id, "state": state, "reason": reason, "text": kept}
                _store(cx, run_id, DEMO, day, question_set, label, answer, stamp)
                written += 1
    return {"chatbot_runs": len(DEMO_DATES), "chatbots": [label for label, _name, _maker in DEMO_APPS], "chatbot_answers": written}


# ---- Public payload: the `chatbots` block of archive.public_payload ---------------------------------------

BLOCK_KEYS = ("apps", "questions", "runs", "answers")
APP_KEYS = ("label", "name", "maker", "attribution")
RUN_KEYS = ("number", "collected_on", "imported_at", "demo", "config_hash", "questions", "conditions")
ANSWER_KEYS_PUBLIC = ("id", "run", "question", "chatbot", "source", "state", "reason", "text", "changed", "screenshot_hash")


def public_block(cx, tables: set[str]) -> dict | None:
    """The chat-app track for the pages and the export, or None when the database holds no such run (the
    payload then has no `chatbots` key at all and is what it always was). Runs are ordered by the day
    the owner asked. Never the imported file's hash or any path; a screenshot is its sha256 only."""
    if not set(TABLE_NAMES) <= tables:
        return None
    runs = [dict(r) for r in cx.execute("SELECT * FROM archive_chatbot_runs ORDER BY collected_on,number")]
    if not runs:
        return None
    number = {r["id"]: r["number"] for r in runs}
    conditions: dict[int, dict] = {}
    apps: dict[str, dict] = {}
    for row in cx.execute("SELECT c.* FROM archive_chatbot_conditions c JOIN archive_chatbot_runs r ON r.id=c.run_id "
                          "ORDER BY r.collected_on,r.number,c.rowid"):
        row = dict(row)
        memory = row["memory_off"]
        conditions.setdefault(row["run_id"], {})[row["chatbot"]] = {
            "account": row["account"], "fresh_chat": bool(row["fresh_chat"]), "temporary_chat": bool(row["temporary_chat"]),
            "memory_off": memory if memory == NOT_APPLICABLE else memory == "true", "web_search": row["web_search"],
            "country": row["country"], "app": row["app"], "shown_model": row["shown_model"], "note": row["note"]}
        known = apps.setdefault(row["chatbot"], {"label": row["chatbot"]})
        known.update(name=row["display_name"], maker=row["maker"], attribution=row["attribution"])   # the newest run's wording
    questions: list[str] = []
    for run in reversed(runs):                                           # the newest list first, then ids only older runs had
        questions += [i for i in json.loads(run["question_ids"]) if i not in questions]
    answers = [{"id": a["id"], "run": number[a["run_id"]], "question": a["question_id"], "chatbot": a["chatbot"],
                "source": a["source"], "state": a["state"], "reason": a["reason"], "text": a["answer_text"],
                "changed": None if a["changed"] is None else bool(a["changed"]), "screenshot_hash": a["screenshot_sha256"]}
               for a in (dict(r) for r in cx.execute(
                   "SELECT a.* FROM archive_chatbot_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
                   "ORDER BY r.collected_on,r.number,a.id"))]
    block = {"apps": list(apps.values()), "questions": questions,
             "runs": [{"number": r["number"], "collected_on": r["collected_on"], "imported_at": r["imported_at"],
                       "demo": r["kind"] == DEMO, "config_hash": r["config_sha256"], "questions": json.loads(r["question_ids"]),
                       "conditions": conditions.get(r["id"], {})} for r in runs],
             "answers": answers}
    return guards.clean_for_reading(block)


def conditions_problem(value: Any) -> str | None:
    """Why one public conditions object is not complete and well-formed, or None."""
    if not isinstance(value, dict) or set(value) != set(CONDITION_KEYS):
        return "a chat app's conditions are missing or incomplete"
    if value["account"] not in ACCOUNTS or value["fresh_chat"] is not True or type(value["temporary_chat"]) is not bool \
            or (type(value["memory_off"]) is not bool and value["memory_off"] != NOT_APPLICABLE) or value["web_search"] not in SEARCH \
            or not isinstance(value["country"], str) or not COUNTRY.fullmatch(value["country"]) or value["app"] not in APPS \
            or not isinstance(value["shown_model"], str) or not 0 < len(value["shown_model"]) <= SHOWN_MODEL_CHARS \
            or not isinstance(value["note"], str) or len(value["note"]) > NOTE_CHARS:
        return "a chat app's conditions hold a value that is not allowed"
    return None


def block_problem(block: Any, question_ids: set[str]) -> str | None:
    """Why the `chatbots` block of a payload is malformed, or None. `question_ids` are the ids of the
    payload's own questions (both tracks). Shape only; what may be PUBLISHED is `export_problem`.
    An answer must belong to a question in its run's recorded id list and to a chat app whose
    conditions that run recorded."""
    if not isinstance(block, dict) or any(not isinstance(block.get(key), list) for key in BLOCK_KEYS):
        return "chatbots: a key is missing or has the wrong type"
    labels: list[str] = []
    for app in block["apps"]:
        if not isinstance(app, dict) or not all(isinstance(app.get(key), str) for key in APP_KEYS) \
                or not CHATBOT_LABEL.fullmatch(app["label"]) or app["label"] in labels or app["label"] in RESERVED_LABELS \
                or not all(0 < len(app[key]) <= NAME_CHARS for key in ("name", "maker")) or len(app["attribution"]) > ATTRIBUTION_CHARS:
            return "chatbots: a chat app lacks its label, name, maker or attribution, or a label repeats"
        labels.append(app["label"])
    ids = block["questions"]
    if not all(isinstance(i, str) and i in question_ids for i in ids) or len(set(ids)) != len(ids):
        return "chatbots: the question list names an unknown question or repeats one"
    runs: dict[int, dict] = {}
    dated: set[tuple[str, str]] = set()
    for run in block["runs"]:
        if not isinstance(run, dict) or type(run.get("number")) is not int or run["number"] in runs \
                or not isinstance(run.get("collected_on"), str) or not archive.ADDED_DATE.fullmatch(run["collected_on"]) \
                or not isinstance(run.get("imported_at"), str) or not archive._DATE.fullmatch(run["imported_at"]) \
                or type(run.get("demo")) is not bool or not isinstance(run.get("config_hash"), str) or not HASH.fullmatch(run["config_hash"]):
            return "chatbots: a run lacks its number, dates, demo flag or configuration hash"
        asked, conditions = run.get("questions"), run.get("conditions")
        if not isinstance(asked, list) or not asked or len(set(map(str, asked))) != len(asked) or not all(i in ids for i in asked):
            return "chatbots: a run's question list is missing or names a question that is not in the track's list"
        if not isinstance(conditions, dict) or not conditions or not all(label in labels for label in conditions):
            return "chatbots: a run lacks its conditions or names an unknown chat app"
        for label, value in conditions.items():
            problem = conditions_problem(value)
            if problem:
                return f"chatbots: {problem}"
            if (label, run["collected_on"]) in dated:
                return "chatbots: a chat app has two runs on one date"
            dated.add((label, run["collected_on"]))
        runs[run["number"]] = run
    if len({run["demo"] for run in runs.values()}) > 1:
        return "chatbots: scripted and hand-entered runs are mixed"
    seen: set[tuple] = set()
    for answer in block["answers"]:
        if not isinstance(answer, dict) or type(answer.get("id")) is not int or answer.get("run") not in runs \
                or answer.get("source") not in SOURCES or answer.get("state") not in archive.STATES \
                or not isinstance(answer.get("reason"), str) or answer.get("changed") not in (None, True, False) \
                or not isinstance(answer.get("question"), str) or not isinstance(answer.get("chatbot"), str):
            return "chatbots: an answer has a missing or invalid field"
        run = runs[answer["run"]]
        if answer["question"] not in run["questions"]:
            return "chatbots: an answer belongs to a question that is not in its run's recorded list"
        if answer["chatbot"] not in run["conditions"]:
            return "chatbots: an answer belongs to a chat app whose conditions its run did not record"
        if (answer["source"] == DEMO) != run["demo"]:
            return "chatbots: an answer's source does not fit its run"
        text = answer.get("text")
        if (answer["state"] == "answered") != isinstance(text, str) or (answer["state"] != "answered" and text is not None) \
                or (isinstance(text, str) and not 0 < len(text) <= REPLY_CHARS):
            return "chatbots: an answer's text does not fit its state or is longer than the archive stores"
        shot = answer.get("screenshot_hash")
        if shot is not None and not (isinstance(shot, str) and HASH.fullmatch(shot)):
            return "chatbots: a screenshot is not given as a sha256 hash"
        key = (answer["run"], answer["question"], answer["chatbot"])
        if key in seen:
            return "chatbots: two answers share one run, question and chat app"
        seen.add(key)
    return None


def closed_problem(block: dict) -> str | None:
    """For a file that is to be published: every object of the block holds exactly the keys
    `public_block` writes. `block` already passed block_problem."""
    checks = [("chatbots", block, BLOCK_KEYS)]
    checks += [("a chat app", app, APP_KEYS) for app in block["apps"]]
    checks += [(f"chat-app run {run['number']}", run, RUN_KEYS) for run in block["runs"]]
    checks += [(f"chat-app answer id {a['id']}", a, ANSWER_KEYS_PUBLIC) for a in block["answers"]]
    for place, node, required in checks:
        problem = archive._keys_problem(place, node, required)
        if problem:
            return problem
    return None


def export_problem(block: dict) -> str | None:
    """Why a well-formed block may not be PUBLISHED, or None (snapshot --export / --check): scripted
    rows are never reviewed data, and every chat app must be one of the checked-in configuration, under
    the name and maker that file gives. `block` already passed block_problem."""
    if any(run["demo"] for run in block["runs"]) or any(a["source"] != MANUAL for a in block["answers"]):
        return "it holds scripted chat-app answers; the demo is published without review"
    try:
        known = {c["label"]: c for c in load_config()["chatbots"]}
    except archive.ArchiveRefused as err:
        return f"the chat-app configuration cannot be read ({err})"
    for app in block["apps"]:
        if app["label"] not in known:
            return f"chat app {app['label']} is not in {CONFIG_FILE.name}"
        if any(app[key] != known[app["label"]][key] for key in ("name", "maker", "attribution")):
            return f"chat app {app['label']} does not carry the name, maker and attribution {CONFIG_FILE.name} gives it"
    return closed_problem(block)


def status(cx) -> dict:
    """For `archive-status`: the configuration's state and the hand-entered runs (numbers and counts only)."""
    try:
        config = load_config()
        state: dict = {"status": config["status"], "chatbots": [c["label"] for c in config["chatbots"]],
                       "questions": config["questions"], "sha256": config["sha256"]}
    except archive.ArchiveRefused as err:
        state = {"error": str(err)}
    runs = [dict(r) for r in cx.execute(
        "SELECT r.number,r.kind,r.collected_on,r.imported_at,COUNT(a.id) AS answers FROM archive_chatbot_runs r "
        "LEFT JOIN archive_chatbot_answers a ON a.run_id=r.id GROUP BY r.id ORDER BY r.collected_on,r.number")]
    return {"config_file": state, "runs": runs}


# ---- CLI glue ---------------------------------------------------------------------------------------------

COMMANDS = ("archive-chatbot-template", "archive-chatbot-import")
IMPORT_NOTE = ("Entered by hand: these replies cannot be verified and are labelled so on every page. They are published "
               "only through the reviewed path: python -m live_genesis.snapshot --export --archive-only")


def add_parsers(sub) -> None:
    template_p = sub.add_parser("archive-chatbot-template", help="Answers Over Time, chat apps: write the file the owner fills in BY HAND (each app, each question's exact prompt, empty answers). Free; no model, no network, no database")
    template_p.add_argument("--out", metavar="FILE", default=None, help="Where to write it (default: chatbots-<date>.json here); never overwrites")
    template_p.add_argument("--date", metavar="YYYY-MM-DD", default=None, help="The day you ask the apps (default: today)")
    import_p = sub.add_parser("archive-chatbot-import", help="Answers Over Time, chat apps: check a filled-in file and store it as one hand-entered run (audited). Free; no model, no network, no key")
    import_p.add_argument("file", metavar="FILE")
    import_p.add_argument("--dry-run", action="store_true", help="Check everything and print what would be stored; store nothing")
    import_p.add_argument("--allow-old", action="store_true", help=f"Accept a collected_on more than {MAX_AGE_DAYS} days ago")


def handle_command(args) -> None:
    """Run one of COMMANDS; ArchiveRefused is left to the caller. Prints counts and lengths, never a reply."""
    if args.command == "archive-chatbot-template":
        target = write_template(args.out, args.date)
        print(f"Template written to {target}. Nothing was contacted and nothing was stored.")
        print("Fill it in BY HAND: open each chat app yourself, a new chat for every question, paste the prompt exactly, "
              "copy the whole reply. Never use a program to collect replies.")
        print(f"Then: python -m live_genesis.cli archive-chatbot-import {target} --dry-run")
        return
    done = import_collection(args.file, dry_run=args.dry_run, allow_old=args.allow_old)
    print(json.dumps(done, indent=1))
    if done["dry_run"]:
        print("DRY RUN: the file passed every check. Nothing was stored. Repeat without --dry-run to store it.")
        return
    print(f"Stored as hand-entered run {done['run']}, collected {done['collected_on']}. Recorded in the operator audit log.")
    if done["first_import"]:
        print(f"This was the first hand-entered import: from now on the question ids in {CONFIG_FILE.name} may only be added to.")
    print(IMPORT_NOTE)
