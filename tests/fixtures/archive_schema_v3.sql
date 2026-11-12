-- The archive tables and rules as they were before repeat samples (named endpoints; one answer row per run, question
-- and answerer). Used by tests/test_archive_samples.py to check the in-place migration. Do not edit.

CREATE TABLE IF NOT EXISTS archive_sets (
    question_set TEXT PRIMARY KEY, sha256 TEXT NOT NULL, questions INTEGER NOT NULL, locked_at TEXT NOT NULL,
    track TEXT NOT NULL DEFAULT 'core' CHECK(track IN ('core','trending'))
);
-- One row per lock. The core set has exactly one (version 1, the row in archive_sets). The trending
-- set gains a version each time new questions are locked; `questions` is the total after it.
CREATE TABLE IF NOT EXISTS archive_set_versions (
    question_set TEXT NOT NULL REFERENCES archive_sets(question_set), version INTEGER NOT NULL CHECK(version>=1),
    sha256 TEXT NOT NULL, questions INTEGER NOT NULL CHECK(questions>=1), locked_at TEXT NOT NULL,
    PRIMARY KEY (question_set, version)
);
CREATE TABLE IF NOT EXISTS archive_questions (
    question_set TEXT NOT NULL REFERENCES archive_sets(question_set), id TEXT NOT NULL,
    position INTEGER NOT NULL, group_name TEXT NOT NULL, group_why TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL, rationale TEXT NOT NULL, prompt TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, added TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
    trend_source TEXT NOT NULL DEFAULT '',
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
-- What a run covered: per track, the set version and the number of questions asked (positions
-- 1..questions). `archive_runs.question_set` / `set_sha256` name the first of them. A run from
-- before this table existed has no row here and is read as covering the whole core set.
CREATE TABLE IF NOT EXISTS archive_run_sets (
    run_id INTEGER NOT NULL REFERENCES archive_runs(id), track TEXT NOT NULL CHECK(track IN ('core','trending')),
    question_set TEXT NOT NULL, version INTEGER NOT NULL, sha256 TEXT NOT NULL, questions INTEGER NOT NULL CHECK(questions>=1),
    PRIMARY KEY (run_id, track)
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
    -- Server calls only (NULL otherwise, and NULL on rows from before these columns): the endpoint NAME (never its
    -- URL), what the provider's response said about itself, and how the call was made and priced.
    endpoint TEXT, reported_model TEXT, system_fingerprint TEXT, finish_reason TEXT, latency_ms INTEGER,
    sampling TEXT, model_digest TEXT, pricing_basis TEXT, local INTEGER,
    UNIQUE (run_id, question_id, answerer),
    CHECK ((state='answered') = (answer_text IS NOT NULL)),
    CHECK ((source='engine') = (reservation_id IS NOT NULL) OR source='mock'),
    CHECK ((source='api') = (agent_id IS NOT NULL))
);
-- One row per answerer of a run made by `archive-run`, written when the run closes: how far that model got.
-- `skipped`: a call failed on a zero-priced endpoint and the run went on without this model. `stopped`: the run
-- ended while this model was being asked. `reason` is a category (SKIP_REASONS), never provider text.
CREATE TABLE IF NOT EXISTS archive_run_answerers (
    run_id INTEGER NOT NULL REFERENCES archive_runs(id), answerer TEXT NOT NULL,
    endpoint TEXT, provider TEXT, model TEXT NOT NULL,
    pricing_basis TEXT CHECK(pricing_basis IN ('rates','local','declared-free')),
    outcome TEXT NOT NULL CHECK(outcome IN ('complete','skipped','stopped','not_asked')),
    reason TEXT NOT NULL DEFAULT '' CHECK(reason IN ('','rate_limited','not_found','auth','timeout','server_error','no_usage','not_ready','other')),
    recorded INTEGER NOT NULL DEFAULT 0, reservation_id INTEGER REFERENCES budget_reservations(id), model_digest TEXT,
    PRIMARY KEY (run_id, answerer)
);

CREATE INDEX IF NOT EXISTS archive_answers_series ON archive_answers(answerer, question_id, run_id);
CREATE UNIQUE INDEX IF NOT EXISTS archive_sets_one_per_track ON archive_sets(track);
CREATE UNIQUE INDEX IF NOT EXISTS archive_questions_position ON archive_questions(question_set, position);
CREATE TRIGGER IF NOT EXISTS archive_sets_no_update BEFORE UPDATE ON archive_sets
    BEGIN SELECT RAISE(ABORT, 'archive_sets is append-only: a locked question set never changes'); END;
CREATE TRIGGER IF NOT EXISTS archive_sets_no_delete BEFORE DELETE ON archive_sets
    BEGIN SELECT RAISE(ABORT, 'archive_sets is append-only: a locked question set never changes'); END;
CREATE TRIGGER IF NOT EXISTS archive_set_versions_no_update BEFORE UPDATE ON archive_set_versions
    BEGIN SELECT RAISE(ABORT, 'archive_set_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_set_versions_no_delete BEFORE DELETE ON archive_set_versions
    BEGIN SELECT RAISE(ABORT, 'archive_set_versions is append-only'); END;
-- Version 1 is the lock in archive_sets itself. Only the trending set may have a later version,
-- numbered without a gap and holding more questions than the one before: it can only grow.
CREATE TRIGGER IF NOT EXISTS archive_set_versions_chain BEFORE INSERT ON archive_set_versions
    WHEN NEW.version IS NOT (SELECT COALESCE(MAX(version),0)+1 FROM archive_set_versions WHERE question_set=NEW.question_set)
      OR (NEW.version=1 AND NOT EXISTS (SELECT 1 FROM archive_sets WHERE question_set=NEW.question_set
                                        AND sha256=NEW.sha256 AND questions=NEW.questions))
      OR (NEW.version>1 AND ((SELECT track FROM archive_sets WHERE question_set=NEW.question_set) IS NOT 'trending'
           OR NEW.questions<=(SELECT questions FROM archive_set_versions WHERE question_set=NEW.question_set AND version=NEW.version-1)))
    BEGIN SELECT RAISE(ABORT, 'archive_set_versions: a set is locked once; only the trending set grows, and only by appending'); END;
CREATE TRIGGER IF NOT EXISTS archive_questions_no_update BEFORE UPDATE ON archive_questions
    BEGIN SELECT RAISE(ABORT, 'archive_questions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_questions_no_delete BEFORE DELETE ON archive_questions
    BEGIN SELECT RAISE(ABORT, 'archive_questions is append-only'); END;
-- A question row belongs to one recorded lock and sits in the positions that lock added, so
-- nothing can be slipped into the core set later or placed before an earlier trending question.
CREATE TRIGGER IF NOT EXISTS archive_questions_in_version BEFORE INSERT ON archive_questions
    WHEN NOT EXISTS (SELECT 1 FROM archive_set_versions v WHERE v.question_set=NEW.question_set AND v.version=NEW.version
                     AND NEW.position<=v.questions AND NEW.position>COALESCE(
                         (SELECT p.questions FROM archive_set_versions p WHERE p.question_set=v.question_set AND p.version=v.version-1), 0))
    BEGIN SELECT RAISE(ABORT, 'an archive question must belong to a recorded lock of its set'); END;
CREATE TRIGGER IF NOT EXISTS archive_answers_no_update BEFORE UPDATE ON archive_answers
    BEGIN SELECT RAISE(ABORT, 'archive_answers is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_answers_no_delete BEFORE DELETE ON archive_answers
    BEGIN SELECT RAISE(ABORT, 'archive_answers is append-only'); END;
-- demo answers only in demo runs, mock only in mock runs, engine and api only in real runs.
CREATE TRIGGER IF NOT EXISTS archive_answers_kind BEFORE INSERT ON archive_answers
    WHEN (SELECT kind FROM archive_runs WHERE id=NEW.run_id) IS NOT
         CASE NEW.source WHEN 'demo' THEN 'demo' WHEN 'mock' THEN 'mock' ELSE 'real' END
    BEGIN SELECT RAISE(ABORT, 'archive answer source does not match the kind of its run'); END;
-- A real answer is an answer to a locked question.
CREATE TRIGGER IF NOT EXISTS archive_answers_real_question BEFORE INSERT ON archive_answers
    WHEN (SELECT kind FROM archive_runs WHERE id=NEW.run_id)='real' AND NOT EXISTS
         (SELECT 1 FROM archive_questions WHERE question_set=NEW.question_set AND id=NEW.question_id)
    BEGIN SELECT RAISE(ABORT, 'a real archive answer needs a locked question'); END;
-- A real run exists only for a locked question set with exactly a locked hash.
CREATE TRIGGER IF NOT EXISTS archive_runs_real_needs_version BEFORE INSERT ON archive_runs
    WHEN NEW.kind='real' AND NOT EXISTS
         (SELECT 1 FROM archive_set_versions WHERE question_set=NEW.question_set AND sha256=NEW.set_sha256)
    BEGIN SELECT RAISE(ABORT, 'a real archive run needs the locked question set'); END;
CREATE TRIGGER IF NOT EXISTS archive_run_sets_real_needs_version BEFORE INSERT ON archive_run_sets
    WHEN (SELECT kind FROM archive_runs WHERE id=NEW.run_id)='real' AND NOT EXISTS
         (SELECT 1 FROM archive_set_versions v JOIN archive_sets s ON s.question_set=v.question_set
          WHERE v.question_set=NEW.question_set AND v.version=NEW.version AND v.sha256=NEW.sha256
            AND v.questions=NEW.questions AND s.track=NEW.track)
    BEGIN SELECT RAISE(ABORT, 'a real archive run needs the locked question set'); END;
CREATE TRIGGER IF NOT EXISTS archive_run_sets_no_update BEFORE UPDATE ON archive_run_sets
    BEGIN SELECT RAISE(ABORT, 'archive_run_sets is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_run_sets_no_delete BEFORE DELETE ON archive_run_sets
    BEGIN SELECT RAISE(ABORT, 'archive_run_sets is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_run_answerers_no_update BEFORE UPDATE ON archive_run_answerers
    BEGIN SELECT RAISE(ABORT, 'archive_run_answerers is append-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_run_answerers_no_delete BEFORE DELETE ON archive_run_answerers
    BEGIN SELECT RAISE(ABORT, 'archive_run_answerers is append-only'); END;
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
