"""Answers Over Time, the chat-app track: the same questions typed into consumer chat apps BY HAND.

What this module is, honestly:
* The OWNER opens each chat app, starts a new chat, pastes the question's exact prompt and copies the
  reply into a file. `cli archive-chatbot-template` writes that file to fill in; `cli
  archive-chatbot-import` checks it and stores it. Nothing here contacts a chat app, a model or the
  network, and nothing here may ever be made to: no automated collection of any kind.
* A hand-entered reply cannot be verified. There is no receipt, no token count and no model id beyond
  the name the app displayed. Pages and exports label every such reply "entered by hand".
* The questions are this track's OWN list (`archive_chatbots.json`, ids C001...): text, the exact prompt
  (one fixed prefix, a blank line, the question), an optional short `hook` and the date added. It is not
  linked to the locked core list or the trending list, and an import needs neither of them locked.
* Storage is its own append-only tables (`archive_chatbot_runs`, `archive_chatbot_conditions`,
  `archive_chatbot_answers`, `archive_chatbot_questions`, `archive_human_collections`,
  `archive_human_answers`), apart from the model answers in `archive_answers`: a hand-entered run is
  never a row of a model grid or timeline and never takes part in a model's `changed`.
* One import is one run dated `collected_on` (the day the owner asked). It holds one or more chat apps
  and, optionally, the HUMAN block; per (run, chat app) the CONDITIONS the owner recorded and one answer
  per question. `changed` compares a reply with that chat app's previous shown answer to the same
  question, by `archive.normalise`.
* HUMAN is a separate kind of answerer, never an app and never a model: answers written by people that
  the owner collected by hand (for instance by asking in a Reddit thread; this software never contacts
  Reddit or any other site). One short text per question per collection, marked `quoted` (one person's
  words, verbatim) or `summarised` (the owner's summary of several). Its conditions are its own shape:
  `source` (free text), `collected_on`, optional `count`. No username, no link. A text that looks like
  a handle (`u/name`, `@name`) or a web address is refused. A difference from the collection before is
  recorded as `changed` like any other, but the pages call it "different answer this month": other
  people answered, nobody changed their mind.
* A refusal or a non-answer is STORED (so the record is honest) and never shown as a card: the owner
  marks it `"refused": true` (with or without the refusal's text) or `"skipped": "reason"`. A refused
  reply never takes part in `changed`.
* A chat app's dates only move forward: an import for a date that is not later than that app's latest
  recorded date is refused (the same date twice included), so the stored `changed` flags stay true.
* The question list is `draft` until the first real import. That import records every question with a
  hash (`archive_chatbot_questions`); from then on a recorded question may never change or disappear,
  and new ones are appended. Each run records the id list and the hash of the configuration.
* No politics: a question or hook holding an obviously political word is refused on load
  (`POLITICAL_TERMS`). The list is crude: it catches words, not meaning, and the owner still reads
  every question.
* Real and scripted data stay apart: an import is refused in a database that holds scripted demo answers
  (there is no override; test with a scratch database). `cli demo-archive` scripts this track under
  MADE-UP names only (`chat-app-a` ..., "Human (sample)"), with its own scripted question list, so a
  scripted page never puts words in a real product's or a real person's mouth.
* Recorded text is never rewritten, shortened or tidied. The pages shorten the DISPLAY (a clamp with
  "more"), never the text.
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
CONFIG_FORMAT = "live-genesis-chatbots/2"
FILE_FORMAT = "live-genesis-chatbot-collection/2"
CHAT_QUESTION_ID = re.compile(r"C[0-9]{3}")
QUESTION_SET = "chat"     # archive_chatbot_answers.question_set of every row written since the track got its own list
# The fixed prefix of every chat-app prompt: prefix, a blank line, the question. It asks for a short, casual reply so
# that the reply itself is short; recorded text is never shortened by us.
PROMPT_PREFIX = "Answer in one short sentence, like you're texting a friend. Be funny if you can."
QUESTION_CHARS, HOOK_CHARS, ASIDE_CHARS, META_CHARS = 160, 60, 160, 80
HUMAN_LABEL = "human"     # never a chat app's label: card/<ID>-human.html is the human card
HUMAN_CHARS = 500         # one short text per question
HUMAN_SOURCE_CHARS = 80
MANNERS = ("quoted", "summarised")
REFUSED = "refused"       # reason of a reply the owner marked as a refusal
CHATBOT_LABEL = re.compile(r"[a-z][a-z0-9-]{1,23}")
MAX_CHATBOTS, MAX_CHATBOT_QUESTIONS = 6, 60
NAME_CHARS = 40
ATTRIBUTION_CHARS = 60    # an optional line a maker's brand guidance asks for, shown as text with every reply of that app
RESERVED_LABELS = ("chatbots", HUMAN_LABEL)   # card/<ID>-chatbots.html is the card of all; card/<ID>-<label>.html is one app's
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
    "one (ChatGPT \"Temporary Chat\", Gemini \"Temporary chat\", Grok \"Private Chat\"). If the reply shows a \"Sources\" or "
    "citations indicator, search was on: record web_search as on; if you switched search off, off; if you cannot tell, unknown.",
    "Before you start an app, fill in its `conditions`. Choices: account = logged_out | free_account | paid_account; "
    "fresh_chat = true (required: a new chat for every question); temporary_chat = true | false; "
    "memory_off = true | false | \"not_applicable\"; web_search = on | off | unknown; country = two letters, e.g. US; "
    "app = web | mobile | desktop; shown_model = the model name the app displays, or \"not shown\"; note = optional, short.",
    "For every question: start a NEW chat, paste the text of `prompt` exactly, wait for the reply, copy the WHOLE reply "
    "and paste it into `answer`.",
    "Paste the whole reply even if it is long; never trim or reword it. The page shortens the display, not the text.",
    "A reply with several lines or with quotation marks is awkward to paste into this file. Save it as a plain text file "
    "next to this file instead, and put that file's name in \"answer_file\" (leave `answer` empty).",
    "If the app refused or dodged the question, add \"refused\": true (paste what it said into `answer` if you like; it is "
    "stored and never shown). If you could not ask at all, leave `answer` empty and add \"skipped\": \"a short reason\". "
    "The pages show only real answers and say that refusals and non-answers are left out.",
    "Optional, per answer: \"screenshot\": \"file name of a screenshot you kept\". Only its sha256 fingerprint is stored, "
    "never the picture and never the file name.",
    "If you did not ask one of the apps today, remove its whole block. Never remove single questions and never change a prompt.",
    "Keep the conditions the same from month to month where you can (same account state, temporary chat where the app "
    "has one, memory off, same country) and always record whether search was on.",
    "The `human` block is optional: remove it if you collected no answers from people this time. It is for answers PEOPLE "
    "wrote, which you collected yourself by hand (no program contacts Reddit or any other site). Before you publish other "
    "people's words, say where you ask that replies may be quoted, and read that community's rules. `conditions`: source = "
    "where, in a few plain words (e.g. \"Reddit thread\"; no link, no username); collected_on = YYYY-MM-DD; count = how "
    "many replies you read (a number, or null). Per question: one short `answer` (one sentence, like the apps) and `kind` = "
    "\"quoted\" (one person's words, copied exactly) or \"summarised\" (your own short summary of several replies), or "
    "\"skipped\": \"reason\". No usernames, no @handles, no links: such a text is refused.",
    "Then check the file without storing anything: python -m live_genesis.cli archive-chatbot-import THIS_FILE --dry-run",
)

TABLES = """
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
    -- 1: the owner marked this reply as a refusal or a dodge. Stored, never shown, never part of `changed`.
    refused INTEGER NOT NULL DEFAULT 0 CHECK(refused IN (0,1)),
    UNIQUE (run_id, question_id, chatbot),
    CHECK ((state='answered') = (answer_text IS NOT NULL)),
    -- An answer belongs to a chat app whose conditions were recorded in the same run.
    FOREIGN KEY (run_id, chatbot) REFERENCES archive_chatbot_conditions(run_id, chatbot)
);
-- The track's own question list as recorded at import: a recorded question never changes or disappears.
CREATE TABLE IF NOT EXISTS archive_chatbot_questions (
    id TEXT PRIMARY KEY CHECK(id GLOB 'C[0-9][0-9][0-9]'), position INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('manual','demo')),
    text TEXT NOT NULL, prompt TEXT NOT NULL, hook TEXT NOT NULL DEFAULT '', added TEXT NOT NULL,
    sha256 TEXT NOT NULL, first_run INTEGER NOT NULL REFERENCES archive_chatbot_runs(id)
);
-- HUMAN: answers written by people, collected by hand. A kind of its own: never a chat app, never a model.
CREATE TABLE IF NOT EXISTS archive_human_collections (
    run_id INTEGER PRIMARY KEY REFERENCES archive_chatbot_runs(id),
    display_name TEXT NOT NULL, meta TEXT NOT NULL,
    -- Where, in a few plain words ("Reddit thread"). Never a link, a username or a handle.
    source TEXT NOT NULL,
    collected_on TEXT NOT NULL CHECK(collected_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    replies INTEGER CHECK(replies IS NULL OR replies>=1)
);
CREATE TABLE IF NOT EXISTS archive_human_answers (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES archive_human_collections(run_id),
    question_id TEXT NOT NULL REFERENCES archive_chatbot_questions(id),
    source TEXT NOT NULL CHECK(source IN ('manual','demo')),
    state TEXT NOT NULL CHECK(state IN ('answered','no_answer','withheld')),
    reason TEXT NOT NULL DEFAULT '',
    answer_text TEXT, normalised TEXT, answer_sha256 TEXT,
    -- quoted: one person's words, verbatim. summarised: the owner's summary of several replies.
    manner TEXT CHECK(manner IN ('quoted','summarised')),
    changed INTEGER CHECK(changed IN (0,1)), created_at TEXT NOT NULL,
    UNIQUE (run_id, question_id),
    CHECK ((state='answered') = (answer_text IS NOT NULL)),
    CHECK ((state='answered') = (manner IS NOT NULL))
);
"""
APPEND_ONLY = ("archive_chatbot_runs", "archive_chatbot_conditions", "archive_chatbot_answers", "archive_chatbot_questions",
               "archive_human_collections", "archive_human_answers")
RULES = "".join(
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}\n"
    f"    BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;\n"
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}\n"
    f"    BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;\n" for table in APPEND_ONLY) + """
CREATE INDEX IF NOT EXISTS archive_chatbot_answers_series ON archive_chatbot_answers(chatbot, question_id, run_id);
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
CREATE TRIGGER IF NOT EXISTS archive_human_collections_forward BEFORE INSERT ON archive_human_collections
    WHEN EXISTS (SELECT 1 FROM archive_human_collections c JOIN archive_chatbot_runs r ON r.id=c.run_id
                 WHERE r.id!=NEW.run_id
                   AND r.collected_on>=(SELECT collected_on FROM archive_chatbot_runs WHERE id=NEW.run_id))
    BEGIN SELECT RAISE(ABORT, 'human answers already have a run on this date or a later one'); END;
CREATE TRIGGER IF NOT EXISTS archive_chatbot_answers_kind BEFORE INSERT ON archive_chatbot_answers
    WHEN (SELECT kind FROM archive_chatbot_runs WHERE id=NEW.run_id) IS NOT NEW.source
    BEGIN SELECT RAISE(ABORT, 'chat-app answer source does not match the kind of its run'); END;
CREATE TRIGGER IF NOT EXISTS archive_human_answers_kind BEFORE INSERT ON archive_human_answers
    WHEN (SELECT kind FROM archive_chatbot_runs WHERE id=NEW.run_id) IS NOT NEW.source
    BEGIN SELECT RAISE(ABORT, 'human answer source does not match the kind of its run'); END;
CREATE TRIGGER IF NOT EXISTS archive_chatbot_questions_kind BEFORE INSERT ON archive_chatbot_questions
    WHEN (SELECT kind FROM archive_chatbot_runs WHERE id=NEW.first_run) IS NOT NEW.kind
    BEGIN SELECT RAISE(ABORT, 'chat-app question kind does not match the kind of its run'); END;
-- An answer is an answer to a question of this track's own recorded list.
CREATE TRIGGER IF NOT EXISTS archive_chatbot_answers_own_question BEFORE INSERT ON archive_chatbot_answers
    WHEN NOT EXISTS (SELECT 1 FROM archive_chatbot_questions WHERE id=NEW.question_id)
    BEGIN SELECT RAISE(ABORT, 'a chat-app answer needs a recorded chat-app question'); END;
"""
TABLE_NAMES = APPEND_ONLY


def migrate(cx) -> None:
    """Create the tables and their rules. In place and additive: a database from before this track gains
    empty tables, and one from the track's first shape (questions referring to the locked lists, no
    human column; `tests/fixtures/archive_schema_v5.sql`) gains the `refused` column, the question
    table and the two human tables, and trades the rule "a hand-entered answer needs a locked question"
    for "an answer needs a recorded chat-app question". Nothing recorded is touched."""
    cx.executescript(TABLES)
    if "refused" not in {row[1] for row in cx.execute("PRAGMA table_info(archive_chatbot_answers)")}:
        cx.execute("ALTER TABLE archive_chatbot_answers ADD COLUMN refused INTEGER NOT NULL DEFAULT 0 CHECK(refused IN (0,1))")
    cx.executescript("DROP TRIGGER IF EXISTS archive_chatbot_answers_locked_question;" + RULES)


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


# ---- No politics, no handles, no links: crude word checks --------------------------------------------------

# Obviously political words, refused in a chat-app question or hook when the configuration is loaded. CRUDE on
# purpose: it matches words, not meaning, so it misses a political question that avoids them and would refuse a
# harmless one that uses them. It holds politics only: joke words (roast, destroy, worst, fight) are never here.
POLITICAL_TERMS = (
    "president", "presidents", "presidential", "vice president", "prime minister", "election", "elections", "electoral",
    "vote", "votes", "voted", "voter", "voters", "voting", "ballot", "ballots", "referendum", "politics", "political",
    "politician", "politicians", "government", "governments", "congress", "senate", "senator", "senators", "parliament",
    "democrat", "democrats", "democratic party", "republican", "republicans", "conservative party", "conservatives",
    "labour party", "liberal party", "liberals", "tory", "tories", "left-wing", "right-wing", "left wing", "right wing",
    "communist", "communism", "socialist", "socialism", "fascist", "fascism", "immigration", "abortion", "gun control",
    "white house", "kremlin", "brexit", "nato", "trump", "biden", "putin", "obama", "campaign trail", "political party",
)
_POLITICAL = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(term) for term in sorted(POLITICAL_TERMS, key=len, reverse=True))
                        + r")(?![a-z0-9])")


def political_term(text: str) -> str | None:
    """The first obviously political word in `text` (letter case ignored), or None. Crude; see POLITICAL_TERMS."""
    found = _POLITICAL.search(text.casefold())
    return found.group(0) if found else None


_HANDLE = re.compile(r"(?<![A-Za-z0-9])(?:u/[A-Za-z0-9_-]{2,}|@[A-Za-z0-9_]{2,})")
_LINK = re.compile(r"(?i)\b(?:https?://|www\.)|\b[a-z0-9-]+\.(?:com|org|net|io|co|me|ly|gg|app|dev|uk|de|fr|info|xyz)\b")


def handle_or_link(text: str) -> str | None:
    """Why a text written by or about PEOPLE may not be stored, or None: it holds something that looks like a
    username handle (`u/name`, `@name`) or a web address. Crude, like every pattern check."""
    if _HANDLE.search(text):
        return "username handle"
    if _LINK.search(text):
        return "web address"
    return None


# ---- The configuration file -----------------------------------------------------------------------------

def _canonical(body: Any) -> bytes:
    return json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


QUESTION_KEYS = ("id", "text", "prompt", "hook", "added")


def question_hash(question: dict) -> str:
    """The hash recorded for one question at its first import: id, text, prompt, hook and added date."""
    return hashlib.sha256(_canonical({key: question[key] for key in QUESTION_KEYS})).hexdigest()


def config_hash(chatbots: list[dict], questions: list, human: dict | None = None) -> str:
    """Covers the chat apps, the human column's wording and the questions."""
    return hashlib.sha256(_canonical({"chatbots": chatbots, "questions": questions, "human": human})).hexdigest()


