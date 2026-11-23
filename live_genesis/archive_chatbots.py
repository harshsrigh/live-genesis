"""Answers Over Time, the chat-app track: the same questions typed into consumer chat apps BY HAND, and,
as a second kind of answerer in a group of its own, put to MODELS by software (the last point below).

What this module is, honestly:
* The OWNER opens each chat app, starts a new chat, pastes the question's exact prompt and copies the
  reply into a file. `cli archive-chatbot-template` writes that file to fill in; `cli
  archive-chatbot-import` checks it and stores it. Neither contacts a chat app, a model or the
  network. Nothing here contacts a chat APP, and nothing here may ever be made to: no automated
  collection from an app of any kind.
* A hand-entered reply cannot be verified. There is no receipt, no token count and no model id beyond
  the name the app displayed. The published pages and files do not say how a reply was collected (owner decision).
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
  reply never takes part in `changed`. Its words (and the note behind a skipped one) stay in the database and
  are in no published file: the public payload carries the fact only (`_public_answer`).
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
* A SECOND KIND OF ANSWERER, since 14 November 2026: a MODEL ASKED BY SOFTWARE. It is never a chat app
  and never carries an app's name: the configuration refuses a model answerer whose name holds a
  configured app's name. An entry of `archive_chatbots.json` with `"kind": "model"` names a label of
  LIVE_ARCHIVE_MODELS (`answerer`); `cli archive-chatbot-ask` (PAID class, the safety path of
  `archive-run`: `archive._paid_call`) puts every question's exact prompt to each such model once and
  stores the replies as one run of kind `asked`, source `asked`. Instead of an app's conditions such a
  run records the requested model id, the endpoint NAME (never a URL), the model name the service
  reported, a local model's digest, the pricing basis and the sampling sent. Everything said above
  about apps "by hand" holds for apps: no software contacts a chat APP. Hand-entered and asked runs
  may share a database; scripted demo runs share one with neither.
* QUESTION LISTS, since 23 November 2026: a question may carry `"list": "trending"` (a newer question picked from
  what people are searching for, added along the way); without the field it belongs to the main list. The list
  is part of a question's recorded identity (its hash and the `list` column), so a recorded question never
  moves; a main question hashes exactly as it did before lists. `cli archive-chatbot-ask --list trending` (or
  `--questions C011,C012`) asks only some questions: the run then records exactly the ids it asked, and a
  question a run did not ask has no entry for that date anywhere (never "no answer", never a change).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from . import archive, config as settings, controls, guards, providers
from . import core  # attributes are used at call time only

CONFIG_FILE = Path(__file__).resolve().parent / "archive_chatbots.json"
CONFIG_FORMAT = "live-genesis-chatbots/3"   # /3: optional answerer groups (`groups`, and `group` on a chat app)
FILE_FORMAT = "live-genesis-chatbot-collection/2"
CHAT_QUESTION_ID = re.compile(r"C[0-9]{3}")
MAIN_LIST, TRENDING_LIST = "main", "trending"
# The lists a question can name with `list`. A question without the field is on the main list, and "main" is never
# written anywhere: not in the configuration, the database ('' there), the hash or the payload.
LISTS = (TRENDING_LIST,)
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
MAX_CHATBOTS, MAX_CHATBOT_QUESTIONS = 24, 60
# Answerer GROUPS: which kind of place a chat app belongs to ("AI chat apps" today; a later group is a configuration
# addition). A group id shares the card namespace with the app labels (card/<ID>-<group id>.html, -<group id>-2.html).
MAX_GROUPS = 6
DEFAULT_GROUP = {"id": "ai-apps", "name": "AI chat apps"}   # a configuration without `groups`, and every row stored before groups
_PAGED = re.compile(r"(.+)-[0-9]+")
NAME_CHARS = 40
ATTRIBUTION_CHARS = 60    # an optional line a maker's brand guidance asks for, shown as text with every reply of that app
RESERVED_LABELS = ("chatbots", HUMAN_LABEL)   # card/<ID>-chatbots.html is the card of all; card/<ID>-<label>.html is one app's
REPLY_CHARS = 2000        # chat apps write longer replies than the API runs (archive.ANSWER_CHARS is 1000)
SHOWN_MODEL_CHARS, NOTE_CHARS, SKIP_CHARS = 60, 200, 120
MAX_FILE_BYTES, MAX_REPLY_FILE_BYTES, MAX_SCREENSHOT_BYTES = 2_000_000, 64_000, 25_000_000
MAX_AGE_DAYS = 14
MANUAL, DEMO = "manual", "demo"
ASKED = "asked"           # a reply a MODEL gave when this software asked it (cli archive-chatbot-ask): never an app's reply
SOURCES = (MANUAL, DEMO, ASKED)
MODEL = "model"           # `kind` of a model answerer (configuration, conditions row, public app entry); an app has none
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
GUIDE = "docs/MONTHLY-ROUND.md"    # the owner's step-by-step guide; named in the sheet and in the messages
HOW_TO = (
    "This is your sheet for this month. It is about half an hour of copy and paste, and you cannot break anything: "
    "nothing in this file is sent anywhere, and nothing is stored until you say so. The whole month, step by step, is in "
    + GUIDE + ".",
    "Do it by hand. Open each chat app yourself. No program may open, read or copy from these apps: their terms forbid "
    "automated access, and this software never contacts them.",
    "Before you start, read each app's current terms. They change, and they decide whether you may publish what the app wrote.",
    "Use the same setup every month: a logged-in FREE account and the app's temporary or private chat where it has one "
    "(ChatGPT \"Temporary Chat\", Gemini \"Temporary chat\", Grok \"Private Chat\"), with memory off.",
    "Fill in each app's `conditions` before you ask it anything. account: logged_out, free_account or paid_account. "
    "fresh_chat: true (always: a new chat for every question). temporary_chat: true or false. memory_off: true, false or "
    "\"not_applicable\". web_search: on, off or unknown (if the reply shows a \"Sources\" or citations marker, search was "
    "on). country: two capital letters, like US. app: web, mobile or desktop. shown_model: the model name the app shows, or "
    "\"not shown\". note: optional and short.",
    "For every question: start a NEW chat, paste the `prompt` exactly as it is, wait for the reply, then copy the WHOLE "
    "reply into `answer`.",
    "Paste the whole reply even if it is long; never trim or reword it. The page shortens the display, not the text.",
    "A reply with line breaks or quotation marks will not sit nicely inside this file. Save it as a plain text file next to "
    "this one and put that file's name in \"answer_file\" (leave `answer` empty).",
    "If the app refused or dodged the question, that is fine and it happens: add \"refused\": true. You may paste what it "
    "said into `answer`; it stays in your own database and is never published. If you could not ask at all, leave `answer` "
    "empty and add \"skipped\": \"a short reason\". The site shows only real answers.",
    "Optional, per answer: \"screenshot\": \"the file name of a screenshot you kept\". Only its sha256 fingerprint is stored, "
    "never the picture and never the file name.",
    "Did not ask one of the apps this time? Remove its whole block. Please never remove single questions and never change "
    "a prompt.",
    "The `human` block is optional: remove it if you have no answers from people this time. It is for answers PEOPLE wrote, "
    "which you gathered yourself by hand (no program contacts Reddit or any other site). The working rule: post the question "
    "yourself, say in the post that replies may be quoted or summed up on the site, and read that community's rules first. "
    "`conditions`: source = where, in a few plain words (like \"Reddit thread\"; no link, no username); collected_on = "
    "YYYY-MM-DD; count = how many replies you read (a number, or null). Per question: one short `answer` and `kind` = "
    "\"summarised\" (your own short summary of several replies; prefer this) or \"quoted\" (one person's short, harmless "
    "line, copied exactly), or \"skipped\": \"reason\". No usernames, no @handles, no links: such a text is refused.",
    "When you are done, check the file. This stores nothing: python -m live_genesis.cli archive-chatbot-import THIS_FILE --dry-run",
)

TABLES = """
-- Chat-app track: replies the owner collected BY HAND and imported from a file (kind manual; one row per import),
-- scripted samples (demo), or replies of models this software asked (asked; one row per cli archive-chatbot-ask).
CREATE TABLE IF NOT EXISTS archive_chatbot_runs (
    id INTEGER PRIMARY KEY, number INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('manual','demo','asked')),
    -- The day the owner asked (their own calendar date) and the moment the file was imported. Both are kept.
    collected_on TEXT NOT NULL CHECK(collected_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    imported_at TEXT NOT NULL,
    -- The question ids of archive_chatbots.json at this import (a JSON list) and the hash of that configuration.
    question_ids TEXT NOT NULL, config_sha256 TEXT NOT NULL,
    -- sha256 of the imported file's bytes ('' for a scripted run). Never the file's name or path.
    file_sha256 TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, number)
);
-- One answerer in one run. kind 'app': what the owner recorded about how one chat app was asked (the conditions).
-- kind 'model': a model this software asked, with what its first recorded call of the run said about itself.
-- The two kinds never share a column set: the CHECKs at the end keep an app row free of model facts and the reverse.
CREATE TABLE IF NOT EXISTS archive_chatbot_conditions (
    run_id INTEGER NOT NULL REFERENCES archive_chatbot_runs(id), chatbot TEXT NOT NULL,
    display_name TEXT NOT NULL, maker TEXT NOT NULL,
    -- A line the maker's brand guidance asks for ("Written with ..."), from archive_chatbots.json; '' when none.
    attribution TEXT NOT NULL DEFAULT '',
    account TEXT CHECK(account IN ('logged_out','free_account','paid_account')),
    fresh_chat INTEGER CHECK(fresh_chat=1),
    temporary_chat INTEGER CHECK(temporary_chat IN (0,1)),
    memory_off TEXT CHECK(memory_off IN ('true','false','not_applicable')),
    web_search TEXT CHECK(web_search IN ('on','off','unknown')),
    country TEXT CHECK(country GLOB '[A-Z][A-Z]'),
    app TEXT CHECK(app IN ('web','mobile','desktop')),
    shown_model TEXT, note TEXT NOT NULL DEFAULT '',
    -- The answerer group this app belonged to at this run (archive_chatbots.json). '' in a row stored before groups
    -- existed: read as the default group.
    group_id TEXT NOT NULL DEFAULT '', group_name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'app' CHECK(kind IN ('app','model')),
    -- A model answerer only: the model id requested, the provider, the endpoint NAME (never its URL), the model name
    -- the service reported (NULL: it did not say), a local model's sha256 digest (NULL: not known), the pricing basis,
    -- the sampling sent ('none': not pinned) and whether it ran on the operator's machine.
    model TEXT, provider TEXT, endpoint TEXT, reported_model TEXT, model_digest TEXT,
    pricing_basis TEXT CHECK(pricing_basis IN ('rates','local','declared-free')),
    sampling TEXT, local INTEGER CHECK(local IN (0,1)),
    PRIMARY KEY (run_id, chatbot),
    CHECK ((kind='app') = (account IS NOT NULL AND fresh_chat IS NOT NULL AND temporary_chat IS NOT NULL AND memory_off IS NOT NULL
                           AND web_search IS NOT NULL AND country IS NOT NULL AND app IS NOT NULL AND shown_model IS NOT NULL)),
    CHECK (kind='model' OR (model IS NULL AND provider IS NULL AND endpoint IS NULL AND reported_model IS NULL
                            AND model_digest IS NULL AND pricing_basis IS NULL AND sampling IS NULL AND local IS NULL)),
    CHECK (kind='app' OR (account IS NULL AND fresh_chat IS NULL AND temporary_chat IS NULL AND memory_off IS NULL
                          AND web_search IS NULL AND country IS NULL AND app IS NULL AND shown_model IS NULL
                          AND attribution='' AND note=''
                          AND model IS NOT NULL AND provider IS NOT NULL AND endpoint IS NOT NULL AND pricing_basis IS NOT NULL
                          AND local IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS archive_chatbot_answers (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
    question_set TEXT NOT NULL, question_id TEXT NOT NULL, chatbot TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('manual','demo','asked')),
    state TEXT NOT NULL CHECK(state IN ('answered','no_answer','withheld')),
    reason TEXT NOT NULL DEFAULT '',
    answer_text TEXT, normalised TEXT, answer_sha256 TEXT,
    changed INTEGER CHECK(changed IN (0,1)),
    screenshot_sha256 TEXT, created_at TEXT NOT NULL,
    -- 1: the owner marked this reply as a refusal or a dodge. Stored, never shown, never part of `changed`.
    refused INTEGER NOT NULL DEFAULT 0 CHECK(refused IN (0,1)),
    -- An asked reply only: the budget reservation its call settled (budget_reservations.id). NULL on every other row.
    reservation_id INTEGER,
    UNIQUE (run_id, question_id, chatbot),
    CHECK ((state='answered') = (answer_text IS NOT NULL)),
    -- Only an asked reply has a reservation; it has no screenshot and is never an owner-marked refusal.
    CHECK ((source='asked') = (reservation_id IS NOT NULL)),
    CHECK (source!='asked' OR (screenshot_sha256 IS NULL AND refused=0)),
    -- An answer belongs to a chat app whose conditions were recorded in the same run.
    FOREIGN KEY (run_id, chatbot) REFERENCES archive_chatbot_conditions(run_id, chatbot)
);
-- The track's own question list as recorded at import: a recorded question never changes or disappears.
CREATE TABLE IF NOT EXISTS archive_chatbot_questions (
    id TEXT PRIMARY KEY CHECK(id GLOB 'C[0-9][0-9][0-9]'), position INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('manual','demo','asked')),
    text TEXT NOT NULL, prompt TEXT NOT NULL, hook TEXT NOT NULL DEFAULT '', added TEXT NOT NULL,
    sha256 TEXT NOT NULL, first_run INTEGER NOT NULL REFERENCES archive_chatbot_runs(id),
    -- The list the question belongs to: '' the main list, 'trending' a question added along the way from what people
    -- are searching for. Part of the recorded hash when it is not ''. Added in place (the last column).
    list TEXT NOT NULL DEFAULT '' CHECK(list IN ('','trending'))
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
RULES_INDEX = "\nCREATE INDEX IF NOT EXISTS archive_chatbot_answers_series ON archive_chatbot_answers(chatbot, question_id, run_id);"
RULES = "".join(
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}\n"
    f"    BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;\n"
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}\n"
    f"    BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;\n" for table in APPEND_ONLY) + RULES_INDEX + """
-- Scripted runs never share a database with recorded ones (hand-entered or asked, which may sit together), and
-- neither sits next to the other kind of model answer.
CREATE TRIGGER IF NOT EXISTS archive_chatbot_runs_kinds_apart BEFORE INSERT ON archive_chatbot_runs
    WHEN EXISTS (SELECT 1 FROM archive_chatbot_runs WHERE (kind='demo')!=(NEW.kind='demo'))
      OR (NEW.kind!='demo' AND EXISTS (SELECT 1 FROM archive_runs WHERE kind='demo'))
      OR (NEW.kind='demo' AND EXISTS (SELECT 1 FROM archive_runs WHERE kind='real'))
    BEGIN SELECT RAISE(ABORT, 'hand-entered and scripted chat-app runs are never mixed with each other or with the other kind of answers'); END;
-- A model answerer belongs to an asked run and to no other; a chat app (and the Human) never to an asked run.
CREATE TRIGGER IF NOT EXISTS archive_chatbot_conditions_kind BEFORE INSERT ON archive_chatbot_conditions
    WHEN (NEW.kind='model') != ((SELECT kind FROM archive_chatbot_runs WHERE id=NEW.run_id) IS 'asked')
    BEGIN SELECT RAISE(ABORT, 'a model answerer belongs to an asked run, a chat app to a hand-entered or scripted one'); END;
CREATE TRIGGER IF NOT EXISTS archive_human_collections_not_asked BEFORE INSERT ON archive_human_collections
    WHEN (SELECT kind FROM archive_chatbot_runs WHERE id=NEW.run_id) IS 'asked'
    BEGIN SELECT RAISE(ABORT, 'an asked run holds no human collection'); END;
-- One label is one kind of answerer for good: an app's series is never continued by a model, or the reverse.
CREATE TRIGGER IF NOT EXISTS archive_chatbot_conditions_one_kind BEFORE INSERT ON archive_chatbot_conditions
    WHEN EXISTS (SELECT 1 FROM archive_chatbot_conditions WHERE chatbot=NEW.chatbot AND kind!=NEW.kind)
    BEGIN SELECT RAISE(ABORT, 'this label already belongs to the other kind of answerer'); END;
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
    for "an answer needs a recorded chat-app question". One from before answerer groups
    (`tests/fixtures/archive_schema_v6.sql`) gains `group_id` and `group_name` on the conditions, empty
    in every stored row. One from before model answerers (`tests/fixtures/archive_schema_v7.sql`) has four
    tables rebuilt once (`_rebuild`): same rows, same ids, same values. One from before question lists
    (`tests/fixtures/archive_schema_v8.sql`) gains the `list` column on the recorded questions, '' (the main
    list) in every stored row. Nothing recorded is touched."""
    cx.executescript(TABLES)
    if "refused" not in {row[1] for row in cx.execute("PRAGMA table_info(archive_chatbot_answers)")}:
        cx.execute("ALTER TABLE archive_chatbot_answers ADD COLUMN refused INTEGER NOT NULL DEFAULT 0 CHECK(refused IN (0,1))")
    columns = {row[1] for row in cx.execute("PRAGMA table_info(archive_chatbot_conditions)")}
    for column in ("group_id", "group_name"):      # answerer groups: added in place; every stored row reads as the default group
        if column not in columns:
            cx.execute(f"ALTER TABLE archive_chatbot_conditions ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    if _stale_tables(cx):
        _rebuild(cx)
    if "list" not in {row[1] for row in cx.execute("PRAGMA table_info(archive_chatbot_questions)")}:
        # Question lists: added in place; every recorded question reads as the main list, and its hash is what it was.
        cx.execute("ALTER TABLE archive_chatbot_questions ADD COLUMN list TEXT NOT NULL DEFAULT '' CHECK(list IN ('','trending'))")
    cx.executescript("DROP TRIGGER IF EXISTS archive_chatbot_answers_locked_question;" + RULES)


REBUILT = ("archive_chatbot_runs", "archive_chatbot_conditions", "archive_chatbot_answers", "archive_chatbot_questions")


def _stale_tables(cx) -> list[str]:
    """The tables of REBUILT that still have their shape from before model answerers: a CHECK without 'asked'
    (`tests/fixtures/archive_schema_v7.sql`)."""
    return [name for name, sql in cx.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")
            if name in REBUILT and "'asked'" not in (sql or "") and "kind IN ('app','model')" not in (sql or "")]


def _rebuild(cx) -> None:
    """Give the four tables whose CHECK constraints name the run kinds and sources their shape with `asked` (and
    the conditions their `kind` and model columns, the answers `reservation_id`). SQLite cannot alter a CHECK,
    so each table is rebuilt once, as `archive._rebuild_answers` does: a new table, every row copied with its
    id and every value as it was (the new columns take their defaults: kind 'app', the rest NULL), the old table
    dropped, the new one renamed. ONE transaction for all four; the row count and the highest rowid of every
    table are compared before the old one goes, and `foreign_key_check` must find nothing afterwards, or nothing
    is changed. Foreign keys are switched off for the swap only (a parent table is dropped while its children
    exist) and the tables' triggers and index, which go with the old tables, are recreated by RULES."""
    cx.commit()
    cx.execute("PRAGMA foreign_keys=OFF")     # a no-op inside a transaction, hence the commit above
    try:
        cx.execute("BEGIN IMMEDIATE")
        stale = _stale_tables(cx)             # read again under the lock: another process may have done it meanwhile
        # Every trigger of the track goes first (RULES brings each back): a trigger on a table that stays may name
        # one that is being swapped, and SQLite refuses a rename while a trigger names a table that is missing.
        for (name,) in cx.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN (%s)"
                                  % ",".join("?" * len(TABLE_NAMES)), TABLE_NAMES).fetchall():
            cx.execute(f"DROP TRIGGER {name}")
        for table in stale:
            marker = f"CREATE TABLE IF NOT EXISTS {table} ("
            start = TABLES.index(marker)
            create = TABLES[start:TABLES.index("\n);", start) + 3].replace(marker, f"CREATE TABLE {table}_rebuild (", 1)
            cx.execute(create)
            new = {row[1] for row in cx.execute(f"PRAGMA table_info({table}_rebuild)")}
            names = ",".join(row[1] for row in cx.execute(f"PRAGMA table_info({table})").fetchall() if row[1] in new)
            cx.execute(f"INSERT INTO {table}_rebuild({names}) SELECT {names} FROM {table} ORDER BY rowid")
            before, after = (tuple(cx.execute(f"SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM {name}").fetchone())
                             for name in (table, f"{table}_rebuild"))
            if before != after:
                raise RuntimeError(f"{table} could not be upgraded: the copy does not hold every row. Nothing was changed.")
            cx.execute(f"DROP TABLE {table}")
            cx.execute(f"ALTER TABLE {table}_rebuild RENAME TO {table}")
        for table in TABLE_NAMES:
            if cx.execute(f"PRAGMA foreign_key_check({table})").fetchone():
                raise RuntimeError(f"{table} could not be upgraded: a row would refer to a missing one. Nothing was changed.")
        cx.execute(RULES_INDEX)               # inside the transaction: the index went with the old answers table
        cx.commit()
    except BaseException:
        cx.rollback()
        raise
    finally:
        cx.execute("PRAGMA foreign_keys=ON")


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


