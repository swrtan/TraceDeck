ALTER TABLE sessions ADD COLUMN source TEXT;
ALTER TABLE turns ADD COLUMN source TEXT;

CREATE TABLE IF NOT EXISTS desktop_import_files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    records_seen INTEGER NOT NULL DEFAULT 0,
    turns_imported INTEGER NOT NULL DEFAULT 0,
    unsupported_records INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_desktop_import_files_attempted ON desktop_import_files(attempted_at);
