-- The chat-app tables and rules in their FIRST shape (14 November 2026, before the owner's decisions of that day):
-- three tables, answers tied to a locked question of the core or trending list, no `refused` column, no question table,
-- no human tables. Used by tests/test_archive_chatbots.py to check the in-place migration. Do not edit.

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
