-- Run once, as the project owner, inside an explicit transaction.
-- This schema is intentionally not in Supabase's default exposed public schema.
CREATE SCHEMA study;
SET LOCAL search_path = study, pg_catalog;
REVOKE ALL ON SCHEMA study FROM PUBLIC;
CREATE TABLE deployment_metadata (
 id INTEGER PRIMARY KEY CHECK (id=1),
 environment TEXT NOT NULL CHECK (environment IN ('local','preview','production')),
 database_id TEXT NOT NULL UNIQUE,
 schema_version INTEGER NOT NULL CHECK (schema_version=1)
);
CREATE TABLE datasets (
 id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
 source_name TEXT NOT NULL, source_bytes BYTEA NOT NULL, results_bytes BYTEA,
 results_sha256 TEXT, demo INTEGER NOT NULL CHECK(demo IN (0,1)), imported_at TEXT NOT NULL
);
CREATE TABLE questions (
 id TEXT PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets(id), subject TEXT NOT NULL,
 question TEXT NOT NULL, choices TEXT NOT NULL, correct_index INTEGER NOT NULL CHECK(correct_index BETWEEN 0 AND 3),
 source TEXT, split TEXT, original_answer INTEGER, original_output TEXT, original_model TEXT,
 content_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE study_config (id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL);
CREATE TABLE participants (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE sessions (
 id TEXT PRIMARY KEY, participant_id TEXT NOT NULL REFERENCES participants(id),
 condition TEXT NOT NULL CHECK(condition IN ('baseline','ai')), dataset_id INTEGER NOT NULL REFERENCES datasets(id),
 config TEXT NOT NULL, demo INTEGER NOT NULL, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
 condition_order INTEGER, stage TEXT NOT NULL DEFAULT 'instructions', attention_failures INTEGER NOT NULL DEFAULT 0,
 comment TEXT, UNIQUE(participant_id, condition), UNIQUE(participant_id, condition_order)
);
CREATE TABLE trials (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), question_id TEXT REFERENCES questions(id),
 position INTEGER NOT NULL, practice INTEGER NOT NULL DEFAULT 0, first_visible_at TEXT,
 selected_index INTEGER CHECK(selected_index BETWEEN 0 AND 3), confidence INTEGER CHECK(confidence BETWEEN 1 AND 5),
 submitted_at TEXT, correct INTEGER, elapsed_ms DOUBLE PRECISION, visible_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
 timing_approximate INTEGER NOT NULL DEFAULT 1, UNIQUE(session_id, position), UNIQUE(session_id,question_id)
);
CREATE TABLE attention (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), selected_index INTEGER,
 passed INTEGER NOT NULL, restart INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE events (
 id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id), page_id TEXT NOT NULL,
 kind TEXT NOT NULL, visible_ms DOUBLE PRECISION NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE checkpoints (
 trial_id TEXT NOT NULL REFERENCES trials(id), page_id TEXT NOT NULL, visible_ms DOUBLE PRECISION NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(trial_id,page_id)
);
CREATE TABLE ai_calls (
 id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id), slot INTEGER NOT NULL,
 attempt INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, model TEXT NOT NULL,
 prompt_version TEXT NOT NULL, settings TEXT NOT NULL, context TEXT NOT NULL, user_text TEXT,
 started_at TEXT NOT NULL, finished_at TEXT, latency_ms DOUBLE PRECISION, raw_response TEXT, provider_metadata TEXT,
 answer_index INTEGER, displayed_text TEXT, error TEXT, acknowledged_at TEXT,
 UNIQUE(trial_id,slot,attempt)
);
CREATE INDEX trials_session ON trials(session_id);
CREATE INDEX ai_trial ON ai_calls(trial_id);

CREATE FUNCTION reject_immutable_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
 RAISE EXCEPTION 'immutable study record' USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER questions_immutable BEFORE UPDATE OR DELETE ON questions
 FOR EACH ROW EXECUTE FUNCTION reject_immutable_change();
CREATE TRIGGER datasets_immutable BEFORE UPDATE OR DELETE ON datasets
 FOR EACH ROW EXECUTE FUNCTION reject_immutable_change();
CREATE TRIGGER deployment_immutable BEFORE UPDATE OR DELETE ON deployment_metadata
 FOR EACH ROW EXECUTE FUNCTION reject_immutable_change();
CREATE TRIGGER session_assignment_immutable
 BEFORE UPDATE OF condition,participant_id,dataset_id,config,demo ON sessions
 FOR EACH ROW EXECUTE FUNCTION reject_immutable_change();
REVOKE ALL ON ALL TABLES IN SCHEMA study FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA study FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA study FROM PUBLIC;