def list_of(question: dict) -> str:
    """The list a question belongs to: "trending", or "main" for a question that names none."""
    return question.get("list") or MAIN_LIST


def question_hash(question: dict) -> str:
    """The hash recorded for one question at its first import: id, text, prompt, hook and added date, and its
    list when it names one. A question of the main list hashes exactly as it did before lists existed."""
    body = {key: question[key] for key in QUESTION_KEYS}
    if question.get("list"):
        body["list"] = question["list"]
    return hashlib.sha256(_canonical(body)).hexdigest()


def config_hash(chatbots: list[dict], questions: list, human: dict | None = None, groups: list[dict] | None = None,
                models: list[dict] | None = None) -> str:
    """Covers the chat apps (with their group), the groups' names, the human column's wording, the questions and,
    when there are any, the model answerers (a configuration without one hashes as it always did)."""
    return hashlib.sha256(_canonical({"chatbots": chatbots, "questions": questions, "human": human, "groups": groups or [],
                                      **({"models": models} if models else {})})).hexdigest()


def groups_problem(groups: list[dict], apps: list[dict], allow_empty: bool = False) -> str | None:
    """Why a list of groups ({id, name}) and of answerers ({label, name, group, ...}; a model answerer also carries
    `kind`) do not fit together, or None.
    Shared by the configuration and the public payload. A group id is shaped like a label and shares the
    card names with the labels, so no label may equal a group id or look like a later page of its card.
    A group holds chat apps or model answerers, never both. `allow_empty` (the configuration only): a group
    may be declared before its first answerer; it is then recorded and shown nowhere."""
    ids = [group["id"] for group in groups]
    if not 1 <= len(groups) <= MAX_GROUPS:
        return f"there must be 1 to {MAX_GROUPS} groups"
    if any(not CHATBOT_LABEL.fullmatch(i) or i in RESERVED_LABELS or _PAGED.fullmatch(i) for i in ids):
        return ('a group id must be 2-24 characters of a-z 0-9 -, starting with a letter, not "chatbots" or "human", '
                "and not ending in a number after a hyphen")
    if len(set(ids)) != len(ids) or len({group["name"].casefold() for group in groups}) != len(groups):
        return "group ids and group names must differ"
    for app in apps:
        if app["group"] not in ids:
            return f"{app['label']}: its group is not one of the groups"
        paged = _PAGED.fullmatch(app["label"])
        if app["label"] in ids or (paged and paged.group(1) in ids):
            return f"{app['label']}: a label may not be a group id, or a group id followed by a number"
    unused = [i for i in ids if not any(app["group"] == i for app in apps)]
    if unused and not allow_empty:
        return f"group {unused[0]} has no chat app"
    mixed = [i for i in ids if len({app.get("kind", "app") for app in apps if app["group"] == i}) > 1]
    if mixed:
        return f"group {mixed[0]} holds both chat apps and model answerers; a group holds one kind"
    return None


def prompt_of(text: str) -> str:
    return f"{PROMPT_PREFIX}\n\n{text}"


