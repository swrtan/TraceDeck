import json

from tracedeck.db import connect, read_prompt
from tracedeck.normalize import project_event
from tracedeck.spool import HookEnvelope


def event(kind, data, event_id):
    return HookEnvelope(kind, data, "2026-09-01T00:00:00+00:00", 1, event_id=event_id)


def test_prompt_stop_and_tool_projection_redacts_and_discards_response(tmp_path):
    db = connect(tmp_path / "trace.db")
    project_event(db, event("UserPromptSubmit", {"session_id": "s", "turn_id": "t", "prompt": "use api_key=sk-secret-value"}, "1"))
    project_event(db, event("PreToolUse", {"session_id": "s", "turn_id": "t", "tool_name": "Bash", "tool_use_id": "c", "tool_input": {"command": "echo hi", "password": "secret"}}, "2"))
    project_event(db, event("PostToolUse", {"session_id": "s", "turn_id": "t", "tool_name": "Bash", "tool_use_id": "c", "tool_response": {"status": "completed", "output": "do not store"}}, "3"))
    project_event(db, event("Stop", {"session_id": "s", "turn_id": "t", "last_assistant_message": "done"}, "4"))

    prompt = db.execute("SELECT user_prompt, prompt_id, lifecycle_status FROM turns").fetchone()
    tool = db.execute("SELECT arguments_json, lifecycle_status FROM tool_calls").fetchone()
    assert prompt[0] is None
    assert prompt[1] is not None
    assert read_prompt(db, prompt[1]) == "use api_key=[REDACTED]"
    assert prompt[2] == "completed"
    assert db.execute("SELECT compressed_size_bytes FROM prompt_store WHERE id=?", (prompt[1],)).fetchone()[0] > 0
    assert "secret" not in tool[0]
    assert tool[1] == "completed"
    assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE sql LIKE '%do not store%'").fetchone()[0] == 0
    db.close()


def test_document_prompt_keeps_only_safe_name(tmp_path):
    db = connect(tmp_path / "trace.db")
    project_event(db, event("UserPromptSubmit", {"session_id": "s", "turn_id": "doc", "prompt": "Document: C:/Users/test/financial-report.pdf"}, "1"))
    document = db.execute("SELECT user_prompt, prompt_id FROM turns").fetchone()
    assert document[0] is None
    assert read_prompt(db, document[1]) == "[Document: financial-report.pdf]"
    db.close()


def test_post_without_pre_is_partial(tmp_path):
    db = connect(tmp_path / "trace.db")
    project_event(db, event("PostToolUse", {"session_id": "s", "turn_id": "t", "tool_name": "Bash", "tool_use_id": "c", "tool_response": {}}, "1"))
    assert tuple(db.execute("SELECT lifecycle_status, pre_observed, post_observed, duration_ms FROM tool_calls").fetchone()) == ("partial", 0, 1, None)
    db.close()


def test_shell_exit_code_maps_tool_status(tmp_path):
    db = connect(tmp_path / "trace.db")
    project_event(db, event("PreToolUse", {"session_id": "s", "turn_id": "t", "tool_name": "Bash", "tool_use_id": "c", "tool_input": {"command": "false"}}, "1"))
    project_event(db, event("PostToolUse", {"session_id": "s", "turn_id": "t", "tool_name": "Bash", "tool_use_id": "c", "tool_response": {"exit_code": 1}}, "2"))
    assert db.execute("SELECT lifecycle_status FROM tool_calls").fetchone()[0] == "failed"
    db.close()


def test_structured_file_observations_record_read_and_written_sizes(tmp_path):
    observed = tmp_path / "observed.txt"
    observed.write_text("hello", encoding="utf-8")
    written = tmp_path / "written.txt"
    db = connect(tmp_path / "trace.db")
    project_event(db, event("SessionStart", {"session_id": "files", "cwd": str(tmp_path)}, "session"))
    project_event(db, event("UserPromptSubmit", {"session_id": "files", "turn_id": "turn", "prompt": "inspect files"}, "prompt"))
    project_event(db, event("PreToolUse", {"session_id": "files", "turn_id": "turn", "tool_name": "mcp__fixture__read", "tool_use_id": "read", "tool_input": {"path": str(observed)}}, "read-pre"))
    project_event(db, event("PreToolUse", {"session_id": "files", "turn_id": "turn", "tool_name": "apply_patch", "tool_use_id": "write", "tool_input": {"patch": f"*** Add File: {written}\n+hello"}}, "write-pre"))
    written.write_text("hello", encoding="utf-8")
    project_event(db, event("PostToolUse", {"session_id": "files", "turn_id": "turn", "tool_name": "apply_patch", "tool_use_id": "write", "tool_input": {"patch": f"*** Add File: {written}\n+hello"}, "tool_response": {"status": "completed"}}, "write-post"))
    rows = db.execute("SELECT path, access_kind, size_bytes FROM file_observations ORDER BY access_kind").fetchall()
    assert [(row[1], row[2]) for row in rows] == [("observed", 5), ("written", 5)]
    assert all("Users" not in row[0] for row in rows)
    db.close()


def test_stop_reconciles_transcript_version_when_hook_omits_codex_version(tmp_path):
    transcript = tmp_path / "codex-0152.jsonl"
    usage = {"input_tokens": 11, "cached_input_tokens": 7, "cache_write_input_tokens": 0,
             "output_tokens": 5, "reasoning_output_tokens": 2, "total_tokens": 16}
    records = [
        {"type": "session_meta", "payload": {"cli_version": "0.152.0"}},
        {"type": "turn_context", "payload": {"turn_id": "t"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": usage}}},
    ]
    transcript.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    db = connect(tmp_path / "trace.db")
    project_event(db, event("SessionStart", {"session_id": "s", "transcript_path": str(transcript)}, "1"))
    project_event(db, event("UserPromptSubmit", {"session_id": "s", "turn_id": "t", "prompt": "hello"}, "2"))
    project_event(db, event("Stop", {"session_id": "s", "turn_id": "t"}, "3"))

    assert tuple(db.execute("SELECT input_tokens, total_tokens, usage_source FROM turns").fetchone()) == (11, 16, "transcript:codex-0.152.x")
    db.close()