def prompt_of(text: str) -> str:
    return f"{PROMPT_PREFIX}\n\n{text}"


def load_config(path: str | Path | None = None) -> dict:
    """Read and validate `archive_chatbots.json`. Returns {status, chatbots, human, aside, questions, items,
    prompts, sha256}: `chatbots` is a list of {label, name, maker, attribution}; `human` is {name, meta}
    (the wording of the Human column); `aside` one short line of the owner's own (shown once on the
    chat-app page, never on a card; '' when none); `questions` the ids in the file's order; `items` the
    questions ({id, text, prompt, hook, added}); `prompts` maps each id to its question.
    Strict shape; ids C001... in order without gaps; every prompt is PROMPT_PREFIX, a blank line, the
    question; no obviously political word in a question or a hook. Raises ArchiveRefused."""
    source = Path(path) if path is not None else CONFIG_FILE

    def bad(why: str) -> archive.ArchiveRefused:
        return _refused(f"Chat-app configuration {source.name}: {why}")
    try:
        raw = _load_json(source.read_bytes())
    except (OSError, ValueError) as err:
        raise _refused(f"Chat-app configuration {source.name} is missing or not valid JSON ({type(err).__name__})") from None
    if not isinstance(raw, dict) or set(raw) - {"format", "status", "note", "prompt_prefix", "chatbots", "human", "aside", "questions"}:
        raise bad("top level must be an object with format, status, note, prompt_prefix, chatbots, human, aside and questions only")
    if raw.get("format") != CONFIG_FORMAT:
        raise bad(f'format must be "{CONFIG_FORMAT}"')
    if raw.get("status") not in ("draft", "locked"):
        raise bad('status must be "draft" or "locked"')
    if "note" in raw and not isinstance(raw["note"], str):
        raise bad("note must be text")
    if raw.get("prompt_prefix") != PROMPT_PREFIX:
        raise bad("prompt_prefix must be exactly the fixed prefix this version of the code uses: " + PROMPT_PREFIX)
    listed, entries, person, aside_text = raw.get("chatbots"), raw.get("questions"), raw.get("human"), raw.get("aside", "")
    if not isinstance(listed, list) or not 1 <= len(listed) <= MAX_CHATBOTS:
        raise bad(f"chatbots must list 1 to {MAX_CHATBOTS} chat apps")
    chatbots: list[dict] = []
    for item in listed:
        if not isinstance(item, dict) or not {"label", "name", "maker"} <= set(item) <= {"label", "name", "maker", "attribution"}:
            raise bad("each chat app needs label, name and maker, and may have attribution")
        if not isinstance(item["label"], str) or not CHATBOT_LABEL.fullmatch(item["label"]) or item["label"] in RESERVED_LABELS:
            raise bad('a label must be 2-24 characters of a-z 0-9 -, starting with a letter, and not "chatbots" or "human"')
        if "attribution" in item and not _line_ok(item["attribution"], ATTRIBUTION_CHARS):
            raise bad(f"{item['label']}: attribution must be 1-{ATTRIBUTION_CHARS} plain characters on one line")
        if not _line_ok(item["name"], NAME_CHARS) or not _line_ok(item["maker"], NAME_CHARS):
            raise bad(f"{item['label']}: name and maker must be 1-{NAME_CHARS} plain characters")
        chatbots.append({"label": item["label"], "name": item["name"].strip(), "maker": item["maker"].strip(),
                         "attribution": item.get("attribution", "").strip()})
    if not isinstance(person, dict) or set(person) != {"name", "meta"} or not _line_ok(person["name"], NAME_CHARS) \
            or not _line_ok(person["meta"], META_CHARS):
        raise bad(f"human must hold name (1-{NAME_CHARS} characters) and meta (1-{META_CHARS} characters): the wording of the "
                  "column of answers written by people")
    human = {"name": person["name"].strip(), "meta": person["meta"].strip()}
    names = [c["name"].casefold() for c in chatbots] + [human["name"].casefold()]
    if len({c["label"] for c in chatbots}) != len(chatbots) or len(set(names)) != len(names):
        raise bad("labels and names must differ")
    if aside_text != "" and not _line_ok(aside_text, ASIDE_CHARS):
        raise bad(f"aside must be one plain line of at most {ASIDE_CHARS} characters, or empty")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CHATBOT_QUESTIONS:
        raise bad(f"questions must list 1 to {MAX_CHATBOT_QUESTIONS} questions")
    items: list[dict] = []
    for position, entry in enumerate(entries, 1):
        wanted = f"C{position:03d}"
        if not isinstance(entry, dict) or not {"id", "text", "prompt", "added"} <= set(entry) <= set(QUESTION_KEYS):
            raise bad(f"question {position} must hold id, text, prompt and added, and may have hook")
        if entry["id"] != wanted:
            raise bad(f"question {position} must have the id {wanted}: ids are C001, C002, ... in order, without gaps")
        text, hook = entry["text"], entry.get("hook", "")
        if not _line_ok(text, QUESTION_CHARS) or text != text.strip():
            raise bad(f"{wanted}: text must be 1-{QUESTION_CHARS} plain characters on one line")
        if hook != "" and (not _line_ok(hook, HOOK_CHARS) or hook != hook.strip()):
            raise bad(f"{wanted}: hook must be at most {HOOK_CHARS} plain characters on one line, or left out")
        if entry["prompt"] != prompt_of(text):
            raise bad(f"{wanted}: prompt must be exactly the fixed prefix, a blank line, then the question")
        try:
            if not isinstance(entry["added"], str) or not archive.ADDED_DATE.fullmatch(entry["added"]):
                raise ValueError
            datetime.date.fromisoformat(entry["added"])
        except ValueError:
            raise bad(f"{wanted}: added must be a date written YYYY-MM-DD") from None
        term = political_term(text) or political_term(hook)
        if term:
            raise bad(f'{wanted}: holds the word "{term}". This list has no political questions (a crude word check; '
                      "reword the question or leave it out)")
        items.append({"id": wanted, "text": text, "prompt": entry["prompt"], "hook": hook, "added": entry["added"]})
    if len({q["text"].casefold() for q in items}) != len(items):
        raise bad("two questions have the same text")
    return {"status": raw["status"], "chatbots": chatbots, "human": human, "aside": aside_text.strip(),
            "questions": [q["id"] for q in items], "items": items, "prompts": {q["id"]: q for q in items},
            "sha256": config_hash(chatbots, items, human)}