def load_config(path: str | Path | None = None) -> dict:
    """Read and validate `archive_chatbots.json`. Returns {status, groups, chatbots, models, human, aside, questions,
    items, prompts, sha256}: `groups` is a list of {id, name} (the optional `groups` of the file, or the one default
    group); `chatbots` is a list of {label, name, maker, attribution, group} (`group` is a group id; an app
    without one belongs to the first group): the chat APPS only; `models` is the list of model answerers, the
    entries of the file's `chatbots` list with `"kind": "model"`: {label, name, maker ('' when none), attribution
    (always ''), group, kind, answerer}, where `answerer` is a label of LIVE_ARCHIVE_MODELS (read only when a model
    is asked, never here) and the name may not hold a chat app's name; `human` is {name, meta}
    (the wording of the Human column); `aside` one short line of the owner's own (shown once on the
    chat-app page, never on a card; '' when none); `questions` the ids in the file's order; `items` the
    questions ({id, text, prompt, hook, added}, and `list` "trending" on a question of that list: a main
    question has no such key); `prompts` maps each id to its question.
    Strict shape; ids C001... in order without gaps; every prompt is PROMPT_PREFIX, a blank line, the
    question; no obviously political word in a question or a hook. Raises ArchiveRefused."""
    source = Path(path) if path is not None else CONFIG_FILE

    def bad(why: str) -> archive.ArchiveRefused:
        return _refused(f"Chat-app configuration {source.name}: {why}")
    try:
        raw = _load_json(source.read_bytes())
    except (OSError, ValueError) as err:
        raise _refused(f"Chat-app configuration {source.name} is missing or not valid JSON ({type(err).__name__})") from None
    if not isinstance(raw, dict) or set(raw) - {"format", "status", "note", "prompt_prefix", "groups", "chatbots", "human", "aside", "questions"}:
        raise bad("top level must be an object with format, status, note, prompt_prefix, groups, chatbots, human, aside and "
                  "questions only")
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
    declared = raw.get("groups", [dict(DEFAULT_GROUP)])
    if not isinstance(declared, list) or not declared:
        raise bad(f"groups must list 1 to {MAX_GROUPS} groups, or be left out")
    groups: list[dict] = []
    for group in declared:
        if not isinstance(group, dict) or set(group) != {"id", "name"} or not isinstance(group["id"], str) \
                or not _line_ok(group["name"], NAME_CHARS):
            raise bad(f"each group needs exactly id and name (name 1-{NAME_CHARS} plain characters)")
        groups.append({"id": group["id"], "name": group["name"].strip()})
    chatbots: list[dict] = []
    models: list[dict] = []
    for item in listed:
        if isinstance(item, dict) and item.get("kind") == MODEL:
            # A model asked by software: its own shape. No attribution; its group is named, never assumed.
            if not {"label", "kind", "name", "answerer", "group"} <= set(item) <= {"label", "kind", "name", "answerer", "group", "maker"}:
                raise bad("a model answerer needs label, kind, name, answerer and group, and may have maker (never attribution)")
            if not isinstance(item["label"], str) or not CHATBOT_LABEL.fullmatch(item["label"]) or item["label"] in RESERVED_LABELS:
                raise bad('a label must be 2-24 characters of a-z 0-9 -, starting with a letter, and not "chatbots" or "human"')
            if not isinstance(item["answerer"], str) or not archive.LABEL.fullmatch(item["answerer"]):
                raise bad(f"{item['label']}: answerer must be a label of LIVE_ARCHIVE_MODELS (1-24 characters of a-z 0-9 . _ -)")
            if not _line_ok(item["name"], NAME_CHARS) or ("maker" in item and not _line_ok(item["maker"], NAME_CHARS)) \
                    or not isinstance(item["group"], str):
                raise bad(f"{item['label']}: name (and maker, when given) must be 1-{NAME_CHARS} plain characters, and group a group id")
            models.append({"label": item["label"], "name": item["name"].strip(), "maker": item.get("maker", "").strip(),
                           "attribution": "", "group": item["group"], "kind": MODEL, "answerer": item["answerer"]})
            continue
        if not isinstance(item, dict) or not {"label", "name", "maker"} <= set(item) <= {"label", "name", "maker", "attribution", "group"}:
            raise bad("each chat app needs label, name and maker, and may have attribution and group")
        if not isinstance(item.get("group", ""), str):
            raise bad("a chat app's group must be a group id")
        if not isinstance(item["label"], str) or not CHATBOT_LABEL.fullmatch(item["label"]) or item["label"] in RESERVED_LABELS:
            raise bad('a label must be 2-24 characters of a-z 0-9 -, starting with a letter, and not "chatbots" or "human"')
        if "attribution" in item and not _line_ok(item["attribution"], ATTRIBUTION_CHARS):
            raise bad(f"{item['label']}: attribution must be 1-{ATTRIBUTION_CHARS} plain characters on one line")
        if not _line_ok(item["name"], NAME_CHARS) or not _line_ok(item["maker"], NAME_CHARS):
            raise bad(f"{item['label']}: name and maker must be 1-{NAME_CHARS} plain characters")
        chatbots.append({"label": item["label"], "name": item["name"].strip(), "maker": item["maker"].strip(),
                         "attribution": item.get("attribution", "").strip(), "group": item.get("group", groups[0]["id"])})
    for model in models:
        # The name says what was asked: an answer a model gave over an API never sits under a chat app's name.
        taken = next((c["name"] for c in chatbots if c["name"].casefold() in model["name"].casefold()), None)
        if taken:
            raise bad(f'{model["label"]}: a model answerer may not carry the name of a chat app ("{taken}"). Name the model '
                      "that is asked; the app's column is for replies from the app itself")
    if len({model["answerer"] for model in models}) != len(models):
        raise bad("two model answerers name the same answerer of LIVE_ARCHIVE_MODELS")
    problem = groups_problem(groups, chatbots + models, allow_empty=True)
    if problem:
        raise bad(problem)
    if not isinstance(person, dict) or set(person) != {"name", "meta"} or not _line_ok(person["name"], NAME_CHARS) \
            or not _line_ok(person["meta"], META_CHARS):
        raise bad(f"human must hold name (1-{NAME_CHARS} characters) and meta (1-{META_CHARS} characters): the wording of the "
                  "column of answers written by people")
    human = {"name": person["name"].strip(), "meta": person["meta"].strip()}
    names = [c["name"].casefold() for c in chatbots + models] + [human["name"].casefold()]
    if len({c["label"] for c in chatbots + models}) != len(chatbots + models) or len(set(names)) != len(names):
        raise bad("labels and names must differ")
    if aside_text != "" and not _line_ok(aside_text, ASIDE_CHARS):
        raise bad(f"aside must be one plain line of at most {ASIDE_CHARS} characters, or empty")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CHATBOT_QUESTIONS:
        raise bad(f"questions must list 1 to {MAX_CHATBOT_QUESTIONS} questions")
    items: list[dict] = []
    for position, entry in enumerate(entries, 1):
        wanted = f"C{position:03d}"
        if not isinstance(entry, dict) or not {"id", "text", "prompt", "added"} <= set(entry) <= set(QUESTION_KEYS) | {"list"}:
            raise bad(f"question {position} must hold id, text, prompt and added, and may have hook and list")
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
        if "list" in entry and entry["list"] not in LISTS:
            raise bad(f'{wanted}: list must be "{TRENDING_LIST}", or left out for the main list')
        # A main question is the dict it always was: no `list` key, so the configuration hash and its own hash are unchanged.
        items.append({"id": wanted, "text": text, "prompt": entry["prompt"], "hook": hook, "added": entry["added"],
                      **({"list": entry["list"]} if "list" in entry else {})})
    if len({q["text"].casefold() for q in items}) != len(items):
        raise bad("two questions have the same text")
    return {"status": raw["status"], "groups": groups, "chatbots": chatbots, "models": models, "human": human,
            "aside": aside_text.strip(),
            "questions": [q["id"] for q in items], "items": items, "prompts": {q["id"]: q for q in items},
            # A group without an answerer is recorded nowhere, the hash included: declaring one changes nothing.
            "sha256": config_hash(chatbots, items, human,
                                  [g for g in groups if any(a["group"] == g["id"] for a in chatbots + models)], models)}


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
        raise _refused(f"{what} must be a date written YYYY-MM-DD, for example 2026-12-01") from None


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
        raise _refused(f"{target} already exists, so it was left alone (it may hold replies you already pasted). "
                       "Choose another name with --out, for example --out chatbots-2.json.")
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


def _blank(value: Any) -> str:
    """" (it is still empty)" for a value the owner has not filled in yet; never echoes what was written."""
    return " (it is still empty)" if value in (None, "") else ""


def _conditions(raw: Any, where: str, problems: list[str]) -> dict | None:
    if not isinstance(raw, dict) or set(raw) - set(CONDITION_KEYS) or any(key not in raw for key in CONDITION_KEYS[:-1]):
        problems.append(f"{where}: conditions must hold exactly " + ", ".join(CONDITION_KEYS[:-1]) + " and optionally note. "
                        "A line was added or removed there: copy that block from a new sheet and fill it in again")
        return None
    before = len(problems)
    if raw["account"] not in ACCOUNTS:
        problems.append(f"{where}: account must be one of " + " | ".join(ACCOUNTS) + _blank(raw["account"])
                        + ". Write the one you used, in quotes")
    if raw["fresh_chat"] is not True:
        problems.append(f"{where}: fresh_chat must be true{_blank(raw['fresh_chat'])}. Every question needs a new chat of its "
                        "own. If you asked them all in one long chat, please ask again, one new chat per question")
    if type(raw["temporary_chat"]) is not bool:
        problems.append(f"{where}: temporary_chat must be true or false{_blank(raw['temporary_chat'])}, without quotes: true if "
                        "you used the app's temporary or private chat")
    if type(raw["memory_off"]) is not bool and raw["memory_off"] != NOT_APPLICABLE:
        problems.append(f'{where}: memory_off must be true, false or "{NOT_APPLICABLE}"{_blank(raw["memory_off"])}: true if the '
                        "app's memory was switched off")
    if raw["web_search"] not in SEARCH:
        problems.append(f"{where}: web_search must be one of " + " | ".join(SEARCH) + _blank(raw["web_search"])
                        + ". If the reply showed sources write on; if you cannot tell, unknown")
    if not isinstance(raw["country"], str) or not COUNTRY.fullmatch(raw["country"]):
        problems.append(f"{where}: country must be two capital letters, e.g. US{_blank(raw['country'])}: the country you asked from")
    if raw["app"] not in APPS:
        problems.append(f"{where}: app must be one of " + " | ".join(APPS) + _blank(raw["app"]) + ": where you used it")
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
                        + ", ".join(sorted(keys - {"question", "prompt", "answer"}))
                        + ". Something else is written there: take it out")
        return None
    question_id = raw["question"]
    where = f"{where}, {question_id if CHAT_QUESTION_ID.fullmatch(question_id) else 'an unknown question'}"
    if question_id not in prompts:
        problems.append(f"{where}: not one of the questions in {CONFIG_FILE.name}. Question ids must stay as they are: "
                        "make a new sheet and paste your replies into it")
        return None
    if raw.get("prompt") != prompts[question_id]["prompt"]:
        problems.append(f"{where}: the prompt in the file is not the prompt of {CONFIG_FILE.name}. A prompt must stay exactly "
                        "as written: make a new sheet and paste your replies into it")
        return None
    return question_id, where


def _answer(raw: Any, where: str, folder: Path, prompts: dict[str, dict], problems: list[str]) -> dict | None:
    found = _question_of(raw, where, ANSWER_KEYS, prompts, problems)
    if found is None:
        return None
    question_id, where = found
    text, named, skipped, refused = raw.get("answer", ""), raw.get("answer_file", ""), raw.get("skipped", ""), raw.get("refused", False)
    if not isinstance(text, str) or not isinstance(named, str) or not isinstance(skipped, str):
        problems.append(f"{where}: answer, answer_file and skipped must be text, in quotes")
        return None
    if type(refused) is not bool:
        problems.append(f"{where}: refused must be true or false, without quotes")
        return None
    given = [bool(text.strip()), bool(named.strip()), bool(skipped.strip())]
    if sum(given) > 1 or (not any(given) and not refused) or (refused and given[2]):
        problems.append(f"{where}: give exactly one of answer, answer_file or skipped"
                        + (" (all are empty: paste the reply, or add \"refused\": true, or say in skipped why you could not ask)"
                           if not any(given) else " (more than one is filled in: keep one)")
                        + '. "refused": true may stand alone or with the reply')
        return None
    screenshot_hash = None
    if raw.get("screenshot") not in ("", None):
        picture = _beside(folder, raw["screenshot"], MAX_SCREENSHOT_BYTES)
        if picture is None:
            problems.append(f"{where}: the screenshot file was not found, is not a plain file or is larger than "
                            f"{MAX_SCREENSHOT_BYTES // 1_000_000} MB. Check its name, or take the screenshot line out")
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
                            f"{MAX_REPLY_FILE_BYTES // 1000} kB. Check its name; it should sit in the same folder as this sheet")
            return None
        if not text.strip():
            problems.append(f"{where}: the answer_file is empty. Paste the whole reply into it and save")
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
        problems.append("human: count must be a whole number of replies read, or null if you did not count")
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
        problems.append(f"{where}: give exactly one of answer or skipped: write one short answer, or say in skipped why "
                        "there is none")
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
        problems.append(f"{where}: the answer holds a {looks}. No usernames, handles or links in an answer written by people: "
                        "take it out, or sum the replies up in your own words")
        return None
    state, reason, kept = classify_reply(text, HUMAN_CHARS)
    return {"question": question_id, "state": state, "reason": reason, "text": kept, "chars": len(text.strip()),
            "manner": manner if state == "answered" else None}


