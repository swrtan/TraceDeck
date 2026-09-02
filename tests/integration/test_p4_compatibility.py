import json
import os
import subprocess
import sys

from tracedeck.db import connect
from tracedeck.normalize import project_event
from tracedeck.spool import HookEnvelope


def run_owned_hook(data_dir, event_name, payload):
    environment = os.environ.copy()
    environment["TRACEDECK_DATA_DIR"] = str(data_dir)
    result = subprocess.run(
        [sys.executable, "-m", "tracedeck", "hook-event", event_name, "--owned"],
        input=json.dumps(payload), text=True, capture_output=True, env=environment, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ('{"continue":true}\n' if event_name == "Stop" else "")


def test_owned_subprocess_hooks_cover_representative_local_matrix(tmp_path):
    data_dir = tmp_path / "data"
    session = {"session_id": "compat-session", "model": "fixture-model", "cwd": "C:/fixture"}
    events = [
        ("SessionStart", session),
        ("UserPromptSubmit", {**session, "turn_id": "normal", "prompt": "normal turn"}),
        ("PreToolUse", {**session, "turn_id": "normal", "tool_name": "Bash", "tool_use_id": "failed-call", "tool_input": {"command": "false"}}),
        ("PostToolUse", {**session, "turn_id": "normal", "tool_name": "Bash", "tool_use_id": "failed-call", "tool_response": {"is_error": True, "error": "command failed"}}),
        ("PreToolUse", {**session, "turn_id": "normal", "tool_name": "apply_patch", "tool_use_id": "patch-call", "tool_input": {"patch": "*** Begin Patch"}}),
        ("PostToolUse", {**session, "turn_id": "normal", "tool_name": "apply_patch", "tool_use_id": "patch-call", "tool_response": {"status": "completed"}}),
        ("PreToolUse", {**session, "turn_id": "normal", "tool_name": "mcp__fixture__read", "tool_use_id": "mcp-call", "tool_input": {"path": "fixture"}}),
        ("PostToolUse", {**session, "turn_id": "normal", "tool_name": "mcp__fixture__read", "tool_use_id": "mcp-call", "tool_response": {"status": "completed"}}),
        ("PreToolUse", {**session, "turn_id": "normal", "tool_name": "unified_exec", "tool_use_id": "long-call", "tool_input": {"command": "long-running"}}),
        ("Stop", {**session, "turn_id": "normal"}),
        # Duplicate delivery must remain idempotent at the domain key level.
        ("PostToolUse", {**session, "turn_id": "normal", "tool_name": "Bash", "tool_use_id": "failed-call", "tool_response": {"is_error": True, "error": "command failed"}}),
        ("SessionEnd", {**session, "reason": "exit"}),
    ]
    for event_name, payload in events:
        run_owned_hook(data_dir, event_name, payload)
    assert len(list((data_dir / "spool" / "pending").glob("*.json"))) == len(events)

    projection_dir = tmp_path / "projection"
    projection_events = []
    for index, (event_name, payload) in enumerate(events):
        projection_events.append(HookEnvelope(event_name, payload, f"2026-09-01T00:00:{index:02d}+00:00", index + 1))
    db = connect(projection_dir / "trace.db")
    # Arrival order can race across hook processes; a late prompt must not
    # regress a terminal lifecycle state already recorded by Stop.
    late_stop = projection_events[9]
    project_event(db, late_stop)
    db.commit()
    late_prompt = projection_events[1]
    project_event(db, late_prompt)
    db.commit()
    skipped = {late_stop.event_id, late_prompt.event_id}
    for event in projection_events:
        if event.event_id in skipped:
            continue
        db.execute("BEGIN IMMEDIATE")
        project_event(db, event)
        db.commit()
    assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    assert db.execute("SELECT lifecycle_status FROM turns").fetchone()[0] == "completed"
    assert db.execute("SELECT assistant_response FROM turns").fetchone()[0] is None
    assert db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 4
    assert db.execute("SELECT lifecycle_status FROM tool_calls WHERE call_id='failed-call'").fetchone()[0] == "failed"
    assert tuple(db.execute("SELECT lifecycle_status, post_observed FROM tool_calls WHERE call_id='long-call'").fetchone()) == ("in_progress", 0)
    db.close()
