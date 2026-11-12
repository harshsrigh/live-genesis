-- The Answers Over Time schema as first released (one fixed question set, no trending track).
-- Used by tests/test_archive_trending.py to build a database from before the upgrade. Do not edit.
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
