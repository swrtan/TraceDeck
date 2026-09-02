"""SQLite connection, migrations, and small domain repositories."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PAGE_SIZE = 4096
BUSY_TIMEOUT_MS = 5000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def store_prompt(connection: sqlite3.Connection, prompt: str, content_kind: str = "prompt") -> int:
    encoded = prompt.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    compressed = zlib.compress(encoded, level=9)
    now = _now()
    connection.execute(
        "INSERT INTO prompt_store(content_hash, compressed_text, original_size_bytes, compressed_size_bytes, created_at, last_seen_at, content_kind) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(content_hash) DO UPDATE SET last_seen_at=excluded.last_seen_at",
        (digest, compressed, len(encoded), len(compressed), now, now, content_kind),
    )
    return int(connection.execute("SELECT id FROM prompt_store WHERE content_hash=?", (digest,)).fetchone()[0])


def read_prompt(connection: sqlite3.Connection, prompt_id: int | None) -> str | None:
    if prompt_id is None:
        return None
    row = connection.execute("SELECT compressed_text FROM prompt_store WHERE id=?", (prompt_id,)).fetchone()
    if row is None:
        return None
    try:
        value = zlib.decompress(row[0]).decode("utf-8")
        from .prompt_policy import prompt_for_storage
        return prompt_for_storage(value)
    except (zlib.error, UnicodeDecodeError):
        return None


def store_message(connection: sqlite3.Connection, session_id: int, turn_id: int, role: str, content: str, position: int) -> int:
    content_id = store_prompt(connection, content, role)
    preview = content[:240]
    now = _now()
    connection.execute(
        "INSERT INTO messages(session_id, role, content_id, preview, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id, role, content_id) DO UPDATE SET preview=excluded.preview",
        (session_id, role, content_id, preview, now),
    )
    message_id = int(connection.execute("SELECT id FROM messages WHERE session_id=? AND role=? AND content_id=?", (session_id, role, content_id)).fetchone()[0])
    connection.execute("INSERT OR IGNORE INTO turn_messages(turn_id, message_id, position) VALUES (?, ?, ?)", (turn_id, message_id, position))
    return message_id


def read_turn_message(connection: sqlite3.Connection, turn_id: int, role: str) -> str | None:
    row = connection.execute(
        "SELECT p.id FROM turn_messages tm JOIN messages m ON m.id=tm.message_id JOIN prompt_store p ON p.id=m.content_id WHERE tm.turn_id=? AND m.role=? ORDER BY tm.position LIMIT 1",
        (turn_id, role),
    ).fetchone()
    return read_prompt(connection, row[0]) if row else None


def connect(path: Path, max_storage_mb: int = 1024) -> sqlite3.Connection:
    """Open a configured local database and apply migrations transactionally."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The FastAPI app may serve requests from worker threads; the collector
    # remains the single SQLite writer and all statements stay parameterized.
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
    connection.execute(f"PRAGMA max_page_count = {(max_storage_mb * 1024 * 1024) // PAGE_SIZE}")
    migrate(connection)
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    """Apply bundled migrations in ascending order, once each."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        migration_dir = Path(__file__).parent / "migrations"
        for migration_path in sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(migration_path.name[:3])
            if version in applied:
                continue
            for statement in migration_path.read_text(encoding="utf-8").split(";"):
                statement = statement.strip()
                if statement:
                    connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, _now()))
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _update(table: str, key: str, key_value: Any, values: dict[str, Any], connection: sqlite3.Connection) -> int:
    columns = [key, *values.keys()]
    placeholders = ", ".join("?" for _ in columns)
    assignments = ", ".join(f"{column}=excluded.{column}" for column in values)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT({key}) DO UPDATE SET {assignments}"
    connection.execute(sql, (key_value, *values.values()))
    return int(connection.execute(f"SELECT id FROM {table} WHERE {key} = ?", (key_value,)).fetchone()[0])


def upsert_session(connection: sqlite3.Connection, codex_session_id: str, **values: Any) -> int:
    values.setdefault("timestamp_source", "observer")
    values.setdefault("lifecycle_status", "in_progress")
    values.setdefault("created_at", _now())
    values["updated_at"] = values.get("updated_at", _now())
    return _update("sessions", "codex_session_id", codex_session_id, values, connection)


def upsert_turn(connection: sqlite3.Connection, session_id: int, codex_turn_id: str | None, **values: Any) -> int:
    now = _now()
    # Human prompts are retained after the normalizer's bounded redaction;
    # assistant content remains excluded from the local MVP.
    prompt = values.pop("user_prompt", None)
    response = values.pop("assistant_response", None)
    if prompt is not None:
        values["prompt_id"] = store_prompt(connection, str(prompt), "user")
    values["assistant_response"] = None
    values.setdefault("lifecycle_status", "unknown")
    values.setdefault("created_at", now)
    values["updated_at"] = values.get("updated_at", now)
    if codex_turn_id is None:
        columns = ["session_id", "codex_turn_id", *values.keys()]
        cursor = connection.execute(f"INSERT INTO turns ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", (session_id, None, *values.values()))
        turn_pk = int(cursor.lastrowid)
    else:
        sql = f"INSERT INTO turns (session_id, codex_turn_id, {', '.join(values)}) VALUES (?, ?, {', '.join('?' for _ in values)}) ON CONFLICT(session_id, codex_turn_id) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in values)}"
        connection.execute(sql, (session_id, codex_turn_id, *values.values()))
        turn_pk = int(connection.execute("SELECT id FROM turns WHERE session_id=? AND codex_turn_id=?", (session_id, codex_turn_id)).fetchone()[0])
    if prompt is not None:
        store_message(connection, session_id, turn_pk, "user", str(prompt), 0)
    if response is not None:
        store_message(connection, session_id, turn_pk, "assistant", str(response), 1)
    return turn_pk


def upsert_tool_call(connection: sqlite3.Connection, turn_id: int, call_id: str | None, tool_name: str, **values: Any) -> int:
    now = _now()
    values.setdefault("lifecycle_status", "unknown")
    values.setdefault("created_at", now)
    values["updated_at"] = values.get("updated_at", now)
    if call_id is None:
        columns = ["turn_id", "call_id", "tool_name", *values.keys()]
        cursor = connection.execute(f"INSERT INTO tool_calls ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", (turn_id, None, tool_name, *values.values()))
        return int(cursor.lastrowid)
    columns = ["turn_id", "call_id", "tool_name", *values.keys()]
    sql = f"INSERT INTO tool_calls ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) ON CONFLICT(turn_id, call_id) DO UPDATE SET tool_name=excluded.tool_name, {', '.join(f'{k}=excluded.{k}' for k in values)}"
    connection.execute(sql, (turn_id, call_id, tool_name, *values.values()))
    return int(connection.execute("SELECT id FROM tool_calls WHERE turn_id=? AND call_id=?", (turn_id, call_id)).fetchone()[0])


def record_file_observation(connection: sqlite3.Connection, turn_id: int, tool_call_id: int | None, path: str, access_kind: str, size_bytes: int | None, observed_at: str, source: str, confidence: str, source_event_id: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO file_observations(turn_id, tool_call_id, path, access_kind, size_bytes, observed_at, source, confidence, source_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (turn_id, tool_call_id, path, access_kind, size_bytes, observed_at, source, confidence, source_event_id),
    )


def record_maintenance(connection: sqlite3.Connection, kind: str, details: dict[str, Any] | None = None) -> int:
    cursor = connection.execute("INSERT INTO maintenance_events(kind, occurred_at, details_json) VALUES (?, ?, ?)", (kind, _now(), json.dumps(details) if details is not None else None))
    return int(cursor.lastrowid)


def database_size_bytes(connection: sqlite3.Connection) -> int:
    """Return SQLite's current allocated database size, including the WAL."""

    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    try:
        wal_frames = int(connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[1])
    except sqlite3.OperationalError:
        # A concurrent reader may briefly hold SQLite's checkpoint lock. The
        # health endpoint must remain available; the base DB size is safe.
        wal_frames = 0
    return page_count * page_size + wal_frames * page_size