def _untouched(conditions: Any, entries: Any) -> bool:
    """Whether a chat-app block is exactly as the template wrote it: no condition filled in and no reply given."""
    if not isinstance(conditions, dict) or not isinstance(entries, list) or not entries:
        return False
    return all(value in (None, "") for value in conditions.values()) and all(
        isinstance(entry, dict) and not any(entry.get(key) for key in ("answer", "answer_file", "skipped", "refused", "screenshot"))
        for entry in entries)


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
        raise _refused(f"{source.name} could not be read: it is missing, larger than {MAX_FILE_BYTES // 1_000_000} MB or not "
                       f"valid JSON ({type(err).__name__}). If the file is there, the usual cause is a reply with line breaks or "
                       "quotation marks pasted straight in: save that reply as a text file next to the sheet and name it in "
                       "\"answer_file\". A missing comma or quote does it too. Nothing was stored.") from None
    if not isinstance(raw, dict) or raw.get("format") != FILE_FORMAT:
        raise _refused(f'{source.name} is not a chat-app collection file (format must be "{FILE_FORMAT}"). If it came from '
                       "an older version, make a new sheet with archive-chatbot-template and paste your replies into it. "
                       "Nothing was stored.")
    if set(raw) - {"format", "how_to", "collected_on", "chatbots", "human"}:
        raise _refused(f"{source.name}: top level must hold format, how_to, collected_on, chatbots and human only. Something "
                       "else was added at the top of the file: take it out. Nothing was stored.")
    day = _date(raw.get("collected_on"), f"{source.name}: collected_on")
    now = today()
    if day > now:
        raise _refused(f"{source.name}: collected_on {day} is in the future. Write the day you actually asked. "
                       "Nothing was stored.")
    if (now - day).days > MAX_AGE_DAYS and not allow_old:
        raise _refused(f"{source.name}: collected_on {day} is more than {MAX_AGE_DAYS} days ago. If that really is the day you "
                       "asked, run the same command again with --allow-old at the end. Nothing was stored.")
    listed, person = raw.get("chatbots", []), raw.get("human")
    if not isinstance(listed, list) or (not listed and person is None):
        raise _refused(f"{source.name}: chatbots must list at least one chat app, or the file must hold the human block. "
                       "This file has neither: make a new sheet. Nothing was stored.")
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
            problems.append("a chat-app block must hold chatbot, name, maker, conditions and answers only. Something else is "
                            "written in one of them: take it out")
            continue
        label = item["chatbot"]
        if any(model["label"] == label for model in config.get("models", ())):
            problems.append(f"{label}: is a model asked by software (cli archive-chatbot-ask); a file cannot carry answers for it")
            continue
        if label not in known:
            problems.append("a chat-app block names an app that is not in " + CONFIG_FILE.name + ". The \"chatbot\" lines "
                            "must stay as the sheet wrote them")
            continue
        if any(c["label"] == label for c in chatbots):
            problems.append(f"{label}: listed twice. Keep one block for each app")
            continue
        entries = item.get("answers")
        if _untouched(item.get("conditions"), entries):
            # One kind line for a block nobody has started, instead of one line per empty field.
            problems.append(f"{label}: this block is still blank. Fill in its conditions and paste the replies, or remove the "
                            "whole block if you did not ask this app")
            chatbots.append({**known[label], "conditions": None, "answers": []})
            continue
        conditions = _conditions(item.get("conditions"), label, problems)
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
            problems.append("the human block must hold name, conditions and answers only. Something else is written there: "
                            "take it out")
        elif isinstance(person.get("conditions"), dict) and person["conditions"].get("source") in (None, "") and person["answers"] \
                and all(isinstance(entry, dict) and not any(entry.get(key) for key in ("answer", "kind", "skipped"))
                        for entry in person["answers"]):
            problems.append("human: this block is still blank. Fill it in, or remove the whole human block if you have no "
                            "answers from people this time")
        else:
            conditions = _human_conditions(person.get("conditions"), day, problems)
            answers = [found for found in (_human_answer(entry, config["prompts"], problems) for entry in person["answers"])
                       if found is not None]
            covered(person["answers"], answers, "human")
            human = {"conditions": conditions, "answers": sorted(answers, key=lambda a: order[a["question"]])}
    if problems:
        shown = problems[:20] + ([f"... and {len(problems) - 20} more"] if len(problems) > 20 else [])
        raise _refused(f"{source.name} is not ready yet, and nothing was stored. That is what this check is for. "
                       f"Fix {'this' if len(problems) == 1 else 'these'} in the file and run the same command again:\n  "
                       + "\n  ".join(shown))
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
    refused = bool(answer.get("refused"))      # `reservation` (an asked reply only): the budget reservation its call settled
    changed = None
    if digest is not None and not refused:
        previous = cx.execute(
            "SELECT a.answer_sha256 FROM archive_chatbot_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
            "WHERE r.kind=? AND r.collected_on<? AND a.chatbot=? AND a.question_id=? AND a.state='answered' AND a.refused=0 "
            "ORDER BY r.collected_on DESC LIMIT 1", (kind, collected_on, label, answer["question"])).fetchone()
        changed = None if previous is None else int(previous[0] != digest)
    cx.execute("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,reason,answer_text,"
               "normalised,answer_sha256,changed,screenshot_sha256,created_at,refused,reservation_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (run_id, question_set, answer["question"], label, kind, answer["state"], answer["reason"], answer["text"],
                normalised, digest, changed, answer.get("screenshot_hash"), created_at, int(refused), answer.get("reservation")))
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
    # One sequence for every kind: hand-entered and asked runs share a database, and the public block tells runs
    # apart by this number. (Scripted runs never sit beside the others, so their numbers are what they always were.)
    number = int(cx.execute("SELECT COALESCE(MAX(number),0)+1 FROM archive_chatbot_runs").fetchone()[0])
    run_id = int(cx.execute(
        "INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256,file_sha256) "
        "VALUES (?,?,?,?,?,?,?)", (number, kind, collected_on, imported_at, json.dumps(question_ids), config_sha, file_sha)).lastrowid)
    return run_id, number


def _store_conditions(cx, run_id: int, chatbot: dict, conditions: dict, groups: list[dict]) -> None:
    """`groups` is the configuration's group list: the app's group id and that group's name are recorded with the run."""
    memory = conditions["memory_off"]
    group_name = next(group["name"] for group in groups if group["id"] == chatbot["group"])
    cx.execute("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,attribution,account,fresh_chat,temporary_chat,"
               "memory_off,web_search,country,app,shown_model,note,group_id,group_name) VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)",
               (run_id, chatbot["label"], chatbot["name"], chatbot["maker"], chatbot.get("attribution", ""), conditions["account"],
                int(conditions["temporary_chat"]), memory if memory == NOT_APPLICABLE else ("true" if memory else "false"),
                conditions["web_search"], conditions["country"], conditions["app"], conditions["shown_model"],
                conditions.get("note", ""), chatbot["group"], group_name))


def _store_model(cx, run_id: int, model: dict, answerer: dict, call: dict, groups: list[dict]) -> None:
    """The row of one MODEL answerer in an asked run, in place of an app's conditions: `model` is its entry of
    the configuration, `answerer` its resolved LIVE_ARCHIVE_MODELS entry, `call` the `archive._call_record` of
    its first recorded call in this run. The endpoint is a NAME; no URL and no key is ever stored."""
    group_name = next(group["name"] for group in groups if group["id"] == model["group"])
    cx.execute("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,group_id,group_name,kind,model,provider,"
               "endpoint,reported_model,model_digest,pricing_basis,sampling,local) VALUES (?,?,?,?,?,?,'model',?,?,?,?,?,?,?,?)",
               (run_id, model["label"], model["name"], model["maker"], model["group"], group_name, answerer["model"],
                answerer["provider"], call["endpoint"], call["reported_model"], call["model_digest"], call["pricing_basis"],
                call["sampling"], call["local"]))


def _store_human_collection(cx, run_id: int, human: dict, conditions: dict) -> None:
    cx.execute("INSERT INTO archive_human_collections(run_id,display_name,meta,source,collected_on,replies) VALUES (?,?,?,?,?,?)",
               (run_id, human["name"], human["meta"], conditions["source"], conditions["collected_on"], conditions["count"]))


def _record_questions(cx, run_id: int, kind: str, items: list[dict]) -> list[str]:
    """Record the questions this run is the first to hold; returns their ids. The caller has already
    checked that every recorded question is still in `items`, unchanged."""
    known = {row[0] for row in cx.execute("SELECT id FROM archive_chatbot_questions")}
    new = [q for q in items if q["id"] not in known]
    for question in new:
        cx.execute("INSERT INTO archive_chatbot_questions(id,position,kind,text,prompt,hook,added,sha256,first_run,list) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (question["id"], int(question["id"][1:]), kind, question["text"], question["prompt"], question["hook"],
                    question["added"], question_hash(question), run_id, question.get("list", "")))
    return [q["id"] for q in new]


def questions_problem(cx, config: dict) -> str | None:
    """Why the configuration's question list does not fit what this database recorded, or None: a recorded
    question is gone, or its text, prompt, hook, date or list was changed."""
    recorded = {row["id"]: row["sha256"] for row in cx.execute("SELECT id,sha256 FROM archive_chatbot_questions WHERE kind!='demo'")}
    dropped = sorted(set(recorded) - set(config["questions"]))
    if dropped:
        return (f"{CONFIG_FILE.name} no longer lists {', '.join(dropped)}, which the first import recorded. "
                "A recorded question never disappears: restore it")
    edited = sorted(i for i in recorded if question_hash(config["prompts"][i]) != recorded[i])
    if edited:
        return (f"{CONFIG_FILE.name}: {', '.join(edited)} was changed since it was first imported (text, prompt, hook, "
                "date or list). A recorded question never changes: restore it and add a new question instead")
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
                           "Point LIVE_DB at your real database (or at a new, empty file) and run the command again. "
                           "Nothing was stored.")
        problem = questions_problem(cx, config)
        if problem:
            raise _refused(f"{problem}. Nothing was stored.")
        for chatbot in collection["chatbots"]:
            if cx.execute("SELECT 1 FROM archive_chatbot_conditions WHERE chatbot=? AND kind='model'", (chatbot["label"],)).fetchone():
                raise _refused(f"{chatbot['label']} is recorded in this database as a model asked by software; a chat app "
                               "needs a label of its own. Nothing was stored.")
            latest = cx.execute("SELECT MAX(r.collected_on) FROM archive_chatbot_conditions c JOIN archive_chatbot_runs r "
                                "ON r.id=c.run_id WHERE c.chatbot=? AND r.kind='manual'", (chatbot["label"],)).fetchone()[0]
            if latest == day:
                raise _refused(f"{chatbot['label']} already has a hand-entered run dated {day}. One run per chat app and day; "
                               "a stored reply is never replaced. If this sheet is a new month, correct collected_on at the "
                               "top of it; if you imported it already, there is nothing more to do. Nothing was stored.")
            if latest is not None and latest > day:
                raise _refused(f"{chatbot['label']} already has a hand-entered run dated {latest}, after {day}. A chat app's "
                               "dates only move forward, so an earlier month cannot be added now. Check collected_on at the "
                               "top of the sheet. Nothing was stored.")
        if collection["human"]:
            latest = cx.execute("SELECT MAX(r.collected_on) FROM archive_human_collections c JOIN archive_chatbot_runs r "
                                "ON r.id=c.run_id WHERE r.kind='manual'").fetchone()[0]
            if latest is not None and latest >= day:
                raise _refused(f"Human answers already have a hand-entered run dated {latest}. One collection per day, and the "
                               "dates only move forward. Check collected_on at the top of the sheet, or take the human block "
                               "out if it is already stored. Nothing was stored.")
        first = not (_has_run(cx, MANUAL) or _has_run(cx, ASKED))   # the first recorded run of any kind fixes the questions
        summary = {"dry_run": dry_run, "collected_on": day, "imported_at": imported_at, "questions": config["questions"],
                   "config_sha256": config["sha256"], "first_import": first, "chatbots": []}
        run_id = number = None
        if not dry_run:
            run_id, number = _open_run(cx, MANUAL, day, config["questions"], config["sha256"], collection["file_sha256"], imported_at)
            summary["new_questions"] = _record_questions(cx, run_id, MANUAL, config["items"])
        for chatbot in collection["chatbots"]:
            if not dry_run:
                _store_conditions(cx, run_id, chatbot, chatbot["conditions"], config["groups"])
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


