import sqlite3

import pytest

from tracedeck.db import collect_orphaned_content, connect, prune_completed_sessions, read_prompt, read_turn_message, upsert_session, upsert_tool_call, upsert_turn


def test_migration_is_idempotent_and_configures_sqlite(tmp_path):
    path = tmp_path / "trace.db"
    connection = connect(path, max_storage_mb=100)
    connect(path, max_storage_mb=100).close()

    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 7
    connection.close()


def test_text_purge_migration_clears_existing_prompt_and_response(tmp_path):
    connection = connect(tmp_path / "trace.db")
    session_id = upsert_session(connection, "privacy-session", created_at="t", updated_at="t")
    turn_id = upsert_turn(connection, session_id, "privacy-turn", created_at="t", updated_at="t")
    connection.execute("UPDATE turns SET user_prompt=?, assistant_response=? WHERE id=?", ("secret prompt", "secret response", turn_id))
    connection.execute("DELETE FROM schema_migrations WHERE version=4")
    from tracedeck.db import migrate
    migrate(connection)
    assert tuple(connection.execute("SELECT user_prompt, assistant_response FROM turns WHERE id=?", (turn_id,)).fetchone()) == (None, None)
    connection.close()


def test_upserts_preserve_ids_and_nullable_usage(tmp_path):
    connection = connect(tmp_path / "trace.db")
    session_id = upsert_session(connection, "session-1", model_initial="gpt", created_at="t", updated_at="t")
    same_session_id = upsert_session(connection, "session-1", model_last="gpt", created_at="t", updated_at="t")
    turn_id = upsert_turn(connection, session_id, "turn-1", model="gpt", created_at="t", updated_at="t")
    same_turn_id = upsert_turn(connection, session_id, "turn-1", lifecycle_status="completed", created_at="t", updated_at="t")

    assert session_id == same_session_id
    assert turn_id == same_turn_id
    row = connection.execute("SELECT input_tokens, total_tokens, lifecycle_status FROM turns WHERE id=?", (turn_id,)).fetchone()
    assert tuple(row) == (None, None, "completed")
    connection.close()


def test_repeated_prompts_are_compressed_and_deduplicated(tmp_path):
    connection = connect(tmp_path / "trace.db")
    session_id = upsert_session(connection, "prompt-session", created_at="t", updated_at="t")
    prompt = "Please summarize this report and list the three most important findings."
    upsert_turn(connection, session_id, "turn-1", user_prompt=prompt, created_at="t", updated_at="t")
    upsert_turn(connection, session_id, "turn-2", user_prompt=prompt, created_at="t", updated_at="t")

    ids = [row[0] for row in connection.execute("SELECT prompt_id FROM turns ORDER BY id")]
    assert ids[0] == ids[1]
    assert read_prompt(connection, ids[0]) == prompt
    stored = connection.execute("SELECT COUNT(*), compressed_size_bytes FROM prompt_store").fetchone()
    assert tuple(stored) == (1, stored[1])
    assert stored[1] > 0
    connection.close()


def test_messages_deduplicate_prompt_and_assistant_content(tmp_path):
    connection = connect(tmp_path / "trace.db")
    session_id = upsert_session(connection, "message-session", created_at="t", updated_at="t")
    turn_id = upsert_turn(connection, session_id, "message-turn", user_prompt="hello", assistant_response="world", created_at="t", updated_at="t")
    assert read_prompt(connection, connection.execute("SELECT prompt_id FROM turns WHERE id=?", (turn_id,)).fetchone()[0]) == "hello"
    assert read_turn_message(connection, turn_id, "assistant") == "world"
    assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM turn_messages").fetchone()[0] == 2
    connection.close()


def test_foreign_keys_are_enforced(tmp_path):
    connection = connect(tmp_path / "trace.db")
    with pytest.raises(sqlite3.IntegrityError):
        upsert_tool_call(connection, 999, "call-1", "shell")
    connection.close()


def test_pruning_removes_oldest_completed_sessions_only(tmp_path):
    connection = connect(tmp_path / "trace.db")
    old_id = upsert_session(connection, "old", ended_at="2025-01-01", lifecycle_status="completed")
    upsert_session(connection, "active", lifecycle_status="in_progress")
    before = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    removed = prune_completed_sessions(connection, 1)

    assert removed == 1
    assert before == 2
    assert connection.execute("SELECT COUNT(*) FROM sessions WHERE id=?", (old_id,)).fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM sessions WHERE codex_session_id='active'").fetchone()[0] == 1
    maintenance = connection.execute("SELECT kind, details_json FROM maintenance_events ORDER BY id DESC LIMIT 1").fetchone()
    assert maintenance[0] == "prune"
    assert '"removed_sessions": 1' in maintenance[1]
    connection.close()


def test_orphaned_compressed_content_is_collectable(tmp_path):
    connection = connect(tmp_path / "trace.db")
    session_id = upsert_session(connection, "content-session", created_at="t", updated_at="t")
    turn_id = upsert_turn(connection, session_id, "content-turn", user_prompt="keep me", created_at="t", updated_at="t")
    orphan_id = connection.execute("INSERT INTO prompt_store(content_hash, compressed_text, original_size_bytes, compressed_size_bytes, created_at, last_seen_at, content_kind) VALUES ('orphan', X'78', 1, 1, 't', 't', 'prompt') RETURNING id").fetchone()[0]
    connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    assert collect_orphaned_content(connection) == 2
    assert connection.execute("SELECT 1 FROM prompt_store WHERE id=?", (orphan_id,)).fetchone() is None
    assert connection.execute("SELECT COUNT(*) FROM prompt_store").fetchone()[0] == 0
    connection.close()