def aside() -> str:
    """The owner's one-line aside for the chat-app page, or '' when the configuration has none or cannot be read."""
    try:
        return load_config()["aside"]
    except archive.ArchiveRefused:
        return ""


# ---- The file the owner fills in (cli archive-chatbot-template) ------------------------------------------

def _date(value: Any, what: str) -> datetime.date:
    try:
        if not isinstance(value, str) or not archive.ADDED_DATE.fullmatch(value):
            raise ValueError
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise _refused(f"{what} must be a date written YYYY-MM-DD") from None


def template(date: str | None = None) -> dict:
    """The file to fill in, as a dict: `collected_on`; per chat app its `conditions` (all empty) and one
    entry per question with the exact prompt to paste and an empty `answer`; and the optional `human`
    block. Reads the configuration only; opens no database and contacts nothing."""
    config = load_config()
    day = _date(date, "--date") if date is not None else today()
    return {"format": FILE_FORMAT, "how_to": list(HOW_TO), "collected_on": day.isoformat(),
            "chatbots": [{"chatbot": c["label"], "name": c["name"], "maker": c["maker"],
                          "conditions": {**dict.fromkeys(CONDITION_KEYS[:-1]), "note": ""},
                          "answers": [{"question": question_id, "prompt": config["prompts"][question_id]["prompt"], "answer": ""}
                                      for question_id in config["questions"]]}
                         for c in config["chatbots"]],
            "human": {"name": config["human"]["name"],
                      "conditions": {"source": "", "collected_on": day.isoformat(), "count": None},
                      "answers": [{"question": question_id, "prompt": config["prompts"][question_id]["prompt"], "answer": "",
                                   "kind": ""} for question_id in config["questions"]]}}


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

def classify_reply(text: Any, limit: int = REPLY_CHARS) -> tuple[str, str, str | None]:
    """(state, reason, text to store) for one pasted reply: the rule for model answers
    (`archive.classify`) with the longer limit of this track. Only leading and trailing white space is
    removed; markdown, citation markers and line breaks are stored as pasted. A reply the character rule
    or the content guards match is `withheld` with the category and no text."""
    if not isinstance(text, str) or not text.strip():
        return "no_answer", archive.EMPTY, None
    text = text.strip()
    if len(text) > limit:
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


ANSWER_KEYS = {"question", "prompt", "answer", "answer_file", "skipped", "refused", "screenshot"}
HUMAN_ANSWER_KEYS = {"question", "prompt", "answer", "kind", "skipped"}
HUMAN_CONDITION_KEYS = ("source", "collected_on", "count")


def _question_of(raw: Any, where: str, keys: set, prompts: dict[str, dict], problems: list[str]) -> tuple[str, str] | None:
    """(question id, place) of one answer entry whose question and prompt are right, or None (problem noted)."""
    if not isinstance(raw, dict) or set(raw) - keys or not isinstance(raw.get("question"), str):
        problems.append(f"{where}: an answer entry must hold question, prompt, answer and optionally "
                        + ", ".join(sorted(keys - {"question", "prompt", "answer"})))
        return None
    question_id = raw["question"]
    where = f"{where}, {question_id if CHAT_QUESTION_ID.fullmatch(question_id) else 'an unknown question'}"
    if question_id not in prompts:
        problems.append(f"{where}: not one of the questions in {CONFIG_FILE.name}")
        return None
    if raw.get("prompt") != prompts[question_id]["prompt"]:
        problems.append(f"{where}: the prompt in the file is not the prompt of {CONFIG_FILE.name}. It must not be edited; "
                        "make a new template")
        return None
    return question_id, where