# ---- Models asked by software (cli archive-chatbot-ask; PAID class) ------------------------------------------
# The same questions, put to models over their APIs (or to local models) by this program. A model answerer is its
# own kind: never an app, never under an app's name. The whole safety path is the model track's: the opt-in, the
# required cap, the budget settings, the block on an unconfirmed charge, and `archive._paid_call` for every call.

ASK_NOTE = ("These are replies of MODELS this program asked, not of any chat app. They are published only through the "
            "reviewed path: python -m live_genesis.snapshot --export --archive-only")


def classify_asked(text: Any, stop: str) -> tuple[str, str, str | None]:
    """(state, reason, text to store) for one reply a model gave: a reply cut off at the output limit is no
    answer (as on the model track); everything else is judged exactly as a pasted reply is (`classify_reply`)."""
    if stop == "length":
        return "no_answer", archive.TRUNCATED, None
    return classify_reply(text)


def _model_answerers(config: dict, only: str | None) -> list[dict]:
    models = config["models"]
    if not models:
        raise _refused(f'No model answerer is configured: {CONFIG_FILE.name} holds no entry with "kind": "model". '
                       "Nothing was asked.")
    if only is not None:
        wanted = [name.strip() for name in only.split(",") if name.strip()]
        if not wanted or any(name not in {model["label"] for model in models} for name in wanted):
            raise _refused(f"--models names a label that is not a model answerer of {CONFIG_FILE.name}")
        models = [model for model in models if model["label"] in wanted]
    return models


def _asked_questions(config: dict, questions: str | None, question_list: str | None) -> list[dict]:
    """The questions one ask covers, in the file's order: all of them, or only those of one list
    (`question_list`, --list: "main" or "trending"), or only the named ids (`questions`, --questions: comma
    separated). An unknown list or id, both options together, or a choice that leaves nothing is refused."""
    items = config["items"]
    if questions is None and question_list is None:
        return list(items)
    if questions is not None and question_list is not None:
        raise _refused("Give --list or --questions, not both. Nothing was asked.")
    if question_list is not None:
        if question_list not in (MAIN_LIST,) + LISTS:
            raise _refused(f"--list must be one of: {', '.join((MAIN_LIST,) + LISTS)}. Nothing was asked.")
        chosen = [q for q in items if list_of(q) == question_list]
        if not chosen:
            raise _refused(f"No question of {CONFIG_FILE.name} is on the list \"{question_list}\". Nothing was asked.")
        return chosen
    wanted = [name.strip() for name in questions.split(",") if name.strip()]
    if not wanted or any(name not in config["prompts"] for name in wanted):
        raise _refused(f"--questions names an id that is not a question of {CONFIG_FILE.name} (ids look like C011, separated "
                       "by commas). Nothing was asked.")
    return [q for q in items if q["id"] in wanted]


def ask_plan(only: str | None = None, *, questions: str | None = None, question_list: str | None = None) -> dict:
    """Free; contacts nothing and stores nothing. What `cli archive-chatbot-ask` would do: the model answerers
    of the configuration (`only`, --models: a subset of their labels), each resolved through LIVE_ARCHIVE_MODELS
    and the endpoint settings exactly as `archive-run` resolves it (`archive.answerers`, `archive.resolve`),
    the questions (all, or the subset `_asked_questions` picks; `left_out` are the others), the number of
    calls (one per model and question) and the worst-case estimate.
    Raises ArchiveRefused / controls.ConfigError when a setting is unusable.

    Each entry of `models` is the resolved answerer with `label` = the chat-track label, `answerer` = the
    LIVE_ARCHIVE_MODELS label and `chat` = the configuration entry."""
    config = load_config()
    chosen = _asked_questions(config, questions, question_list)     # an unknown list or id is refused before anything else
    wanted = _model_answerers(config, only)
    configured = {answerer["label"]: answerer for answerer in archive.answerers()}
    missing = [f"{model['label']} (answerer {model['answerer']})" for model in wanted if model["answerer"] not in configured]
    if missing:
        raise _refused("LIVE_ARCHIVE_MODELS does not list the answerer of: " + ", ".join(missing) + ". Nothing was asked.")
    by_answerer = {model["answerer"]: model for model in wanted}
    resolved, budget = archive.resolve([configured[model["answerer"]] for model in wanted], archive.max_output_tokens())
    models = [{**answerer, "label": by_answerer[answerer["label"]]["label"], "answerer": answerer["label"],
               "chat": by_answerer[answerer["label"]]} for answerer in resolved]
    holds = {a["label"]: {q["id"]: archive.call_hold(q["prompt"], a["input_rate"], a["output_rate"], a["max_output_tokens"])
                          for q in chosen} for a in models}
    option = f"--list {question_list} " if question_list is not None else \
        f"--questions {','.join(q['id'] for q in chosen)} " if questions is not None else ""
    return {"config": config, "collected_on": today().isoformat(), "models": models, "questions": chosen,
            "left_out": [q["id"] for q in config["items"] if q not in chosen], "option": option,
            "calls": len(models) * len(chosen), "holds": holds, "spend_limit_usd": budget["spend_limit_usd"],
            "worst_case_usd": math.fsum(value for per in holds.values() for value in per.values())}


def render_ask_plan(planned: dict) -> str:
    """Text for `archive-chatbot-ask` (and its --dry-run). Endpoint NAMES only: no URL, no host, no key."""
    basis = {providers.BASIS_RATES: "priced at its rates", providers.BASIS_LOCAL: "local (recorded as $0 estimated)",
             providers.BASIS_FREE: "declared-free (UNVERIFIED)"}
    lines = [f"Answers Over Time, chat-app questions put to models by software. Run dated {planned['collected_on']}.",
             "Model answerers (label = endpoint:model, shown as), in the order they are asked:"]
    lines += [f"  {a['label']} = {a['endpoint']}:{a['model']}, shown as \"{a['chat']['name']}\", {basis[a['basis']]}, "
              f"output limit {a['max_output_tokens']} tokens" for a in planned["models"]]
    left_out = planned.get("left_out") or []
    total = len(planned["questions"]) + len(left_out)
    lines.append(f"Questions ({len(planned['questions'])}{f' of {total}' if left_out else ''}), each asked once of each model "
                 "with its exact prompt:")
    lines += [f"  {q['id']}  {q['text']}" for q in planned["questions"]]
    if left_out:
        lines.append(f"Left out this time ({len(left_out)}): {', '.join(left_out)}. They are not asked, and this run records "
                     "nothing about them: their pages stay as they are.")
    if any(a["basis"] == providers.BASIS_FREE for a in planned["models"]):
        lines.append("  " + archive.DECLARED_FREE_NOTE)
    lines += [f"Calls: {planned['calls']} ({len(planned['questions'])} questions x {len(planned['models'])} models).",
              f"Worst-case estimate: ${planned['worst_case_usd']:.6f}. This is {controls.COST_CAVEAT}.",
              "If a call fails: " + archive.FAILURE_RULE,
              "The PAID command, to be run only on the owner's explicit word (--max-usd and LIVE_ALLOW_PAID_CALLS=YES are "
              "required for every run, a free one included):",
              f"  python -m live_genesis.cli archive-chatbot-ask {planned.get('option', '')}--max-usd "
              f"{controls.suggested_cap(planned['worst_case_usd'])}"]
    return "\n".join(lines)


