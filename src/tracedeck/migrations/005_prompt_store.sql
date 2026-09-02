CREATE TABLE IF NOT EXISTS prompt_store (
    id INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    compressed_text BLOB NOT NULL,
    original_size_bytes INTEGER NOT NULL,
    compressed_size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

ALTER TABLE turns ADD COLUMN prompt_id INTEGER REFERENCES prompt_store(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_turns_prompt_id ON turns(prompt_id);