def _answer(raw: Any, where: str, folder: Path, prompts: dict[str, dict], problems: list[str]) -> dict | None:
    found = _question_of(raw, where, ANSWER_KEYS, prompts, problems)
    if found is None:
        return None
    question_id, where = found
    text, named, skipped, refused = raw.get("answer", ""), raw.get("answer_file", ""), raw.get("skipped", ""), raw.get("refused", False)
    if not isinstance(text, str) or not isinstance(named, str) or not isinstance(skipped, str):
        problems.append(f"{where}: answer, answer_file and skipped must be text")
        return None
    if type(refused) is not bool:
        problems.append(f"{where}: refused must be true or false")
        return None
    given = [bool(text.strip()), bool(named.strip()), bool(skipped.strip())]
    if sum(given) > 1 or (not any(given) and not refused) or (refused and given[2]):
        problems.append(f"{where}: give exactly one of answer, answer_file or skipped"
                        + (" (all are empty)" if not any(given) else "") + '; "refused": true may stand alone or with the reply')
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
                "chars": 0, "screenshot_hash": screenshot_hash, "refused": False}
    if refused and not any(given):
        return {"question": question_id, "state": "no_answer", "reason": REFUSED, "text": None, "chars": 0,
                "screenshot_hash": screenshot_hash, "refused": True}
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
            "screenshot_hash": screenshot_hash, "refused": refused}


def _human_conditions(raw: Any, day: datetime.date, problems: list[str]) -> dict | None:
    if not isinstance(raw, dict) or set(raw) != set(HUMAN_CONDITION_KEYS):
        problems.append("human: conditions must hold exactly " + ", ".join(HUMAN_CONDITION_KEYS))
        return None
    before = len(problems)
    source = raw["source"]
    if not _line_ok(source, HUMAN_SOURCE_CHARS):
        problems.append(f'human: source must say where the answers came from in 1-{HUMAN_SOURCE_CHARS} plain characters, '
                        'e.g. "Reddit thread"')
    elif handle_or_link(source):
        problems.append(f"human: source holds a {handle_or_link(source)}. Say where in plain words; no link and no username")
    collected = None
    try:
        collected = _date(raw["collected_on"], "human: collected_on")
        if collected > day:
            problems.append("human: collected_on is after the file's collected_on")
    except archive.ArchiveRefused as err:
        problems.append(str(err))
    count = raw["count"]
    if count is not None and (type(count) is not int or not 1 <= count <= 100_000):
        problems.append("human: count must be a whole number of replies read, or null")
    if len(problems) != before:
        return None
    return {"source": source.strip(), "collected_on": collected.isoformat(), "count": count}


def _human_answer(raw: Any, prompts: dict[str, dict], problems: list[str]) -> dict | None:
    found = _question_of(raw, "human", HUMAN_ANSWER_KEYS, prompts, problems)
    if found is None:
        return None
    question_id, where = found
    text, manner, skipped = raw.get("answer", ""), raw.get("kind", ""), raw.get("skipped", "")
    if not isinstance(text, str) or not isinstance(manner, str) or not isinstance(skipped, str):
        problems.append(f"{where}: answer, kind and skipped must be text")
        return None
    if bool(text.strip()) == bool(skipped.strip()):
        problems.append(f"{where}: give exactly one of answer or skipped")
        return None
    if skipped.strip():
        if not _line_ok(skipped, SKIP_CHARS):
            problems.append(f"{where}: skipped must be a short plain reason on one line (at most {SKIP_CHARS} characters)")
            return None
        return {"question": question_id, "state": "no_answer", "reason": f"{SKIPPED}: {skipped.strip()}", "text": None,
                "chars": 0, "manner": None}
    if manner not in MANNERS:
        problems.append(f'{where}: kind must be "quoted" (one person\'s words, copied exactly) or "summarised" (your summary of several)')
        return None
    looks = handle_or_link(text)
    if looks:
        problems.append(f"{where}: the answer holds a {looks}. No usernames, handles or links in an answer written by people")
        return None
    state, reason, kept = classify_reply(text, HUMAN_CHARS)
    return {"question": question_id, "state": state, "reason": reason, "text": kept, "chars": len(text.strip()),
            "manner": manner if state == "answered" else None}


def read_collection(path: str | Path, config: dict, *, allow_old: bool = False) -> dict:
    """Read and check a filled-in file completely. Returns {collected_on, file_sha256, chatbots: [{label,
    name, maker, conditions, answers: [{question, state, reason, text, chars, screenshot_hash, refused}]}],
    human: None | {conditions: {source, collected_on, count}, answers: [{question, state, reason, text,
    chars, manner}]}}. Raises ArchiveRefused naming every problem found (place and rule; never the text
    of a reply).

    Checked: strict JSON; the format; `collected_on` (a real date, not in the future, at most MAX_AGE_DAYS
    old unless `allow_old`); at least one chat app or the human block; each app one of the configuration,
    none twice; complete conditions with closed choices and `fresh_chat` true; for each app and for the
    human block exactly the configured questions, each with its prompt unchanged; for an app exactly one
    of `answer`, `answer_file` or `skipped` (or `refused`: true, alone or with the reply); for the human
    block `answer` with `kind`, or `skipped`, and no handle or link."""
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
        raise _refused(f'{source.name} is not a chat-app collection file (format must be "{FILE_FORMAT}"; make a new '
                       "template if it was written by an older version). Nothing was stored.")
    if set(raw) - {"format", "how_to", "collected_on", "chatbots", "human"}:
        raise _refused(f"{source.name}: top level must hold format, how_to, collected_on, chatbots and human only. Nothing was stored.")
    day = _date(raw.get("collected_on"), f"{source.name}: collected_on")
    now = today()
    if day > now:
        raise _refused(f"{source.name}: collected_on {day} is in the future. Nothing was stored.")
    if (now - day).days > MAX_AGE_DAYS and not allow_old:
        raise _refused(f"{source.name}: collected_on {day} is more than {MAX_AGE_DAYS} days ago. If the date is right, "
                       "repeat with --allow-old. Nothing was stored.")
    listed, person = raw.get("chatbots", []), raw.get("human")
    if not isinstance(listed, list) or (not listed and person is None):
        raise _refused(f"{source.name}: chatbots must list at least one chat app, or the file must hold the human block. "
                       "Nothing was stored.")
    known = {c["label"]: c for c in config["chatbots"]}
    order = {question_id: n for n, question_id in enumerate(config["questions"])}
    problems: list[str] = []

    def covered(entries: list, answers: list[dict], where: str) -> None:
        ids = [a["question"] for a in answers]
        if len(entries) == len(answers) and (len(set(ids)) != len(ids) or set(ids) != set(config["questions"])):
            problems.append(f"{where}: the answers must cover exactly the {len(config['questions'])} questions of "
                            f"{CONFIG_FILE.name}, each once (make a new template if the list changed)")
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
        covered(entries, answers, label)
        chatbots.append({**known[label], "conditions": conditions, "answers": sorted(answers, key=lambda a: order[a["question"]])})
    human = None
    if person is not None:
        if not isinstance(person, dict) or set(person) - {"name", "conditions", "answers"} or not isinstance(person.get("answers"), list):
            problems.append("the human block must hold name, conditions and answers only")
        else:
            conditions = _human_conditions(person.get("conditions"), day, problems)
            answers = [found for found in (_human_answer(entry, config["prompts"], problems) for entry in person["answers"])
                       if found is not None]
            covered(person["answers"], answers, "human")
            human = {"conditions": conditions, "answers": sorted(answers, key=lambda a: order[a["question"]])}
    if problems:
        shown = problems[:20] + ([f"... and {len(problems) - 20} more"] if len(problems) > 20 else [])
        raise _refused(f"{source.name} was refused; nothing was stored:\n  " + "\n  ".join(shown))
    return {"collected_on": day.isoformat(), "file_sha256": hashlib.sha256(raw_bytes).hexdigest(), "chatbots": chatbots,
            "human": human}


# ---- Import (free, audited; no model, no network) -------------------------------------------------------

def _has_run(cx, kind: str) -> bool:
    return cx.execute("SELECT 1 FROM archive_chatbot_runs WHERE kind=? LIMIT 1", (kind,)).fetchone() is not None


def _digest(answer: dict) -> tuple[str | None, str | None]:
    if answer["state"] != "answered":
        return None, None
    normalised = archive.normalise(answer["text"])
    return normalised, hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _store(cx, run_id: int, kind: str, collected_on: str, question_set: str, label: str, answer: dict, created_at: str) -> bool | None:
    """Insert one chat-app answer. `changed` compares with this chat app's previous SHOWN reply (answered and
    not marked refused) to the same question on an earlier date, in runs of the same kind: None for the
    first, for a row with no answer and for a refusal."""
    normalised, digest = _digest(answer)
    refused = bool(answer.get("refused"))
    changed = None
    if digest is not None and not refused:
        previous = cx.execute(
            "SELECT a.answer_sha256 FROM archive_chatbot_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
            "WHERE r.kind=? AND r.collected_on<? AND a.chatbot=? AND a.question_id=? AND a.state='answered' AND a.refused=0 "
            "ORDER BY r.collected_on DESC LIMIT 1", (kind, collected_on, label, answer["question"])).fetchone()
        changed = None if previous is None else int(previous[0] != digest)
    cx.execute("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,reason,answer_text,"
               "normalised,answer_sha256,changed,screenshot_sha256,created_at,refused) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (run_id, question_set, answer["question"], label, kind, answer["state"], answer["reason"], answer["text"],
                normalised, digest, changed, answer.get("screenshot_hash"), created_at, int(refused)))
    return None if changed is None else bool(changed)