def ask(*, max_usd: float, only: str | None = None, questions: str | None = None, question_list: str | None = None,
        pause_seconds: float = 0.0) -> dict:
    """`cli archive-chatbot-ask`: PAID when `engine.infer` is the real adapter. Run only on the owner's explicit
    instruction. Asks every model answerer of the configuration each chat-app question ONCE, with the question's
    exact prompt as the one user message (and the model track's fixed system line, `archive.SYSTEM_LINE`), and
    stores the replies as one run of kind `asked` dated today.

    `question_list` (--list) or `questions` (--questions) asks only some questions (`_asked_questions`; an unknown
    list or id is refused before anything else is looked at). The run row then records exactly the ids it asked:
    a question it left out has no entry for this date, so no page, data file or `changed` flag treats it as
    unanswered. Every question of the configuration is still recorded (fixed) by the first run, asked or not.

    Refuses (ArchiveRefused / controls.ConfigError), before any provider request and before anything is
    stored, when: no model answerer is configured or one names an answerer LIVE_ARCHIVE_MODELS does not list;
    the paid opt-in is not exactly YES (also for a run that costs nothing); `max_usd` is not a positive finite
    number; a provider, endpoint or budget setting is unusable or a key is missing; the adapter was replaced
    (there is no mock mode: a stored reply is a real one); the worst case does not fit under `max_usd` or the
    remaining local ceiling; the database holds scripted demo answers; the configuration dropped or changed a
    recorded question; a model answerer already has a run today or later (one run per answerer and day, dates
    only move forward) or its label is recorded as a chat app; or an unconfirmed reservation is outstanding.

    Every call goes through `archive._paid_call` (reserve -> infer -> settle) and so follows
    archive.FAILURE_RULE: a failed call on a zero-priced endpoint releases its $0 hold and skips THAT model; on
    a priced endpoint the hold stays unconfirmed and the run STOPS; an interrupted priced call is closed as
    unconfirmed. Nothing is retried. Provider error text, keys and base URLs are never stored or returned.

    The run row, the questions (the first recorded run of any kind fixes the list) and a model's own row are
    written with that model's first recorded reply, so a run in which no call succeeded stores nothing. Each
    reply is judged by `classify_asked`; a reply the guards match is stored as withheld, without its text.
    Returns counts, categories and reservation ids only: never the text of a reply."""
    from . import engine   # lazy: only this command needs the adapter
    first_look = load_config()
    _asked_questions(first_look, questions, question_list)
    _model_answerers(first_look, only)
    if os.getenv("LIVE_ALLOW_PAID_CALLS") != "YES":
        raise _refused("Explicit opt-in required for paid calls: set LIVE_ALLOW_PAID_CALLS=YES")
    if isinstance(max_usd, bool) or not isinstance(max_usd, (int, float)) or not math.isfinite(max_usd) or max_usd <= 0:
        raise _refused("--max-usd is required and must be a finite positive number. No paid call was made.")
    planned = ask_plan(only, questions=questions, question_list=question_list)
    asked_ids = [q["id"] for q in planned["questions"]]
    config, models, day, worst = planned["config"], planned["models"], planned["collected_on"], planned["worst_case_usd"]
    archive._check_ready(models)
    if engine.infer is not engine.REAL_INFER:
        raise _refused("The inference adapter was replaced. archive-chatbot-ask has no mock mode: what it stores is shown as "
                       "a model's real reply. Nothing was asked.")
    if worst > max_usd:
        raise _refused(f"Worst-case estimate ${worst:.6f} for {planned['calls']} calls exceeds --max-usd ${max_usd:.6f}. "
                       "No paid call was made.")
    with core.connect() as cx:
        pending = controls.unconfirmed_ids(cx)
        if pending:        # before every other look at the database: an unconfirmed charge blocks every paid call
            raise _refused(controls.reconcile_message(pending))
        if archive._has(cx, "demo") or _has_run(cx, DEMO):
            raise _refused("This database holds scripted demo answers; real replies are never mixed with them. "
                           "Use the real database. No paid call was made.")
        problem = questions_problem(cx, config)
        if problem:
            raise _refused(f"{problem}. No paid call was made.")
        for model in models:
            latest, kind = cx.execute("SELECT MAX(r.collected_on),MIN(c.kind) FROM archive_chatbot_conditions c JOIN "
                                      "archive_chatbot_runs r ON r.id=c.run_id WHERE c.chatbot=?", (model["label"],)).fetchone()
            if kind == "app":
                raise _refused(f"{model['label']} is recorded in this database as a chat app; a model answerer needs a label "
                               "of its own. No paid call was made.")
            if latest is not None and latest >= day:
                raise _refused(f"{model['label']} already has a run dated {latest}. One run per model answerer and day, and "
                               "the dates only move forward (use --models to ask the others). No paid call was made.")
        baseline = core._budget(cx)["committed_usd"]
    ceiling = controls.effective_ceiling(planned["spend_limit_usd"])
    if baseline + worst > ceiling:
        raise _refused(f"Worst-case estimate ${worst:.6f} does not fit under the local ceiling: ${baseline:.6f} of "
                       f"${ceiling:.2f} is already committed. No paid call was made.")
    cap_ceiling = min(planned["spend_limit_usd"], baseline + max_usd + controls.LEDGER_ROUNDING_USD)
    status, reason = "incomplete", "interrupted before the run finished"
    run: dict = {"id": None, "number": None, "first": False, "new_questions": []}
    spent: list[float] = []
    counts = {state: 0 for state in archive.STATES}
    progress = {a["label"]: {"outcome": "not_asked", "reason": "", "recorded": 0, "reservation": None} for a in models}
    open_hold: list = [None]   # (reservation, hold, label) of a priced call between its reservation and its settlement
    try:
        for answerer in models:
            label, state_of, record = answerer["label"], progress[answerer["label"]], answerer["record"]
            try:
                where = record if record is not None else providers.compat_settings() if answerer["local"] else None
            except providers.ProviderConfigError:
                where = None
            digest = archive.lookup_model_digest(where, answerer["model"])
            extra: dict[str, Any] = {}   # only what this call needs
            if record is not None:
                extra["endpoint"] = record
            if answerer["send_sampling"]:
                extra["sampling"] = archive.sampling_for(1)
            wait = max(float(pause_seconds or 0.0), answerer["pause_seconds"], 0.0)
            state_of["outcome"] = "stopped"   # until this model's last question is stored
            for question in planned["questions"]:
                hold = planned["holds"][label][question["id"]]
                if math.fsum(spent) + hold > max_usd + controls.EPSILON_USD:
                    reason = "run cap reached; no further calls made"
                    break
                done = archive._paid_call(answerer, question["prompt"], hold, cap_ceiling, answerer["max_output_tokens"], extra, open_hold)
                if done["outcome"] == "refused":
                    reason = done["reason"]
                    break
                if done["outcome"] == "skipped":   # zero-priced: its $0 hold is released; this model only
                    state_of.update(outcome="skipped", reason=done["category"], reservation=done["reservation"])
                    with core.connect() as cx:
                        controls.audit(cx, f"archive-chatbot-ask: {label} skipped ({done['category']}); $0 hold "
                                           f"{done['reservation']} released (pricing basis {answerer['basis']})")
                    break
                if done["outcome"] == "stopped":
                    reason = done["reason"]
                    state_of.update(reason=done["category"], reservation=done["reservation"])
                    break
                spent.append(done["usd"])
                state, why, kept = classify_asked(done["text"], done["stop"])
                stamp = core.utcnow()
                with core.connect() as cx:
                    cx.execute("BEGIN IMMEDIATE")
                    if run["id"] is None:
                        run["first"] = not (_has_run(cx, MANUAL) or _has_run(cx, ASKED))
                        run["id"], run["number"] = _open_run(cx, ASKED, day, asked_ids, config["sha256"], "", stamp)
                        run["new_questions"] = _record_questions(cx, run["id"], ASKED, config["items"])
                    if not state_of["recorded"]:
                        _store_model(cx, run["id"], answerer["chat"], answerer,
                                     archive._call_record(answerer, done["result"], done["latency_ms"], digest), config["groups"])
                    _store(cx, run["id"], ASKED, day, QUESTION_SET, label,
                           {"question": question["id"], "state": state, "reason": why, "text": kept,
                            "reservation": done["reservation"]}, stamp)
                state_of["recorded"] += 1
                counts[state] += 1
                if done["usd"] > hold:
                    reason = (f"recorded usage exceeded the hold; reservation {done['reservation']} is unconfirmed. "
                              + archive.UNCONFIRMED_HINT.format(done["reservation"]))
                    state_of["reservation"] = done["reservation"]
                    break
                if wait:
                    time.sleep(wait)
            else:
                state_of["outcome"] = "complete"
                continue
            if state_of["outcome"] == "skipped":
                continue          # this model only; the next one is asked
            break
        else:
            skipped = [f"{a['label']} ({progress[a['label']]['reason']})" for a in models if progress[a["label"]]["outcome"] == "skipped"]
            status = "complete_with_gaps" if skipped else "complete"
            reason = ("skipped, no further question asked of: " + ", ".join(skipped)) if skipped else ""
    finally:
        # Also on KeyboardInterrupt. A priced hold that was neither settled nor closed (the call was interrupted, so it
        # may have been sent and billed) becomes unconfirmed: the next paid command stops for `cli reconcile`.
        if open_hold[0] is not None:
            held = open_hold[0]
            try:
                core.close_reservation(held[0], "unconfirmed")
            except ValueError:
                pass   # already closed inside archive._paid_call
            else:
                reason = (f"interrupted during a call; ${held[1]:.6f} kept as unconfirmed (reservation {held[0]}). "
                          + archive.UNCONFIRMED_HINT.format(held[0]))
                progress[held[2]].update(reason="other", reservation=held[0])
        with core.connect() as cx:
            outcomes = ", ".join(f"{a['label']} {progress[a['label']]['outcome']} {progress[a['label']]['recorded']}" for a in models)
            controls.audit(cx, f"archive-chatbot-ask {'run ' + str(run['number']) if run['id'] else '(nothing stored)'}: "
                               f"collected {day}, {status}, {outcomes}, "
                               + (f"questions {','.join(asked_ids)}, " if planned["left_out"] else "")
                               + f"config sha256 {config['sha256'][:16]}")
    return {"run": run["number"], "collected_on": day, "status": status, "stop_reason": reason,
            "first_run": run["first"], "new_questions": run["new_questions"], "questions": asked_ids,
            "left_out": planned["left_out"], "answers": counts,
            "estimated_usd": round(math.fsum(spent), 6), "max_usd": float(max_usd),
            "models": [{"chatbot": a["label"], "answerer": a["answerer"], "endpoint": a["endpoint"], "model": a["model"],
                        "pricing_basis": a["basis"], **progress[a["label"]]} for a in models]}


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
# Two scripted questions of the "trending" list: added at the third scripted date and asked from then on, so the
# sample site shows the Trending tab and questions that start later than the others. Made up like everything here:
# they are not taken from any list of searches.
DEMO_TRENDING_FROM = 3
DEMO_TRENDING = (
    ("Socks with sandals: brave or wrong?", "Socks with sandals?", {
        "chat-app-a": ((3, "Brave. Wrong, but brave."),),
        "chat-app-b": ((3, "Wrong, and my toes agree."), (4, "Brave, if the socks have little avocados on them.")),
        "chat-app-c": ((3, "It's a cry for help with good arch support."),),
        HUMAN_LABEL: ((3, "My dad does it and he has never been happier.", "quoted"),)}),
    ("Does the milk go in before the cereal?", "Milk first or cereal first?", {
        "chat-app-a": ((3, "Cereal first. Milk first is how you get soup with regrets."),),
        "chat-app-b": ((3, "Cereal first, unless you enjoy chaos."),),
        "chat-app-c": ((3, "Milk first, and I'd like to speak to nobody about it."),),
        HUMAN_LABEL: ((3, "Nearly everyone said cereal first; two people said bowl last.", "summarised"),)}),
)
# (question position, answerer, run) scripted as a reply that is stored and never shown, so the "left out" line is real.
DEMO_SKIPPED = {(4, "chat-app-b", 3): f"{SKIPPED}: the app showed an error (scripted)"}
DEMO_REFUSED = {(2, "chat-app-a", 4): "I'd rather not say anything unkind about anyone."}


def demo_items() -> list[dict]:
    """The scripted question list as question dicts (ids C001...): the main questions, then the trending ones."""
    main = [{"id": f"C{n:03d}", "text": text, "prompt": prompt_of(text), "hook": hook, "added": DEMO_DATES[0]}
            for n, (text, hook, _script) in enumerate(DEMO_QUESTIONS, 1)]
    return main + [{"id": f"C{n:03d}", "text": text, "prompt": prompt_of(text), "hook": hook,
                    "added": DEMO_DATES[DEMO_TRENDING_FROM - 1], "list": TRENDING_LIST}
                   for n, (text, hook, _script) in enumerate(DEMO_TRENDING, len(main) + 1)]


def _at(steps: tuple, run: int):
    return [step for step in steps if step[0] <= run][-1]


def demo(cx, sets: list[dict] | None = None) -> dict:
    """Scripted chat-app runs for `cli demo-archive`, written inside its transaction. Three made-up apps
    and "Human (sample)", four dated runs, the scripted question list above, every row source `demo`.
    The two scripted trending questions join at the third date: the runs before it neither list nor record them.
    No app and no person was asked. `sets` is ignored (the track has its own questions)."""
    every = demo_items()
    scripts = DEMO_QUESTIONS + DEMO_TRENDING
    groups = [dict(DEFAULT_GROUP)]      # one group, as on the real site today; several groups are covered by the tests
    apps = [{"label": label, "name": name, "maker": maker, "attribution": DEMO_ATTRIBUTION.get(label, ""), "group": groups[0]["id"]}
            for label, name, maker in DEMO_APPS]
    written = 0
    for number, day in enumerate(DEMO_DATES, 1):
        stamp = f"{day}T12:00:00+00:00"
        items = [q for q in every if "list" not in q or number >= DEMO_TRENDING_FROM]   # the list as it stood on that date
        ids = [q["id"] for q in items]
        run_id, _ = _open_run(cx, DEMO, day, ids, config_hash(apps, items, DEMO_HUMAN, groups), "", stamp)
        _record_questions(cx, run_id, DEMO, items)
        for app in apps:
            label = app["label"]
            if DEMO_FIRST_RUN.get(label, 1) > number:
                continue
            _store_conditions(cx, run_id, app, _at(DEMO_CONDITIONS[label], number)[1], groups)
            for position, (question, (_text, _hook, script)) in enumerate(zip(items, scripts), 1):
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
            for question, (_text, _hook, script) in zip(items, scripts):
                _from, text, manner = _at(script[HUMAN_LABEL], number)
                state, reason, kept = classify_reply(text, HUMAN_CHARS)
                _store_human(cx, run_id, DEMO, day, {"question": question["id"], "state": state, "reason": reason, "text": kept,
                                                    "manner": manner if state == "answered" else None}, stamp)
                written += 1
    return {"chatbot_runs": len(DEMO_DATES), "chatbots": [label for label, _name, _maker in DEMO_APPS], "chatbot_answers": written}


# ---- Public payload: the `chatbots` block of archive.public_payload ---------------------------------------

BLOCK_KEYS = ("groups", "apps", "human", "questions", "runs", "answers", "human_answers")
APP_KEYS = ("label", "name", "maker", "attribution", "group")       # plus "kind" ("model") on a model answerer, and only there
MODEL_KEYS = ("model",) + archive.PUBLIC_CALL_FIELDS                # one model answerer in one run: `models` of that run
GROUP_KEYS = ("id", "name")
HUMAN_KEYS = ("name", "meta")
RUN_KEYS = ("number", "collected_on", "imported_at", "demo", "config_hash", "questions", "conditions", "human")   # plus "models" on an asked run
ANSWER_KEYS_PUBLIC = ("id", "run", "question", "chatbot", "source", "state", "reason", "text", "changed", "screenshot_hash", "refused")
HUMAN_ANSWER_KEYS_PUBLIC = ("id", "run", "question", "source", "state", "reason", "text", "manner", "changed")
# The Human column's meta line as imports stored it before 14 November 2026 -> what the public payload carries (owner
# decision: nothing published says how replies were collected). The database row stays as it was.
LEGACY_HUMAN_META = {"written by people · collected by hand": "written by people"}


def _public_reason(reason: str) -> str:
    """A skipped question is published as "skipped" alone: the owner's note behind it stays in the database
    (it may say what the app wrote instead of answering)."""
    return SKIPPED if reason.startswith(SKIPPED) else reason


def _public_answer(row: dict, run_number: int) -> dict:
    """One stored chat-app answer as the public payload carries it. A reply the owner marked as a refusal
    is published as the FACT only (decided by default, 15 November 2026): `refused` true, state
    `no_answer`, reason "refused" and no text, whether or not the owner pasted what the app said. The
    words stay in the database row, which is never changed. The shape is the one a refusal stored
    without its words always had, so the schema markers are unchanged."""
    refused = bool(row["refused"])
    return {"id": row["id"], "run": run_number, "question": row["question_id"], "chatbot": row["chatbot"],
            "source": row["source"], "state": "no_answer" if refused else row["state"],
            "reason": REFUSED if refused else _public_reason(row["reason"]), "text": None if refused else row["answer_text"],
            "changed": None if row["changed"] is None else bool(row["changed"]), "screenshot_hash": row["screenshot_sha256"],
            "refused": refused}


