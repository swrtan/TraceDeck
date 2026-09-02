CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    codex_session_id TEXT NOT NULL UNIQUE,
    transcript_path TEXT,
    cwd_initial TEXT,
    cwd_last TEXT,
    model_initial TEXT,
    model_last TEXT,
    start_source TEXT,
    started_at TEXT,
    ended_at TEXT,
    timestamp_source TEXT NOT NULL DEFAULT 'observer',
    lifecycle_status TEXT NOT NULL DEFAULT 'in_progress',
    codex_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    codex_turn_id TEXT,
    user_prompt TEXT,
    assistant_response TEXT,
    model TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_ms INTEGER,
    duration_source TEXT,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    cache_write_input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_output_tokens INTEGER,
    total_tokens INTEGER,
    usage_source TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'unknown',
    error_message TEXT,
    prompt_redacted INTEGER NOT NULL DEFAULT 0,
    prompt_truncated INTEGER NOT NULL DEFAULT 0,
    response_redacted INTEGER NOT NULL DEFAULT 0,
    response_truncated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, codex_turn_id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    call_id TEXT,
    tool_name TEXT NOT NULL,
    arguments_json TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_ms INTEGER,
    duration_source TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'unknown',
    error_message TEXT,
    arguments_redacted INTEGER NOT NULL DEFAULT 0,
    arguments_truncated INTEGER NOT NULL DEFAULT 0,
    pre_observed INTEGER NOT NULL DEFAULT 0,
    post_observed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(turn_id, call_id)
);

CREATE TABLE IF NOT EXISTS maintenance_events (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_turns_session_started ON turns(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_turns_status ON turns(lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_tool_calls_turn_started ON tool_calls(turn_id, started_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_events_occurred ON maintenance_events(occurred_at);