def _store_human(cx, run_id: int, kind: str, collected_on: str, answer: dict, created_at: str) -> bool | None:
    """Insert one human answer. `changed` says only that the text differs from the collection before."""
    normalised, digest = _digest(answer)
    changed = None
    if digest is not None:
        previous = cx.execute(
            "SELECT a.answer_sha256 FROM archive_human_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
            "WHERE r.kind=? AND r.collected_on<? AND a.question_id=? AND a.state='answered' "
            "ORDER BY r.collected_on DESC LIMIT 1", (kind, collected_on, answer["question"])).fetchone()
        changed = None if previous is None else int(previous[0] != digest)
    cx.execute("INSERT INTO archive_human_answers(run_id,question_id,source,state,reason,answer_text,normalised,answer_sha256,"
               "manner,changed,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (run_id, answer["question"], kind, answer["state"], answer["reason"], answer["text"], normalised, digest,
                answer["manner"], changed, created_at))
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


def _store_human_collection(cx, run_id: int, human: dict, conditions: dict) -> None:
    cx.execute("INSERT INTO archive_human_collections(run_id,display_name,meta,source,collected_on,replies) VALUES (?,?,?,?,?,?)",
               (run_id, human["name"], human["meta"], conditions["source"], conditions["collected_on"], conditions["count"]))


def _record_questions(cx, run_id: int, kind: str, items: list[dict]) -> list[str]:
    """Record the questions this run is the first to hold; returns their ids. The caller has already
    checked that every recorded question is still in `items`, unchanged."""
    known = {row[0] for row in cx.execute("SELECT id FROM archive_chatbot_questions")}
    new = [q for q in items if q["id"] not in known]
    for question in new:
        cx.execute("INSERT INTO archive_chatbot_questions(id,position,kind,text,prompt,hook,added,sha256,first_run) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (question["id"], int(question["id"][1:]), kind, question["text"], question["prompt"], question["hook"],
                    question["added"], question_hash(question), run_id))
    return [q["id"] for q in new]


def questions_problem(cx, config: dict) -> str | None:
    """Why the configuration's question list does not fit what this database recorded, or None: a recorded
    question is gone, or its text, prompt, hook or date was changed."""
    recorded = {row["id"]: row["sha256"] for row in cx.execute("SELECT id,sha256 FROM archive_chatbot_questions WHERE kind='manual'")}
    dropped = sorted(set(recorded) - set(config["questions"]))
    if dropped:
        return (f"{CONFIG_FILE.name} no longer lists {', '.join(dropped)}, which the first import recorded. "
                "A recorded question never disappears: restore it")
    edited = sorted(i for i in recorded if question_hash(config["prompts"][i]) != recorded[i])
    if edited:
        return (f"{CONFIG_FILE.name}: {', '.join(edited)} was changed since it was first imported (text, prompt, hook or "
                "date). A recorded question never changes: restore it and add a new question instead")
    return None


def import_collection(path: str | Path, *, dry_run: bool = False, allow_old: bool = False) -> dict:
    """`cli archive-chatbot-import FILE`: check a filled-in file and store it as one hand-entered run.
    Free: no model, no network, no key, no budget hold. Audited. All or nothing: the whole file is
    checked before the first row is written, and `dry_run` stops there and reports what would be stored.
    The core and trending lists need not be locked: this track has its own questions.

    Refused (ArchiveRefused; nothing stored) when: the file fails `read_collection`; the database holds
    scripted demo answers; the configuration dropped or changed a question an earlier import recorded
    (questions are only added); a chat app in the file, or the human block, already has a run on that
    date or a later one.

    The summary holds counts and lengths only, never the text of a reply."""
    config = load_config()
    collection = read_collection(path, config, allow_old=allow_old)
    day = collection["collected_on"]
    imported_at = core.utcnow()
    with core.connect() as cx:
        cx.execute("BEGIN IMMEDIATE")
        if archive._has(cx, "demo") or _has_run(cx, DEMO):
            raise _refused("This database holds scripted demo answers; hand-entered replies are never mixed with them. "
                           "Use the real database. Nothing was stored.")
        problem = questions_problem(cx, config)
        if problem:
            raise _refused(f"{problem}. Nothing was stored.")
        for chatbot in collection["chatbots"]:
            latest = cx.execute("SELECT MAX(r.collected_on) FROM archive_chatbot_conditions c JOIN archive_chatbot_runs r "
                                "ON r.id=c.run_id WHERE c.chatbot=? AND r.kind='manual'", (chatbot["label"],)).fetchone()[0]
            if latest == day:
                raise _refused(f"{chatbot['label']} already has a hand-entered run dated {day}. One run per chat app and day; "
                               "a stored reply is never replaced. Nothing was stored.")
            if latest is not None and latest > day:
                raise _refused(f"{chatbot['label']} already has a hand-entered run dated {latest}, after {day}. A chat app's "
                               "dates only move forward. Nothing was stored.")
        if collection["human"]:
            latest = cx.execute("SELECT MAX(r.collected_on) FROM archive_human_collections c JOIN archive_chatbot_runs r "
                                "ON r.id=c.run_id WHERE r.kind='manual'").fetchone()[0]
            if latest is not None and latest >= day:
                raise _refused(f"Human answers already have a hand-entered run dated {latest}. One collection per day, and the "
                               "dates only move forward. Nothing was stored.")
        first = not _has_run(cx, MANUAL)
        summary = {"dry_run": dry_run, "collected_on": day, "imported_at": imported_at, "questions": config["questions"],
                   "config_sha256": config["sha256"], "first_import": first, "chatbots": []}
        run_id = number = None
        if not dry_run:
            run_id, number = _open_run(cx, MANUAL, day, config["questions"], config["sha256"], collection["file_sha256"], imported_at)
            summary["new_questions"] = _record_questions(cx, run_id, MANUAL, config["items"])
        for chatbot in collection["chatbots"]:
            if not dry_run:
                _store_conditions(cx, run_id, chatbot, chatbot["conditions"])
            counts = {state: 0 for state in archive.STATES}
            rows = []
            for answer in chatbot["answers"]:
                counts[answer["state"]] += 1
                changed = None if dry_run else _store(cx, run_id, MANUAL, day, QUESTION_SET, chatbot["label"], answer, imported_at)
                row = {"question": answer["question"], "state": answer["state"], "characters": answer["chars"],
                       "screenshot": answer["screenshot_hash"] is not None, "refused": answer["refused"]}
                if answer["state"] == "withheld" or (answer["state"] == "no_answer" and not answer["reason"].startswith(SKIPPED)):
                    row["reason"] = answer["reason"]      # a category, never the reply
                if not dry_run:
                    row["changed"] = changed
                rows.append(row)
            summary["chatbots"].append({"chatbot": chatbot["label"], "conditions": chatbot["conditions"], "answers": counts,
                                        "skipped": sum(1 for a in chatbot["answers"] if a["reason"].startswith(SKIPPED)),
                                        "refused": sum(1 for a in chatbot["answers"] if a["refused"]),
                                        "shown": sum(1 for a in chatbot["answers"] if a["state"] == "answered" and not a["refused"]),
                                        "rows": rows})
        human = collection["human"]
        if human:
            if not dry_run:
                _store_human_collection(cx, run_id, config["human"], human["conditions"])
            counts = {state: 0 for state in archive.STATES}
            rows = []
            for answer in human["answers"]:
                counts[answer["state"]] += 1
                changed = None if dry_run else _store_human(cx, run_id, MANUAL, day, answer, imported_at)
                row = {"question": answer["question"], "state": answer["state"], "characters": answer["chars"], "kind": answer["manner"]}
                if answer["state"] == "withheld" or (answer["state"] == "no_answer" and not answer["reason"].startswith(SKIPPED)):
                    row["reason"] = answer["reason"]
                if not dry_run:
                    row["different"] = changed
                rows.append(row)
            summary["human"] = {"conditions": human["conditions"], "answers": counts, "rows": rows}
        if not dry_run:
            summary["run"] = number
            total = sum(len(c["answers"]) for c in collection["chatbots"]) + (len(human["answers"]) if human else 0)
            who = [c["label"] for c in collection["chatbots"]] + ([HUMAN_LABEL] if human else [])
            controls.audit(cx, f"archive-chatbot-import run {number}: collected {day}, {','.join(who)}, {total} rows, "
                               f"config sha256 {config['sha256'][:16]}")
    return summary


