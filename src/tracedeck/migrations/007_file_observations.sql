CREATE TABLE IF NOT EXISTS file_observations (
    id INTEGER PRIMARY KEY,
    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    tool_call_id INTEGER REFERENCES tool_calls(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    access_kind TEXT NOT NULL CHECK (access_kind IN ('observed', 'written')),
    size_bytes INTEGER,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'inferred')),
    source_event_id TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_file_observations_turn ON file_observations(turn_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_file_observations_path ON file_observations(path, access_kind);
