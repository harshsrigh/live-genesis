-- The chat-app tables and rules as they were on main on 14 November 2026, BEFORE answerer groups: six tables, no
-- `group_id` / `group_name` on archive_chatbot_conditions. Used by tests/test_archive_groups.py to check that a database
-- made by that version upgrades in place and keeps every row. Do not edit.

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
CREATE TRIGGER IF NOT EXISTS archive_chatbot_questions_no_update BEFORE UPDATE ON archive_chatbot_questions
    BEGIN SELECT RAISE(ABORT, 'archive_chatbot_questions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_chatbot_questions_no_delete BEFORE DELETE ON archive_chatbot_questions
    BEGIN SELECT RAISE(ABORT, 'archive_chatbot_questions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_human_collections_no_update BEFORE UPDATE ON archive_human_collections
    BEGIN SELECT RAISE(ABORT, 'archive_human_collections is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_human_collections_no_delete BEFORE DELETE ON archive_human_collections
    BEGIN SELECT RAISE(ABORT, 'archive_human_collections is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_human_answers_no_update BEFORE UPDATE ON archive_human_answers
    BEGIN SELECT RAISE(ABORT, 'archive_human_answers is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_human_answers_no_delete BEFORE DELETE ON archive_human_answers
    BEGIN SELECT RAISE(ABORT, 'archive_human_answers is append-only'); END;

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
