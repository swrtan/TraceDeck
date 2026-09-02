import json

from tracedeck.db import connect, read_prompt, upsert_session, upsert_turn
from tracedeck.integrations.codex_desktop import import_desktop_sessions


def _write_desktop_fixture(path, session_id="desktop-session", turn_id="desktop-turn"):
    usage = {
        "input_tokens": 11,
        "cached_input_tokens": 7,
        "cache_write_input_tokens": 0,
        "output_tokens": 5,
        "reasoning_output_tokens": 2,
        "total_tokens": 16,
    }
    records = [
        {
            "timestamp": "2026-09-02T08:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "originator": "codex_work_desktop",
                "cli_version": "0.152.1",
                "cwd": "C:/fixture",
            },
        },
        {
            "timestamp": "2026-09-02T08:00:01Z",
            "type": "turn_context",
            "payload": {"turn_id": turn_id, "model": "fixture-model"},
        },
        {
            "timestamp": "2026-09-02T08:00:01Z",
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": turn_id, "started_at": "2026-09-02T08:00:01Z"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "use api_key=secret-value"}],
                "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "done"}],
                "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {"last_token_usage": usage}},
        },
        {
            "timestamp": "2026-09-02T08:00:03Z",
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": turn_id,
                "completed_at": "2026-09-02T08:00:03Z",
                "duration_ms": 2000,
            },
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def test_desktop_fixture_imports_bounded_turns_and_exact_usage(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    path = root / "desktop.jsonl"
    _write_desktop_fixture(path)
    db = connect(tmp_path / "trace.db")

    assert import_desktop_sessions(root, db) == {"files": 1, "turns": 1, "unsupported": 0, "skipped": 0}
    session = db.execute("SELECT codex_session_id, source, codex_version FROM sessions").fetchone()
    assert tuple(session) == ("desktop-session", "transcript:desktop", "0.152.1")
    turn = db.execute("SELECT user_prompt, prompt_id, assistant_response, total_tokens, usage_source, duration_ms, source FROM turns").fetchone()
    assert turn[0] is None
    assert read_prompt(db, turn[1]) == "use api_key=[REDACTED]"
    assert tuple(turn[2:]) == (None, 16, "transcript:desktop:codex-0.152.x", 2000, "transcript:desktop:codex-0.152.x")

    assert import_desktop_sessions(root, db) == {"files": 0, "turns": 0, "unsupported": 0, "skipped": 1}
    assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    db.close()


def test_desktop_import_preserves_hook_values_and_ignores_tool_output(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    path = root / "desktop.jsonl"
    _write_desktop_fixture(path)
    db = connect(tmp_path / "trace.db")
    session_id = upsert_session(db, "desktop-session", source="hook", created_at="t", updated_at="t")
    upsert_turn(db, session_id, "desktop-turn", user_prompt="hook prompt", source="hook", created_at="t", updated_at="t")

    import_desktop_sessions(root, db)
    turn = db.execute("SELECT prompt_id, source, total_tokens FROM turns").fetchone()
    assert read_prompt(db, turn[0]) == "hook prompt"
    assert tuple(turn[1:]) == ("hook", 16)
    assert db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0
    assert db.execute("SELECT details_json FROM maintenance_events").fetchone() is None
    db.close()


def test_malformed_and_partial_desktop_files_do_not_block_other_files(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    (root / "bad.jsonl").write_text('{"type":"unknown"}\n', encoding="utf-8")
    _write_desktop_fixture(root / "good.jsonl", session_id="good", turn_id="good-turn")
    with (root / "partial.jsonl").open("w", encoding="utf-8") as handle:
        handle.write('{"type":"session_meta"')
    db = connect(tmp_path / "trace.db")

    result = import_desktop_sessions(root, db)
    assert result["files"] == 3
    assert result["turns"] == 1
    assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM maintenance_events").fetchone()[0] >= 2
    db.close()


def test_desktop_import_records_explicit_function_call_file_paths(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    observed = tmp_path / "readme.md"
    observed.write_text("hello", encoding="utf-8")
    session_id = "desktop-files"
    turn_id = "desktop-file-turn"
    records = [
        {"timestamp": "2026-09-02T08:00:00Z", "type": "session_meta", "payload": {"id": session_id, "originator": "codex_work_desktop", "cli_version": "0.152.1", "cwd": str(tmp_path)}},
        {"timestamp": "2026-09-02T08:00:01Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}},
        {"timestamp": "2026-09-02T08:00:02Z", "type": "response_item", "payload": {"type": "function_call", "name": "read_file", "call_id": "call-1", "arguments": json.dumps({"path": str(observed)})}},
    ]
    (root / "desktop.jsonl").write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    db = connect(tmp_path / "trace.db")

    import_desktop_sessions(root, db)
    row = db.execute("SELECT path, access_kind, size_bytes, source FROM file_observations").fetchone()
    assert tuple(row) == ("readme.md", "observed", 5, "transcript:desktop:structured_tool_input")
    db.close()