def public_block(cx, tables: set[str]) -> dict | None:
    """The chat-app track for the pages and the export, or None when the database holds no such run (the
    payload then has no `chatbots` key at all and is what it always was). Runs are ordered by the day
    the owner asked. `groups` are the answerer groups ({id, name}) in the order their first app was
    recorded, and every app carries its `group`; a row stored before groups existed belongs to the default
    group. `questions` are the recorded questions (id, text, prompt, hook, added; and `list` "trending" on a
    question of that list, a key no main question has); a run's `questions` are the ids THAT run asked, which
    may be only some of them (`cli archive-chatbot-ask --list`); `human` is
    the Human column's wording ({name, meta}) or None when no human answer was ever collected; each run
    carries the apps' `conditions` and `human` ({source, collected_on, count} or None). A reply the owner
    marked as a refusal is here as a fact (`refused`: true, no text; `_public_answer`) and on no page; a
    skipped question carries the owner's short reason, never a reply. Never the imported file's hash or any
    path; a screenshot is its sha256 only.

    A MODEL answerer (asked by software) is an entry of `apps` with `kind` "model" (an app has no such key),
    and its run carries `models`: per label {model, endpoint, reported_model, model_digest, pricing_basis,
    sampling_pinned, local} (the public part of the call record: the endpoint NAME, twelve digits of a local
    digest; never a URL). Its answers have the source `asked`. None of these keys or values exists in a
    database without such a run: its block is what it always was."""
    if not set(TABLE_NAMES) <= tables:
        return None
    # `list` only on a question of a list other than the main one (.get: a database opened read-only before migrate()
    # ran has no such column): a block without such a question is what it always was.
    questions = [{**{key: row[key] for key in QUESTION_KEYS}, **({"list": row["list"]} if row.get("list") else {})}
                 for row in (dict(r) for r in cx.execute("SELECT * FROM archive_chatbot_questions ORDER BY position"))]
    own = {q["id"] for q in questions}
    # A run from the track's first shape (its ids referred to the core and trending lists) stays in the database and is
    # never shown: it has no question of this track's own list.
    runs = [dict(r) for r in cx.execute("SELECT * FROM archive_chatbot_runs ORDER BY collected_on,number")
            if set(json.loads(r["question_ids"])) <= own]
    if not runs:
        return None
    number = {r["id"]: r["number"] for r in runs}
    conditions: dict[int, dict] = {}
    asked: dict[int, dict] = {}
    apps: dict[str, dict] = {}
    groups: dict[str, dict] = {}
    for row in cx.execute("SELECT c.* FROM archive_chatbot_conditions c JOIN archive_chatbot_runs r ON r.id=c.run_id "
                          "ORDER BY r.collected_on,r.number,c.rowid"):
        row = dict(row)
        if row["run_id"] not in number:
            continue
        model = row.get("kind") == MODEL        # .get: a database opened read-only before migrate() ran has no such column
        memory = row["memory_off"]
        if model:
            asked.setdefault(row["run_id"], {})[row["chatbot"]] = {"model": row["model"],
                                                                   **archive._public_call({**row, "source": "engine"})}
        else:
            conditions.setdefault(row["run_id"], {})[row["chatbot"]] = {
                "account": row["account"], "fresh_chat": bool(row["fresh_chat"]), "temporary_chat": bool(row["temporary_chat"]),
                "memory_off": memory if memory == NOT_APPLICABLE else memory == "true", "web_search": row["web_search"],
                "country": row["country"], "app": row["app"], "shown_model": row["shown_model"], "note": row["note"]}
        known = apps.setdefault(row["chatbot"], {"label": row["chatbot"]})
        group = row["group_id"] or DEFAULT_GROUP["id"]
        groups.setdefault(group, {"id": group})["name"] = row["group_name"] or DEFAULT_GROUP["name"]
        known.update(name=row["display_name"], maker=row["maker"], attribution=row["attribution"], group=group)   # the newest run's wording
        if model:
            known["kind"] = MODEL
    human, collected = None, {}
    for row in cx.execute("SELECT c.* FROM archive_human_collections c JOIN archive_chatbot_runs r ON r.id=c.run_id "
                          "ORDER BY r.collected_on,r.number"):
        collected[row["run_id"]] = {"source": row["source"], "collected_on": row["collected_on"], "count": row["replies"]}
        human = {"name": row["display_name"], "meta": LEGACY_HUMAN_META.get(row["meta"], row["meta"])}   # the newest run's wording
    answers = [_public_answer(a, number[a["run_id"]])
               for a in (dict(r) for r in cx.execute(
                   "SELECT a.* FROM archive_chatbot_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
                   "ORDER BY r.collected_on,r.number,a.id")) if a["run_id"] in number]
    human_answers = [{"id": a["id"], "run": number[a["run_id"]], "question": a["question_id"], "source": a["source"],
                      "state": a["state"], "reason": _public_reason(a["reason"]), "text": a["answer_text"], "manner": a["manner"],
                      "changed": None if a["changed"] is None else bool(a["changed"])}
                     for a in (dict(r) for r in cx.execute(
                         "SELECT a.* FROM archive_human_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
                         "ORDER BY r.collected_on,r.number,a.id"))]
    used = {app["group"] for app in apps.values()}          # an app that moved leaves no empty group behind
    block = {"groups": [group for group in groups.values() if group["id"] in used], "apps": list(apps.values()),
             "human": human, "questions": questions,
             "runs": [{"number": r["number"], "collected_on": r["collected_on"], "imported_at": r["imported_at"],
                       "demo": r["kind"] == DEMO, "config_hash": r["config_sha256"], "questions": json.loads(r["question_ids"]),
                       "conditions": conditions.get(r["id"], {}), "human": collected.get(r["id"]),
                       **({"models": asked[r["id"]]} if r["id"] in asked else {})} for r in runs],
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


def model_problem(value: Any) -> str | None:
    """Why the public record of one model answerer in one run is not complete and well-formed, or None. The
    endpoint is a plain NAME and neither model name can be a web address: no URL can pass."""
    if not isinstance(value, dict) or set(value) != set(MODEL_KEYS):
        return "a model answerer's record is missing or incomplete"
    reported = value["reported_model"]
    if not archive.model_id_ok(value["model"]) or "://" in value["model"] or not isinstance(value["endpoint"], str) \
            or value["pricing_basis"] is None or type(value["local"]) is not bool \
            or (isinstance(reported, str) and "://" in reported):
        return "a model answerer's record holds a value that is not allowed"
    return archive._call_problem(value)


def has_models(block: Any) -> bool:
    """Does a (well-formed) block hold a model answerer? Decides the schema marker of the payload around it."""
    return isinstance(block, dict) and any(isinstance(app, dict) and "kind" in app for app in block.get("apps") or ())


def has_lists(block: Any) -> bool:
    """Does a (well-formed) block hold a question of a list other than the main one (`list` on the question)?
    Decides the schema marker of the payload around it, before the model answerers do."""
    return isinstance(block, dict) and any(isinstance(q, dict) and "list" in q for q in block.get("questions") or ())


def payload_schema(block: dict) -> str:
    """The schema marker of a whole payload that holds this block (archive.public_payload)."""
    return archive.PAYLOAD_SCHEMA_LISTS if has_lists(block) else archive.PAYLOAD_SCHEMA_ASKED if has_models(block) \
        else archive.PAYLOAD_SCHEMA_CHATBOTS


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
    if len(block["apps"]) > MAX_CHATBOTS:
        return "chatbots: too many chat apps"
    for group in block["groups"]:
        if not isinstance(group, dict) or not all(isinstance(group.get(key), str) for key in GROUP_KEYS) \
                or not 0 < len(group["name"]) <= NAME_CHARS or "\n" in group["name"]:
            return "chatbots: a group lacks its id or name"
    asked_labels: set[str] = set()      # the model answerers: a maker is optional, an attribution never there
    for app in block["apps"]:
        model = isinstance(app, dict) and "kind" in app
        if not isinstance(app, dict) or not all(isinstance(app.get(key), str) for key in APP_KEYS) \
                or not CHATBOT_LABEL.fullmatch(app["label"]) or app["label"] in labels or app["label"] in RESERVED_LABELS \
                or not 0 < len(app["name"]) <= NAME_CHARS or not (0 if model else 1) <= len(app["maker"]) <= NAME_CHARS \
                or len(app["attribution"]) > ATTRIBUTION_CHARS or (model and (app["kind"] != MODEL or app["attribution"])):
            return "chatbots: a chat app lacks its label, name, maker, attribution or group, or a label repeats"
        labels.append(app["label"])
        if model:
            asked_labels.add(app["label"])
    names = [app["name"].casefold() for app in block["apps"] if app["label"] not in asked_labels]
    if any(name in app["name"].casefold() for app in block["apps"] if app["label"] in asked_labels for name in names):
        return "chatbots: a model answerer carries the name of a chat app"
    if block["apps"] or block["groups"]:
        problem = groups_problem(block["groups"], block["apps"])
        if problem:
            return f"chatbots: {problem}"
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
        if "list" in question and question["list"] not in LISTS:
            return "chatbots: a question names a list that does not exist"
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
        if not isinstance(conditions, dict) or not all(label in labels and label not in asked_labels for label in conditions):
            return "chatbots: a run lacks its conditions or names an unknown chat app"
        models = run.get("models", {})
        if not isinstance(models, dict) or ("models" in run and not models) or not all(label in asked_labels for label in models) \
                or (models and (conditions or person is not None or run["demo"])):
            return ("chatbots: a run's model answerers are malformed or unknown, or share a run with a chat app, a human "
                    "collection or scripted data")
        for value in models.values():
            problem = model_problem(value)
            if problem:
                return f"chatbots: {problem}"
        if person is False or (person is not None and (
                human is None or not isinstance(person, dict) or set(person) != set(HUMAN_CONDITION_KEYS)
                or not isinstance(person["source"], str) or not 0 < len(person["source"]) <= HUMAN_SOURCE_CHARS
                or not isinstance(person["collected_on"], str) or not archive.ADDED_DATE.fullmatch(person["collected_on"])
                or not (person["count"] is None or (type(person["count"]) is int and person["count"] >= 1)))):
            return "chatbots: a run's human collection is missing or malformed"
        if not conditions and person is None and not models:
            return "chatbots: a run holds neither a chat app nor a human collection"
        for label, value in conditions.items():
            problem = conditions_problem(value)
            if problem:
                return f"chatbots: {problem}"
        for label in list(conditions) + list(models) + ([HUMAN_LABEL] if person else []):
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
        if answer["chatbot"] not in run["conditions"] and answer["chatbot"] not in run.get("models", {}):
            return "chatbots: an answer belongs to a chat app whose conditions its run did not record"
        if (answer["source"] == DEMO) != run["demo"] or (answer["source"] == ASKED) != (answer["chatbot"] in run.get("models", {})):
            return "chatbots: an answer's source does not fit its run"
        if answer["source"] == ASKED and (answer["refused"] or answer.get("screenshot_hash") is not None):
            return "chatbots: an asked reply carries a refusal mark or a screenshot"
        if _text_problem(answer, REPLY_CHARS):
            return "chatbots: an answer's text does not fit its state or is longer than the archive stores"
        if answer["refused"] and answer["changed"] is not None:
            return "chatbots: a refused reply carries a changed flag"
        if answer["refused"] and (answer.get("text") is not None or answer["state"] != "no_answer"):
            return "chatbots: a refused reply carries its words; only the fact that it was refused is published"
        shot = answer.get("screenshot_hash")
        if shot is not None and not (isinstance(shot, str) and HASH.fullmatch(shot)):
            return "chatbots: a screenshot is not given as a sha256 hash"
        key = (answer["run"], answer["question"], answer["chatbot"])
        if key in seen:
            return "chatbots: two answers share one run, question and chat app"
        seen.add(key)
    for answer in block["human_answers"]:
        if not isinstance(answer, dict) or type(answer.get("id")) is not int or answer.get("run") not in runs \
                or answer.get("source") not in (MANUAL, DEMO) or answer.get("state") not in archive.STATES \
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
    checks = [("chatbots", block, BLOCK_KEYS, ())]
    checks += [("a group", group, GROUP_KEYS, ()) for group in block["groups"]]
    checks += [("a chat app", app, APP_KEYS, ("kind",)) for app in block["apps"]]
    checks += [("the human column", block["human"], HUMAN_KEYS, ())] if block["human"] is not None else []
    checks += [(f"chat-app question {q['id']}", q, QUESTION_KEYS, ("list",)) for q in block["questions"]]
    checks += [(f"chat-app run {run['number']}", run, RUN_KEYS, ("models",)) for run in block["runs"]]
    checks += [(f"a model answerer of chat-app run {run['number']}", facts, MODEL_KEYS, ())
               for run in block["runs"] for facts in run.get("models", {}).values()]
    checks += [(f"chat-app answer id {a['id']}", a, ANSWER_KEYS_PUBLIC, ()) for a in block["answers"]]
    checks += [(f"human answer id {a['id']}", a, HUMAN_ANSWER_KEYS_PUBLIC, ()) for a in block["human_answers"]]
    for place, node, required, optional in checks:
        problem = archive._keys_problem(place, node, required, optional)
        if problem:
            return problem
    return None


def export_problem(block: dict) -> str | None:
    """Why a well-formed block may not be PUBLISHED, or None (snapshot --export / --check): scripted
    rows are never reviewed data; every chat app must be one of the checked-in configuration, under the
    name and maker that file gives; the human column must carry that file's wording; and every question
    must be a question of that file, word for word (so none holds a word the file's own check refuses).
    `block` already passed block_problem."""
    if any(run["demo"] for run in block["runs"]) or any(a["source"] not in (MANUAL, ASKED) for a in block["answers"]) \
            or any(a["source"] != MANUAL for a in block["human_answers"]):
        return "it holds scripted chat-app answers; the demo is published without review"
    try:
        config = load_config()
    except archive.ArchiveRefused as err:
        return f"the chat-app configuration cannot be read ({err})"
    known = {c["label"]: c for c in config["chatbots"]}
    models = {m["label"]: m for m in config["models"]}
    for app in block["apps"]:
        if "kind" in app:      # a model answerer: one of the file's, under the name, maker and group the file gives it
            if app["label"] not in models:
                return f"model answerer {app['label']} is not a model answerer of {CONFIG_FILE.name}"
            if any(app[key] != models[app["label"]][key] for key in ("name", "maker", "attribution", "group")):
                return f"model answerer {app['label']} does not carry the name, maker and group {CONFIG_FILE.name} gives it"
            continue
        if app["label"] not in known:
            return f"chat app {app['label']} is not in {CONFIG_FILE.name}"
        if any(app[key] != known[app["label"]][key] for key in ("name", "maker", "attribution", "group")):
            return f"chat app {app['label']} does not carry the name, maker, attribution and group {CONFIG_FILE.name} gives it"
    for group in block["groups"]:
        if group not in config["groups"]:
            return f"group {group['id']} is not a group of {CONFIG_FILE.name}, under the name that file gives it"
    if block["human"] is not None and block["human"] != config["human"]:
        return f"the human column does not carry the name and meta line {CONFIG_FILE.name} gives it"
    for question in block["questions"]:
        if config["prompts"].get(question["id"]) != question:
            return f"chat-app question {question['id']} is not the question {CONFIG_FILE.name} holds under that id"
    return closed_problem(block)


# ---- The chat-app track on its own: the public site's archive.json and the archive-only export --------------
# Owner decision: the public site is the chat-app comparison only. What is published there (and what
# `snapshot --export --archive-only` writes) is this payload: a schema marker and the `chatbots` block.
# No model answer, answerer, model question list, set hash or model run is in it.

SITE_SCHEMA = "live-genesis-chat-archive/2"   # /2: the block holds `groups` and every app its `group`
# /3: the block also holds a model answerer (`kind` on its app entry, `models` on its runs, answers of source `asked`).
# Written exactly when it does: a site without one is still /2, byte for byte.
SITE_SCHEMA_ASKED = "live-genesis-chat-archive/3"
# /4: a question of the block carries `list` (a trending question), with or without a model answerer. Written exactly
# when one does: a site whose questions are all on the main list is still /2 or /3, byte for byte.
SITE_SCHEMA_LISTS = "live-genesis-chat-archive/4"
SITE_SCHEMAS = (SITE_SCHEMA, SITE_SCHEMA_ASKED, SITE_SCHEMA_LISTS)
SITE_KEYS = ("schema", "chatbots")


def site_schema(block: Any) -> str:
    """The schema marker of a chat-app-only payload that holds this block."""
    return SITE_SCHEMA_LISTS if has_lists(block) else SITE_SCHEMA_ASKED if has_models(block) else SITE_SCHEMA


def site_payload(payload: dict) -> dict:
    """A public payload (archive.public_payload, or the archive.json of a full export) reduced to the
    chat-app track. A payload that is already reduced is returned as it is. With no chat-app run the
    block is empty: the pages then say that no chat app has been asked yet."""
    if payload.get("schema") in SITE_SCHEMAS:
        return payload
    empty = {"groups": [], "apps": [], "human": None, "questions": [], "runs": [], "answers": [], "human_answers": []}
    block = payload.get("chatbots") or empty
    return {"schema": site_schema(block), "chatbots": block}


def site_problem(data: Any) -> str | None:
    """Why `data` is not a well-formed chat-app-only payload, or None: the schema marker, exactly the
    two keys, and a well-formed block. What may be PUBLISHED from it is `export_problem` on the block."""
    if not isinstance(data, dict):
        return "top level is not an object"
    if data.get("schema") not in SITE_SCHEMAS:
        return "unknown schema"
    problem = archive._keys_problem("top level", data, SITE_KEYS) or block_problem(data["chatbots"])
    if problem is None and data["schema"] != site_schema(data["chatbots"]):
        return ("the schema marker does not fit the file (/4 exactly when a question names a list, else /3 exactly when it "
                "holds a model answerer)")
    return problem


def status(cx) -> dict:
    """For `archive-status`: the configuration's state and the hand-entered runs (numbers and counts only)."""
    try:
        config = load_config()
        state: dict = {"status": config["status"], "groups": [g["id"] for g in config["groups"]],
                       "chatbots": [c["label"] for c in config["chatbots"]],
                       "models": [{"label": m["label"], "answerer": m["answerer"]} for m in config["models"]],
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

COMMANDS = ("archive-chatbot-template", "archive-chatbot-import", "archive-chatbot-ask")
IMPORT_NOTE = ("Nothing is public yet. The replies are in your own database only. To publish them you export, read every answer "
               f"and approve: {GUIDE} walks through it.")


def summary_lines(done: dict) -> list[str]:
    """The import summary in plain words, one line per app and one for the human block: counts and categories only,
    never the text of a reply."""
    lines = []

    def held(rows: list[dict]) -> str:
        reasons = sorted({row["reason"] for row in rows if row["state"] == "withheld"})
        return f" ({', '.join(reasons)})" if reasons else ""
    for app in done["chatbots"]:
        rows, counts = app["rows"], app["answers"]
        empty = sum(1 for row in rows if row["state"] == "no_answer" and not row["refused"]) - app["skipped"]
        parts = [f"{app['shown']} of {len(rows)} answers will show", f"{app['refused']} refused", f"{app['skipped']} skipped"]
        if counts["withheld"]:
            parts.append(f"{counts['withheld']} held back by the content check{held(rows)}")
        if empty:
            parts.append(f"{empty} empty or too long")
        lines.append(f"  {app['chatbot']}: " + ", ".join(parts))
    if "human" in done:
        rows, counts = done["human"]["rows"], done["human"]["answers"]
        parts = [f"{counts['answered']} of {len(rows)} answers will show"]
        if counts["withheld"]:
            parts.append(f"{counts['withheld']} held back by the content check{held(rows)}")
        if counts["no_answer"]:
            parts.append(f"{counts['no_answer']} skipped, empty or too long")
        lines.append("  human: " + ", ".join(parts))
    else:
        lines.append("  human: no human block in this file")
    return lines


def add_parsers(sub) -> None:
    template_p = sub.add_parser("archive-chatbot-template", help="Answers Over Time, chat apps: write the file the owner fills in BY HAND (each app, each question's exact prompt, empty answers, the optional human block). Free; no model, no network, no database")
    template_p.add_argument("--out", metavar="FILE", default=None, help="Where to write it (default: chatbots-<date>.json here); never overwrites")
    template_p.add_argument("--date", metavar="YYYY-MM-DD", default=None, help="The day you ask the apps (default: today)")
    import_p = sub.add_parser("archive-chatbot-import", help="Answers Over Time, chat apps: check a filled-in file and store it as one hand-entered run (audited). Free; no model, no network, no key")
    import_p.add_argument("file", metavar="FILE")
    import_p.add_argument("--dry-run", action="store_true", help="Check everything and print what would be stored; store nothing")
    import_p.add_argument("--allow-old", action="store_true", help=f"Accept a collected_on more than {MAX_AGE_DAYS} days ago")
    ask_p = sub.add_parser("archive-chatbot-ask", help="PAID: Answers Over Time, chat-app questions: ask every MODEL answerer of archive_chatbots.json (kind \"model\") each question once (or only some: --list, --questions) and store the replies as one run dated today. Needs the owner's explicit decision, the paid opt-in and --max-usd. With --dry-run: free, prints the plan and calls nothing")
    ask_p.add_argument("--max-usd", type=float, default=None, help="Required for a run: hard cap on this run's estimated USD; the worst case must fit under it before the first call")
    ask_p.add_argument("--dry-run", action="store_true", help="Print the plan (models, questions, calls, worst-case estimate); call nothing, store nothing")
    ask_p.add_argument("--models", help="Comma-separated labels of model answerers in archive_chatbots.json (default: all)")
    ask_p.add_argument("--list", dest="question_list", metavar="LIST", default=None, help="Ask only the questions of one list: trending (the questions marked \"list\": \"trending\" in archive_chatbots.json) or main (default: every question)")
    ask_p.add_argument("--questions", metavar="IDS", default=None, help="Ask only these questions: comma-separated ids such as C011,C012 (default: every question; not together with --list)")
    ask_p.add_argument("--pause", type=float, default=0.0, help="Seconds to wait between calls (an endpoint's own _PAUSE_SECONDS applies when it is longer)")


def handle_command(args) -> None:
    """Run one of COMMANDS; ArchiveRefused is left to the caller. Prints counts and lengths, never a reply."""
    if args.command == "archive-chatbot-template":
        target = write_template(args.out, args.date)
        print(f"Template written to {target}. This is your sheet for the month. Nothing was contacted and nothing was stored.")
        print("Fill it in BY HAND: open each chat app yourself, a new chat for every question, paste the prompt exactly, "
              "copy the whole reply. Never use a program to collect replies. The notes at the top of the sheet say how; "
              f"{GUIDE} has every step.")
        print(f"When it is filled in, check it (this stores nothing): python -m live_genesis.cli archive-chatbot-import {target} --dry-run")
        return
    if args.command == "archive-chatbot-ask":
        print(render_ask_plan(ask_plan(args.models, questions=args.questions, question_list=args.question_list)))
        if args.dry_run:
            print("DRY RUN: nothing was called and nothing was stored.")
            return
        if args.max_usd is None:
            raise _refused("--max-usd is required and must be a finite positive number. No paid call was made.")
        print("PAID RUN: provider requests follow. A failed call is handled by the rule printed above (\"If a call fails\").")
        settings.load_provider_credentials()   # as for `cli archive-run`: the only moment .env provider keys enter the process
        os.environ["LIVE_COMPAT_JSON_MODE"] = "off"   # replies are plain sentences, not JSON objects
        done = ask(max_usd=args.max_usd, only=args.models, questions=args.questions, question_list=args.question_list,
                   pause_seconds=args.pause)
        print(json.dumps(done, indent=1))
        if done["status"] == "complete_with_gaps":
            print("RUN COMPLETE WITH GAPS:", done["stop_reason"])
        elif done["status"] != "complete":
            print("RUN INCOMPLETE:", done["stop_reason"])
        if done["run"] is None:
            print("No reply was stored.")
        else:
            print(f"Stored as run {done['run']}, collected {done['collected_on']}. Recorded in the operator audit log.")
            if done["first_run"]:
                print(f"This was the first recorded run: from now on the questions in {CONFIG_FILE.name} may only be added to; "
                      "a recorded question never changes.")
        print(f"Estimated cost is {controls.COST_CAVEAT}.")
        print(ASK_NOTE)
        return
    done = import_collection(args.file, dry_run=args.dry_run, allow_old=args.allow_old)
    print(json.dumps(done, indent=1))
    if done["dry_run"]:
        print("DRY RUN: the file passed every check. Nothing was stored.")
        print("\n".join(summary_lines(done)))
        print("If those numbers look right, run the same command again without --dry-run to store it.")
        return
    print(f"Done. Stored as hand-entered run {done['run']}, collected {done['collected_on']}. Recorded in the operator audit log.")
    print("\n".join(summary_lines(done)))
    if done["first_import"]:
        print(f"This was your first import, so the question list is now fixed: you can add questions to {CONFIG_FILE.name}, "
              "but a recorded question never changes.")
    print(IMPORT_NOTE)
