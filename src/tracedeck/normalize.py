"""Convert hook envelopes into bounded domain rows."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import record_file_observation, upsert_session, upsert_tool_call, upsert_turn
from .integrations.codex_transcript import reconcile_transcript
from .prompt_policy import prompt_for_storage
from .redact import redact
from .spool import AtomicSpool, HookEnvelope
from .truncate import truncate_text


PROMPT_LIMIT = 256 * 1024
RESPONSE_LIMIT = 1024 * 1024
ARGUMENT_LIMIT = 256 * 1024
ERROR_LIMIT = 64 * 1024
_PATH_KEYS = {"path", "file_path", "filename", "target_file", "file"}
_WRITE_TOOLS = {"apply_patch", "write_file", "edit_file", "save_file"}


def _file_paths(tool_name: str, tool_input: Any) -> list[tuple[str, str]]:
    """Extract explicit paths only; never inspect file contents or parse shell output."""

    found: list[tuple[str, str]] = []
    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and key in _PATH_KEYS and value.strip():
            found.append((value.strip(), "high"))
    visit(tool_input)
    if tool_name == "apply_patch" and isinstance(tool_input, dict):
        patch = tool_input.get("patch")
        if isinstance(patch, str):
            for line in patch.splitlines():
                marker = next((prefix for prefix in ("*** Update File:", "*** Add File:", "*** Delete File:") if line.startswith(prefix)), None)
                if marker:
                    found.append((line[len(marker):].strip(), "high"))
    unique: list[tuple[str, str]] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def _safe_file_path(raw_path: str, cwd: str | None) -> tuple[str, int | None]:
    candidate = Path(raw_path)
    base = Path(cwd) if cwd else None
    resolved = (base / candidate if base and not candidate.is_absolute() else candidate)
    try:
        resolved = resolved.resolve()
        size = resolved.stat().st_size if resolved.is_file() else None
        if base:
            base_resolved = base.resolve()
            try:
                return resolved.relative_to(base_resolved).as_posix(), size
            except ValueError:
                return f"<external>/{resolved.name}", size
        return resolved.name, size
    except (OSError, RuntimeError, ValueError):
        return Path(raw_path.replace("\\", "/")).name or "<unknown>", None


def _record_file_paths(connection, session_pk: int, turn_pk: int, tool_pk: int | None, tool_name: str, tool_input: Any, access_kind: str, observed_at: str, event: HookEnvelope) -> None:
    session = connection.execute("SELECT cwd_last FROM sessions WHERE id=?", (session_pk,)).fetchone()
    cwd = session[0] if session else None
    for raw_path, confidence in _file_paths(tool_name, tool_input):
        path, size = _safe_file_path(raw_path, cwd)
        record_file_observation(connection, turn_pk, tool_pk, path, access_kind, size, observed_at, "hook:structured_tool_input", confidence, f"{event.event_id}:{access_kind}:{path}")


def _duration(started_at: str | None, ended_at: str | None) -> int | None:
    if not started_at or not ended_at:
        return None
    try:
        return max(0, round((datetime.fromisoformat(ended_at.replace("Z", "+00:00")) - datetime.fromisoformat(started_at.replace("Z", "+00:00"))).total_seconds() * 1000))
    except ValueError:
        return None


def _text(value: Any, limit: int) -> tuple[str | None, bool, int | None, bool]:
    if value is None:
        return None, False, None, False
    redacted, was_redacted = redact(str(value))
    result, was_truncated, original_size = truncate_text(str(redacted), limit)
    return result, was_redacted, original_size, was_truncated


def _session(connection, event: HookEnvelope) -> int:
    data = event.data
    session_id = str(data.get("session_id", ""))
    if not session_id:
        raise ValueError("missing session_id")
    values = {"updated_at": event.observed_at}
    values["source"] = "hook"
    if data.get("transcript_path") is not None:
        values["transcript_path"] = str(data["transcript_path"])
    if data.get("cwd") is not None:
        values["cwd_last"] = str(data["cwd"])
        values.setdefault("cwd_initial", str(data["cwd"]))
    if data.get("model") is not None:
        values["model_last"] = str(data["model"])
        values.setdefault("model_initial", str(data["model"]))
    if event.codex_version is not None:
        values["codex_version"] = event.codex_version
    elif data.get("codex_version") is not None:
        values["codex_version"] = str(data["codex_version"])
    if event.event_type == "SessionStart":
        values.update(started_at=event.observed_at, start_source=data.get("source"), lifecycle_status="in_progress")
    elif event.event_type == "SessionEnd":
        values.update(ended_at=event.observed_at, lifecycle_status="completed")
    return upsert_session(connection, session_id, **values)


def project_event(connection, event: HookEnvelope) -> None:
    session_pk = _session(connection, event)
    data = event.data
    if event.event_type in {"SessionStart", "SessionEnd"}:
        if event.event_type == "SessionEnd":
            _reconcile_session_transcript(connection, session_pk)
        return
    turn_key = data.get("turn_id")
    if not turn_key:
        raise ValueError("missing turn_id")
    turn_id = str(turn_key)
    common = {"updated_at": event.observed_at, "source": "hook"}
    if data.get("model") is not None:
        common["model"] = data["model"]
    if event.event_type == "UserPromptSubmit":
        prompt, redacted, original_size, truncated = _text(prompt_for_storage(data.get("prompt")), PROMPT_LIMIT)
        existing_turn = connection.execute("SELECT lifecycle_status FROM turns WHERE session_id=? AND codex_turn_id=?", (session_pk, turn_id)).fetchone()
        lifecycle_status = "in_progress"
        if existing_turn is not None and existing_turn[0] in {"completed", "failed", "interrupted"}:
            lifecycle_status = existing_turn[0]
        upsert_turn(connection, session_pk, turn_id, started_at=event.observed_at,
                    lifecycle_status=lifecycle_status, user_prompt=prompt, prompt_redacted=int(redacted), prompt_truncated=int(truncated),
                    prompt_original_size_bytes=original_size, **common)
    elif event.event_type == "Stop":
        response, redacted, original_size, truncated = _text(data.get("last_assistant_message"), RESPONSE_LIMIT)
        row = connection.execute("SELECT started_at FROM turns WHERE session_id=? AND codex_turn_id=?", (session_pk, turn_id)).fetchone()
        started_at = row[0] if row else None
        upsert_turn(connection, session_pk, turn_id, ended_at=event.observed_at,
                    duration_ms=_duration(started_at, event.observed_at), duration_source="observer_wall_clock" if started_at else None,
                    lifecycle_status="completed", response_redacted=int(redacted), response_truncated=int(truncated),
                    response_original_size_bytes=original_size, assistant_response=response, **common)
        _reconcile_session_transcript(connection, session_pk)
    elif event.event_type in {"PreToolUse", "PostToolUse"}:
        existing_turn = connection.execute("SELECT id FROM turns WHERE session_id=? AND codex_turn_id=?", (session_pk, turn_id)).fetchone()
        if existing_turn is None:
            turn_id = upsert_turn(connection, session_pk, turn_id, lifecycle_status="in_progress", **common)
        else:
            turn_id = int(existing_turn[0])
            if "model" in common:
                connection.execute("UPDATE turns SET model=?, updated_at=? WHERE id=?", (common["model"], common["updated_at"], turn_id))
        call_id = data.get("tool_use_id")
        tool_name = str(data.get("tool_name", "unknown"))
        values: dict[str, Any] = {"updated_at": event.observed_at}
        if event.event_type == "PreToolUse":
            safe_input, input_redacted = redact(data.get("tool_input", {}))
            arguments, text_redacted, original_size, truncated = _text(json.dumps(safe_input, ensure_ascii=False), ARGUMENT_LIMIT)
            redacted = input_redacted or text_redacted
            values.update(arguments_json=arguments, arguments_redacted=int(redacted), arguments_truncated=int(truncated),
                          arguments_original_size_bytes=original_size, started_at=event.observed_at, pre_observed=1, lifecycle_status="in_progress")
        else:
            row = connection.execute("SELECT started_at, pre_observed FROM tool_calls WHERE turn_id=? AND call_id=?", (turn_id, call_id)).fetchone()
            status = "unknown"
            error = None
            response = data.get("tool_response")
            if isinstance(response, dict):
                if response.get("is_error") is True or response.get("error") is not None:
                    status = "failed"
                    error, _, _, _ = _text(response.get("error") or response.get("message"), ERROR_LIMIT)
                elif isinstance(response.get("exit_code"), int):
                    status = "completed" if response["exit_code"] == 0 else "failed"
                elif response.get("status") in {"completed", "failed", "declined", "partial"}:
                    status = response["status"]
            if row is None:
                status = "partial"
                started_at = None
            else:
                started_at = row[0]
            values.update(ended_at=event.observed_at, duration_ms=_duration(started_at, event.observed_at),
                          duration_source="observer_wall_clock" if started_at else None, lifecycle_status=status,
                          error_message=error, post_observed=1)
        tool_pk = upsert_tool_call(connection, turn_id, call_id, tool_name, **values)
        tool_input = data.get("tool_input", {})
        if event.event_type == "PreToolUse" and tool_name not in _WRITE_TOOLS:
            _record_file_paths(connection, session_pk, turn_id, tool_pk, tool_name, tool_input, "observed", event.observed_at, event)
        elif event.event_type == "PostToolUse" and tool_name in _WRITE_TOOLS:
            _record_file_paths(connection, session_pk, turn_id, tool_pk, tool_name, tool_input, "written", event.observed_at, event)


def _reconcile_session_transcript(connection, session_id: int) -> None:
    row = connection.execute("SELECT transcript_path, codex_version FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None or not row[0]:
        return
    reconcile_transcript(Path(row[0]), connection, session_id, row[1])


def project_spool(spool: AtomicSpool, connection) -> tuple[int, int]:
    def handle(event: HookEnvelope) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            project_event(connection, event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return spool.drain(handle)