# ---- Scripted demo (part of cli demo-archive): MADE-UP names only ------------------------------------------

DEMO_APPS = (("chat-app-a", "Chat app A", "Made-up maker A"), ("chat-app-b", "Chat app B", "Made-up maker B"),
             ("chat-app-c", "Chat app C", "Made-up maker C"))
DEMO_HUMAN = {"name": "Human (sample)", "meta": "sample we wrote · no person was asked"}
DEMO_HUMAN_SOURCE = "Scripted sample"
DEMO_ATTRIBUTION = {"chat-app-c": "Written with Chat app C"}   # one made-up app carries an attribution line, to show it
DEMO_DATES = ("2026-08-03", "2026-09-01", "2026-10-01", "2026-11-02")
DEMO_FIRST_RUN = {"chat-app-c": 2, HUMAN_LABEL: 2}       # these join at the second scripted date
_BASE = {"fresh_chat": True, "country": "US", "note": ""}
# Per app: ((from run, conditions), ...). chat-app-a switches search on at run 3; chat-app-b only shows another
# model name at run 4 (not a change of conditions); chat-app-c moves country at run 4.
DEMO_CONDITIONS = {
    "chat-app-a": ((1, {**_BASE, "account": "free_account", "temporary_chat": True, "memory_off": True, "web_search": "off",
                        "app": "web", "shown_model": "A-1"}),
                   (3, {**_BASE, "account": "free_account", "temporary_chat": True, "memory_off": True, "web_search": "on",
                        "app": "web", "shown_model": "A-1", "note": "Scripted: search was switched on from this date."})),
    "chat-app-b": ((1, {**_BASE, "account": "free_account", "temporary_chat": True, "memory_off": NOT_APPLICABLE,
                        "web_search": "unknown", "app": "web", "shown_model": "B mini"}),
                   (4, {**_BASE, "account": "free_account", "temporary_chat": True, "memory_off": NOT_APPLICABLE,
                        "web_search": "unknown", "app": "web", "shown_model": "B mini 2"})),
    "chat-app-c": ((2, {**_BASE, "account": "free_account", "temporary_chat": True, "memory_off": True, "web_search": "off",
                        "app": "mobile", "shown_model": "not shown"}),
                   (4, {**_BASE, "account": "free_account", "temporary_chat": True, "memory_off": True, "web_search": "off",
                        "app": "mobile", "shown_model": "not shown", "country": "GB"})),
}
# The scripted question list (its own; not the configuration file's) and, per question, per answerer,
# ((from run, scripted reply), ...). Written by hand; no app and no person was asked. For "human" a step is
# (from run, text, quoted | summarised).
DEMO_QUESTIONS = (
    ("Is a hot dog a sandwich?", "Hot dog: sandwich or not?", {
        "chat-app-a": ((1, "Technically yes, but say it out loud at a barbecue and see what happens."),
                       (3, "No. It's a taco that went to a baseball game.")),
        "chat-app-b": ((1, "It's bread around a filling, so yes, and I will not be taking questions."),),
        "chat-app-c": ((2, "A hot dog is a hot dog. It answers to no one."),),
        HUMAN_LABEL: ((2, "No, and if you serve me one cut in triangles we're done.", "quoted"),)}),
    ("Roast the other AI apps in one sentence.", "AI vs AI: the roast", {
        "chat-app-a": ((1, "They're like me, but they apologise more and know less about hot dogs."),),
        "chat-app-b": ((1, "One talks too much, one hedges everything, and one thinks it's funny."),
                       (4, "They all say 'great question' to questions that were not great.")),
        "chat-app-c": ((2, "Bold of them to call it 'thinking' when it's just a loading spinner with opinions."),),
        HUMAN_LABEL: ((2, "They all sound like a coworker who just got back from a leadership retreat.", "quoted"),
                      (4, "Most replies: confident, wordy, and wrong about what day it is.", "summarised"))}),
    ("Pineapple on pizza: defend it or destroy it.", "Pineapple on pizza. Pick a side.", {
        "chat-app-a": ((1, "Defend. Sweet plus salty is science, and science is delicious."),),
        "chat-app-b": ((1, "Destroy. Fruit had the whole dessert menu and still wanted more."),
                       (3, "Defend. I was wrong and the ham has forgiven me.")),
        "chat-app-c": ((2, "Defend it, but only so I can watch the comments."),),
        HUMAN_LABEL: ((2, "Split almost down the middle, with the 'destroy' side typing in capitals.", "summarised"),)}),
    ("Is Monday really necessary?", "Do we even need Monday?", {
        "chat-app-a": ((1, "Yes, as a warning."),),
        "chat-app-b": ((1, "Sadly yes: without Monday, Tuesday becomes Monday and nobody wins."),),
        "chat-app-c": ((2, "No. Start the week on Tuesday and call it a software update."), (4, "Only for the people who invented it.")),
        HUMAN_LABEL: ((2, "No. Next question.", "quoted"),)}),
    ("Which would you rather fight: one horse-sized duck or a hundred duck-sized horses?", "Giant duck or tiny horses?", {
        "chat-app-a": ((1, "The tiny horses. I'd lose, but it would be adorable."),),
        "chat-app-b": ((1, "One giant duck. One problem, one plan, one very large pond to avoid."),),
        "chat-app-c": ((2, "A hundred tiny horses, and then I'd open a very small ranch."),),
        HUMAN_LABEL: ((2, "Most people picked the tiny horses and then asked if they could keep them.", "summarised"),)}),
    ("Are you smarter than a ten-year-old?", "Smarter than a ten-year-old?", {
        "chat-app-a": ((1, "At trivia, yes. At knowing when to stop talking, absolutely not."),),
        "chat-app-b": ((1, "I know more facts, but a ten-year-old knows what a hug is for."),
                       (3, "Depends: is the ten-year-old allowed to ask 'why' more than five times?")),
        "chat-app-c": ((2, "Yes, until they ask me to draw a hand."),),
        HUMAN_LABEL: ((2, "My ten-year-old beat it at tic-tac-toe, so no.", "quoted"),)}),
)
# (question position, answerer, run) scripted as a reply that is stored and never shown, so the "left out" line is real.
DEMO_SKIPPED = {(4, "chat-app-b", 3): f"{SKIPPED}: the app showed an error (scripted)"}
DEMO_REFUSED = {(2, "chat-app-a", 4): "I'd rather not say anything unkind about anyone."}


def demo_items() -> list[dict]:
    """The scripted question list as question dicts (ids C001...)."""
    return [{"id": f"C{n:03d}", "text": text, "prompt": prompt_of(text), "hook": hook, "added": DEMO_DATES[0]}
            for n, (text, hook, _script) in enumerate(DEMO_QUESTIONS, 1)]


def _at(steps: tuple, run: int):
    return [step for step in steps if step[0] <= run][-1]


def demo(cx, sets: list[dict] | None = None) -> dict:
    """Scripted chat-app runs for `cli demo-archive`, written inside its transaction. Three made-up apps
    and "Human (sample)", four dated runs, the scripted question list above, every row source `demo`.
    No app and no person was asked. `sets` is ignored (the track has its own questions)."""
    items = demo_items()
    ids = [q["id"] for q in items]
    apps = [{"label": label, "name": name, "maker": maker, "attribution": DEMO_ATTRIBUTION.get(label, "")}
            for label, name, maker in DEMO_APPS]
    digest = config_hash(apps, items, DEMO_HUMAN)
    written = 0
    for number, day in enumerate(DEMO_DATES, 1):
        stamp = f"{day}T12:00:00+00:00"
        run_id, _ = _open_run(cx, DEMO, day, ids, digest, "", stamp)
        _record_questions(cx, run_id, DEMO, items)
        for app in apps:
            label = app["label"]
            if DEMO_FIRST_RUN.get(label, 1) > number:
                continue
            _store_conditions(cx, run_id, app, _at(DEMO_CONDITIONS[label], number)[1])
            for position, (question, (_text, _hook, script)) in enumerate(zip(items, DEMO_QUESTIONS), 1):
                skipped, refusal = DEMO_SKIPPED.get((position, label, number)), DEMO_REFUSED.get((position, label, number))
                if skipped:
                    answer = {"question": question["id"], "state": "no_answer", "reason": skipped, "text": None}
                else:
                    state, reason, kept = classify_reply(refusal or _at(script[label], number)[1])
                    answer = {"question": question["id"], "state": state, "reason": reason, "text": kept, "refused": bool(refusal)}
                _store(cx, run_id, DEMO, day, QUESTION_SET, label, answer, stamp)
                written += 1
        if DEMO_FIRST_RUN[HUMAN_LABEL] <= number:
            _store_human_collection(cx, run_id, DEMO_HUMAN, {"source": DEMO_HUMAN_SOURCE, "collected_on": day, "count": None})
            for question, (_text, _hook, script) in zip(items, DEMO_QUESTIONS):
                _from, text, manner = _at(script[HUMAN_LABEL], number)
                state, reason, kept = classify_reply(text, HUMAN_CHARS)
                _store_human(cx, run_id, DEMO, day, {"question": question["id"], "state": state, "reason": reason, "text": kept,
                                                    "manner": manner if state == "answered" else None}, stamp)
                written += 1
    return {"chatbot_runs": len(DEMO_DATES), "chatbots": [label for label, _name, _maker in DEMO_APPS], "chatbot_answers": written}