def collect_orphaned_content(connection: sqlite3.Connection) -> int:
    """Remove compressed content that no live turn or message references."""

    cursor = connection.execute(
        """DELETE FROM prompt_store
           WHERE id NOT IN (SELECT prompt_id FROM turns WHERE prompt_id IS NOT NULL)
             AND id NOT IN (SELECT content_id FROM messages)"""
    )
    return cursor.rowcount


def prune_completed_sessions(connection: sqlite3.Connection, target_bytes: int) -> int:
    """Delete oldest completed sessions until allocated size is below target."""

    if target_bytes < 1:
        raise ValueError("target_bytes must be positive")
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    removed = 0
    removed_ranges: list[str] = []
    while database_size_bytes(connection) > target_bytes:
        row = connection.execute(
            "SELECT id, ended_at FROM sessions WHERE lifecycle_status='completed' AND ended_at IS NOT NULL ORDER BY ended_at ASC, id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            break
        connection.execute("DELETE FROM sessions WHERE id=?", (row[0],))
        removed += 1
        removed_ranges.append(row[1])
        collect_orphaned_content(connection)
        connection.execute("PRAGMA incremental_vacuum(1000)")
    if removed:
        record_maintenance(connection, "prune", {
            "removed_sessions": removed,
            "oldest_ended_at": min(removed_ranges),
            "newest_ended_at": max(removed_ranges),
            "reason": "storage_high_water_mark",
            "target_bytes": target_bytes,
        })
    return removed
