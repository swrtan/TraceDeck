ALTER TABLE prompt_store ADD COLUMN content_kind TEXT NOT NULL DEFAULT 'prompt';

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content_id INTEGER NOT NULL REFERENCES prompt_store(id) ON DELETE RESTRICT,
    preview TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, role, content_id)
);

CREATE TABLE IF NOT EXISTS turn_messages (
    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY(turn_id, message_id),
    UNIQUE(turn_id, position)
);

CREATE INDEX IF NOT EXISTS idx_messages_session_role ON messages(session_id, role);
CREATE INDEX IF NOT EXISTS idx_turn_messages_message ON turn_messages(message_id);
