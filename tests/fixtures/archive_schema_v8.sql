-- The chat-app track's tables and rules as the version before question lists made them (21 November 2026, commit
-- 0077bc1): a recorded question has no `list` column, so every question belongs to the one main list. Used by
-- tests/test_archive_lists.py to check that such a database gains the column in place and keeps every row.
-- Frozen: never edit it to match the code.

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

