PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS datasets (
 id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
 source_name TEXT NOT NULL, source_bytes BLOB NOT NULL, results_bytes BLOB,
 results_sha256 TEXT, demo INTEGER NOT NULL CHECK(demo IN (0,1)), imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
 id TEXT PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets(id), subject TEXT NOT NULL,
 question TEXT NOT NULL, choices TEXT NOT NULL, correct_index INTEGER NOT NULL CHECK(correct_index BETWEEN 0 AND 3),
 source TEXT, split TEXT, original_answer INTEGER, original_output TEXT, original_model TEXT,
 content_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS study_config (id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS participants (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, participant_id TEXT NOT NULL REFERENCES participants(id),
 condition TEXT NOT NULL CHECK(condition IN ('baseline','ai')), dataset_id INTEGER NOT NULL REFERENCES datasets(id),
 config TEXT NOT NULL, demo INTEGER NOT NULL, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
 condition_order INTEGER, stage TEXT NOT NULL DEFAULT 'instructions', attention_failures INTEGER NOT NULL DEFAULT 0,
 comment TEXT, UNIQUE(participant_id, condition), UNIQUE(participant_id, condition_order)
);
CREATE TABLE IF NOT EXISTS trials (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), question_id TEXT REFERENCES questions(id),
 position INTEGER NOT NULL, practice INTEGER NOT NULL DEFAULT 0, first_visible_at TEXT,
 selected_index INTEGER CHECK(selected_index BETWEEN 0 AND 3), confidence INTEGER CHECK(confidence BETWEEN 1 AND 5),
 submitted_at TEXT, correct INTEGER, elapsed_ms REAL, visible_ms REAL NOT NULL DEFAULT 0,
 timing_approximate INTEGER NOT NULL DEFAULT 1, UNIQUE(session_id, position), UNIQUE(session_id,question_id)
);
CREATE TABLE IF NOT EXISTS attention (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), selected_index INTEGER,
 passed INTEGER NOT NULL, restart INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id), page_id TEXT NOT NULL,
 kind TEXT NOT NULL, visible_ms REAL NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
 trial_id TEXT NOT NULL REFERENCES trials(id), page_id TEXT NOT NULL, visible_ms REAL NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(trial_id,page_id)
);
CREATE TABLE IF NOT EXISTS ai_calls (
 id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id), slot INTEGER NOT NULL,
 attempt INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, model TEXT NOT NULL,
 prompt_version TEXT NOT NULL, settings TEXT NOT NULL, context TEXT NOT NULL, user_text TEXT,
 started_at TEXT NOT NULL, finished_at TEXT, latency_ms REAL, raw_response TEXT, provider_metadata TEXT,
 answer_index INTEGER, displayed_text TEXT, error TEXT, acknowledged_at TEXT,
 UNIQUE(trial_id,slot,attempt)
);
CREATE INDEX IF NOT EXISTS trials_session ON trials(session_id);
CREATE INDEX IF NOT EXISTS ai_trial ON ai_calls(trial_id);
CREATE TRIGGER IF NOT EXISTS questions_no_update BEFORE UPDATE ON questions BEGIN SELECT RAISE(ABORT,'immutable questions'); END;
CREATE TRIGGER IF NOT EXISTS questions_no_delete BEFORE DELETE ON questions BEGIN SELECT RAISE(ABORT,'immutable questions'); END;
CREATE TRIGGER IF NOT EXISTS dataset_no_update BEFORE UPDATE ON datasets BEGIN SELECT RAISE(ABORT,'immutable dataset'); END;
CREATE TRIGGER IF NOT EXISTS locked_session BEFORE UPDATE OF condition,participant_id,dataset_id,config,demo ON sessions BEGIN SELECT RAISE(ABORT,'immutable session assignment'); END;