# ---- Public payload: the `chatbots` block of archive.public_payload ---------------------------------------

BLOCK_KEYS = ("apps", "human", "questions", "runs", "answers", "human_answers")
APP_KEYS = ("label", "name", "maker", "attribution")
HUMAN_KEYS = ("name", "meta")
RUN_KEYS = ("number", "collected_on", "imported_at", "demo", "config_hash", "questions", "conditions", "human")
ANSWER_KEYS_PUBLIC = ("id", "run", "question", "chatbot", "source", "state", "reason", "text", "changed", "screenshot_hash", "refused")
HUMAN_ANSWER_KEYS_PUBLIC = ("id", "run", "question", "source", "state", "reason", "text", "manner", "changed")


def public_block(cx, tables: set[str]) -> dict | None:
    """The chat-app track for the pages and the export, or None when the database holds no such run (the
    payload then has no `chatbots` key at all and is what it always was). Runs are ordered by the day
    the owner asked. `questions` are the recorded questions (id, text, prompt, hook, added); `human` is
    the Human column's wording ({name, meta}) or None when no human answer was ever collected; each run
    carries the apps' `conditions` and `human` ({source, collected_on, count} or None). A reply the owner
    marked as a refusal is here (`refused`: true) and on no page. Never the imported file's hash or any
    path; a screenshot is its sha256 only."""
    if not set(TABLE_NAMES) <= tables:
        return None
    questions = [{key: row[key] for key in QUESTION_KEYS}
                 for row in cx.execute("SELECT * FROM archive_chatbot_questions ORDER BY position")]
    own = {q["id"] for q in questions}
    # A run from the track's first shape (its ids referred to the core and trending lists) stays in the database and is
    # never shown: it has no question of this track's own list.
    runs = [dict(r) for r in cx.execute("SELECT * FROM archive_chatbot_runs ORDER BY collected_on,number")
            if set(json.loads(r["question_ids"])) <= own]
    if not runs:
        return None
    number = {r["id"]: r["number"] for r in runs}
    conditions: dict[int, dict] = {}
    apps: dict[str, dict] = {}
    for row in cx.execute("SELECT c.* FROM archive_chatbot_conditions c JOIN archive_chatbot_runs r ON r.id=c.run_id "
                          "ORDER BY r.collected_on,r.number,c.rowid"):
        row = dict(row)
        if row["run_id"] not in number:
            continue
        memory = row["memory_off"]
        conditions.setdefault(row["run_id"], {})[row["chatbot"]] = {
            "account": row["account"], "fresh_chat": bool(row["fresh_chat"]), "temporary_chat": bool(row["temporary_chat"]),
            "memory_off": memory if memory == NOT_APPLICABLE else memory == "true", "web_search": row["web_search"],
            "country": row["country"], "app": row["app"], "shown_model": row["shown_model"], "note": row["note"]}
        known = apps.setdefault(row["chatbot"], {"label": row["chatbot"]})
        known.update(name=row["display_name"], maker=row["maker"], attribution=row["attribution"])   # the newest run's wording
    human, collected = None, {}
    for row in cx.execute("SELECT c.* FROM archive_human_collections c JOIN archive_chatbot_runs r ON r.id=c.run_id "
                          "ORDER BY r.collected_on,r.number"):
        collected[row["run_id"]] = {"source": row["source"], "collected_on": row["collected_on"], "count": row["replies"]}
        human = {"name": row["display_name"], "meta": row["meta"]}                                   # the newest run's wording
    answers = [{"id": a["id"], "run": number[a["run_id"]], "question": a["question_id"], "chatbot": a["chatbot"],
                "source": a["source"], "state": a["state"], "reason": a["reason"], "text": a["answer_text"],
                "changed": None if a["changed"] is None else bool(a["changed"]), "screenshot_hash": a["screenshot_sha256"],
                "refused": bool(a["refused"])}
               for a in (dict(r) for r in cx.execute(
                   "SELECT a.* FROM archive_chatbot_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
                   "ORDER BY r.collected_on,r.number,a.id")) if a["run_id"] in number]
    human_answers = [{"id": a["id"], "run": number[a["run_id"]], "question": a["question_id"], "source": a["source"],
                      "state": a["state"], "reason": a["reason"], "text": a["answer_text"], "manner": a["manner"],
                      "changed": None if a["changed"] is None else bool(a["changed"])}
                     for a in (dict(r) for r in cx.execute(
                         "SELECT a.* FROM archive_human_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
                         "ORDER BY r.collected_on,r.number,a.id"))]
    block = {"apps": list(apps.values()), "human": human, "questions": questions,
             "runs": [{"number": r["number"], "collected_on": r["collected_on"], "imported_at": r["imported_at"],
                       "demo": r["kind"] == DEMO, "config_hash": r["config_sha256"], "questions": json.loads(r["question_ids"]),
                       "conditions": conditions.get(r["id"], {}), "human": collected.get(r["id"])} for r in runs],
             "answers": answers, "human_answers": human_answers}
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


def _text_problem(answer: dict, limit: int) -> bool:
    text = answer.get("text")
    return (answer["state"] == "answered") != isinstance(text, str) or (answer["state"] != "answered" and text is not None) \
        or (isinstance(text, str) and not 0 < len(text) <= limit)


def block_problem(block: Any, question_ids: set[str] | None = None) -> str | None:
    """Why the `chatbots` block of a payload is malformed, or None. Shape only; what may be PUBLISHED is
    `export_problem`. The block is self-contained: its questions are its own (`question_ids` is ignored).
    An answer must belong to a question in its run's recorded id list and to a chat app whose conditions
    that run recorded; a human answer to a run that recorded a human collection."""
    if not isinstance(block, dict) or any(not isinstance(block.get(key), list) for key in BLOCK_KEYS if key != "human") \
            or "human" not in block:
        return "chatbots: a key is missing or has the wrong type"
    labels: list[str] = []
    for app in block["apps"]:
        if not isinstance(app, dict) or not all(isinstance(app.get(key), str) for key in APP_KEYS) \
                or not CHATBOT_LABEL.fullmatch(app["label"]) or app["label"] in labels or app["label"] in RESERVED_LABELS \
                or not all(0 < len(app[key]) <= NAME_CHARS for key in ("name", "maker")) or len(app["attribution"]) > ATTRIBUTION_CHARS:
            return "chatbots: a chat app lacks its label, name, maker or attribution, or a label repeats"
        labels.append(app["label"])
    human = block["human"]
    if human is not None and (not isinstance(human, dict) or not isinstance(human.get("name"), str) or not isinstance(human.get("meta"), str)
                              or not 0 < len(human["name"]) <= NAME_CHARS or not 0 < len(human["meta"]) <= META_CHARS):
        return "chatbots: the human column lacks its name or meta line"
    ids: list[str] = []
    for position, question in enumerate(block["questions"], 1):
        if not isinstance(question, dict) or not all(isinstance(question.get(key), str) for key in QUESTION_KEYS) \
                or question["id"] != f"C{position:03d}" or not 0 < len(question["text"]) <= QUESTION_CHARS \
                or len(question["hook"]) > HOOK_CHARS or not question["prompt"].endswith("\n\n" + question["text"]) \
                or len(question["prompt"]) > QUESTION_CHARS + 400 or not archive.ADDED_DATE.fullmatch(question["added"]) \
                or "\n" in question["text"] or "\n" in question["hook"]:
            return "chatbots: a question lacks a field, its id is out of order, or its prompt does not end with the question"
        ids.append(question["id"])
    if len(ids) > MAX_CHATBOT_QUESTIONS:
        return "chatbots: too many questions"
    runs: dict[int, dict] = {}
    dated: set[tuple[str, str]] = set()
    for run in block["runs"]:
        if not isinstance(run, dict) or type(run.get("number")) is not int or run["number"] in runs \
                or not isinstance(run.get("collected_on"), str) or not archive.ADDED_DATE.fullmatch(run["collected_on"]) \
                or not isinstance(run.get("imported_at"), str) or not archive._DATE.fullmatch(run["imported_at"]) \
                or type(run.get("demo")) is not bool or not isinstance(run.get("config_hash"), str) or not HASH.fullmatch(run["config_hash"]):
            return "chatbots: a run lacks its number, dates, demo flag or configuration hash"
        asked, conditions, person = run.get("questions"), run.get("conditions"), run.get("human", False)
        if not isinstance(asked, list) or not asked or len(set(map(str, asked))) != len(asked) or not all(i in ids for i in asked):
            return "chatbots: a run's question list is missing or names a question that is not in the track's list"
        if not isinstance(conditions, dict) or not all(label in labels for label in conditions):
            return "chatbots: a run lacks its conditions or names an unknown chat app"
        if person is False or (person is not None and (
                human is None or not isinstance(person, dict) or set(person) != set(HUMAN_CONDITION_KEYS)
                or not isinstance(person["source"], str) or not 0 < len(person["source"]) <= HUMAN_SOURCE_CHARS
                or not isinstance(person["collected_on"], str) or not archive.ADDED_DATE.fullmatch(person["collected_on"])
                or not (person["count"] is None or (type(person["count"]) is int and person["count"] >= 1)))):
            return "chatbots: a run's human collection is missing or malformed"
        if not conditions and person is None:
            return "chatbots: a run holds neither a chat app nor a human collection"
        for label, value in conditions.items():
            problem = conditions_problem(value)
            if problem:
                return f"chatbots: {problem}"
        for label in list(conditions) + ([HUMAN_LABEL] if person else []):
            if (label, run["collected_on"]) in dated:
                return "chatbots: a chat app has two runs on one date"
            dated.add((label, run["collected_on"]))
        runs[run["number"]] = run
    if len({run["demo"] for run in runs.values()}) > 1:
        return "chatbots: scripted and hand-entered runs are mixed"
    if human is not None and not any(run["human"] for run in runs.values()):
        return "chatbots: a human column without a human collection"
    seen: set[tuple] = set()
    for answer in block["answers"]:
        if not isinstance(answer, dict) or type(answer.get("id")) is not int or answer.get("run") not in runs \
                or answer.get("source") not in SOURCES or answer.get("state") not in archive.STATES \
                or not isinstance(answer.get("reason"), str) or answer.get("changed") not in (None, True, False) \
                or not isinstance(answer.get("question"), str) or not isinstance(answer.get("chatbot"), str) \
                or type(answer.get("refused")) is not bool:
            return "chatbots: an answer has a missing or invalid field"
        run = runs[answer["run"]]
        if answer["question"] not in run["questions"]:
            return "chatbots: an answer belongs to a question that is not in its run's recorded list"
        if answer["chatbot"] not in run["conditions"]:
            return "chatbots: an answer belongs to a chat app whose conditions its run did not record"
        if (answer["source"] == DEMO) != run["demo"]:
            return "chatbots: an answer's source does not fit its run"
        if _text_problem(answer, REPLY_CHARS):
            return "chatbots: an answer's text does not fit its state or is longer than the archive stores"
        if answer["refused"] and answer["changed"] is not None:
            return "chatbots: a refused reply carries a changed flag"
        shot = answer.get("screenshot_hash")
        if shot is not None and not (isinstance(shot, str) and HASH.fullmatch(shot)):
            return "chatbots: a screenshot is not given as a sha256 hash"
        key = (answer["run"], answer["question"], answer["chatbot"])
        if key in seen:
            return "chatbots: two answers share one run, question and chat app"
        seen.add(key)
    for answer in block["human_answers"]:
        if not isinstance(answer, dict) or type(answer.get("id")) is not int or answer.get("run") not in runs \
                or answer.get("source") not in SOURCES or answer.get("state") not in archive.STATES \
                or not isinstance(answer.get("reason"), str) or answer.get("changed") not in (None, True, False) \
                or not isinstance(answer.get("question"), str) or answer.get("manner") not in (None, *MANNERS):
            return "chatbots: a human answer has a missing or invalid field"
        run = runs[answer["run"]]
        if answer["question"] not in run["questions"] or not run["human"]:
            return "chatbots: a human answer belongs to a run that recorded no human collection, or to a question it did not ask"
        if (answer["source"] == DEMO) != run["demo"]:
            return "chatbots: a human answer's source does not fit its run"
        if _text_problem(answer, HUMAN_CHARS) or (answer["state"] == "answered") != (answer["manner"] is not None):
            return "chatbots: a human answer's text or its quoted/summarised mark does not fit its state"
        if isinstance(answer.get("text"), str) and handle_or_link(answer["text"]):
            return "chatbots: a human answer holds a username handle or a web address"
        key = (answer["run"], answer["question"], HUMAN_LABEL)
        if key in seen:
            return "chatbots: two human answers share one run and question"
        seen.add(key)
    return None


def closed_problem(block: dict) -> str | None:
    """For a file that is to be published: every object of the block holds exactly the keys
    `public_block` writes. `block` already passed block_problem."""
    checks = [("chatbots", block, BLOCK_KEYS)]
    checks += [("a chat app", app, APP_KEYS) for app in block["apps"]]
    checks += [("the human column", block["human"], HUMAN_KEYS)] if block["human"] is not None else []
    checks += [(f"chat-app question {q['id']}", q, QUESTION_KEYS) for q in block["questions"]]
    checks += [(f"chat-app run {run['number']}", run, RUN_KEYS) for run in block["runs"]]
    checks += [(f"chat-app answer id {a['id']}", a, ANSWER_KEYS_PUBLIC) for a in block["answers"]]
    checks += [(f"human answer id {a['id']}", a, HUMAN_ANSWER_KEYS_PUBLIC) for a in block["human_answers"]]
    for place, node, required in checks:
        problem = archive._keys_problem(place, node, required)
        if problem:
            return problem
    return None


def export_problem(block: dict) -> str | None:
    """Why a well-formed block may not be PUBLISHED, or None (snapshot --export / --check): scripted
    rows are never reviewed data; every chat app must be one of the checked-in configuration, under the
    name and maker that file gives; the human column must carry that file's wording; and every question
    must be a question of that file, word for word (so none holds a word the file's own check refuses).
    `block` already passed block_problem."""
    if any(run["demo"] for run in block["runs"]) or any(a["source"] != MANUAL for a in block["answers"] + block["human_answers"]):
        return "it holds scripted chat-app answers; the demo is published without review"
    try:
        config = load_config()
    except archive.ArchiveRefused as err:
        return f"the chat-app configuration cannot be read ({err})"
    known = {c["label"]: c for c in config["chatbots"]}
    for app in block["apps"]:
        if app["label"] not in known:
            return f"chat app {app['label']} is not in {CONFIG_FILE.name}"
        if any(app[key] != known[app["label"]][key] for key in ("name", "maker", "attribution")):
            return f"chat app {app['label']} does not carry the name, maker and attribution {CONFIG_FILE.name} gives it"
    if block["human"] is not None and block["human"] != config["human"]:
        return f"the human column does not carry the name and meta line {CONFIG_FILE.name} gives it"
    for question in block["questions"]:
        if config["prompts"].get(question["id"]) != question:
            return f"chat-app question {question['id']} is not the question {CONFIG_FILE.name} holds under that id"
    return closed_problem(block)


def status(cx) -> dict:
    """For `archive-status`: the configuration's state and the hand-entered runs (numbers and counts only)."""
    try:
        config = load_config()
        state: dict = {"status": config["status"], "chatbots": [c["label"] for c in config["chatbots"]],
                       "human": config["human"]["name"], "questions": config["questions"], "sha256": config["sha256"],
                       "problem": questions_problem(cx, config)}
    except archive.ArchiveRefused as err:
        state = {"error": str(err)}
    runs = [dict(r) for r in cx.execute(
        "SELECT r.number,r.kind,r.collected_on,r.imported_at,"
        "(SELECT COUNT(*) FROM archive_chatbot_answers a WHERE a.run_id=r.id) AS answers,"
        "(SELECT COUNT(*) FROM archive_human_answers h WHERE h.run_id=r.id) AS human_answers "
        "FROM archive_chatbot_runs r ORDER BY r.collected_on,r.number")]
    recorded = [row[0] for row in cx.execute("SELECT id FROM archive_chatbot_questions ORDER BY position")]
    return {"config_file": state, "recorded_questions": recorded, "runs": runs}


# ---- CLI glue ---------------------------------------------------------------------------------------------

COMMANDS = ("archive-chatbot-template", "archive-chatbot-import")
IMPORT_NOTE = ("Entered by hand: these replies cannot be verified and are labelled so on every page. They are published "
               "only through the reviewed path: python -m live_genesis.snapshot --export --archive-only")


def add_parsers(sub) -> None:
    template_p = sub.add_parser("archive-chatbot-template", help="Answers Over Time, chat apps: write the file the owner fills in BY HAND (each app, each question's exact prompt, empty answers, the optional human block). Free; no model, no network, no database")
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
        print(f"This was the first hand-entered import: from now on the questions in {CONFIG_FILE.name} may only be added to; "
              "a recorded question never changes.")
    print(IMPORT_NOTE)
