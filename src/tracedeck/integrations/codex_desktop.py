"""Bounded, read-only importer for local Codex Desktop session JSONL files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import record_file_observation, record_maintenance, store_message, store_prompt, upsert_session, upsert_turn
from ..redact import redact
from ..prompt_policy import prompt_for_storage
from ..truncate import truncate_text
from .codex_transcript import CODEX_0152_PREFIX, USAGE_FIELDS, _usage_values


SOURCE = "transcript:desktop:codex-0.152.x"
DESKTOP_ORIGINATORS = {"codex_work_desktop", "Codex Desktop"}
MAX_FILES_PER_SCAN = 200
MAX_TURNS_PER_FILE = 2_000
MAX_LINE_BYTES = 8 * 1024 * 1024
PROMPT_LIMIT = 256 * 1024
RESPONSE_LIMIT = 1024 * 1024
IDENTIFIER_LIMIT = 4 * 1024
PATH_KEYS = {"path", "file_path", "filename", "target_file", "file"}
WRITE_TOOLS = {"apply_patch", "write_file", "edit_file", "save_file"}


def discover_desktop_transcripts(root: Path, limit: int = MAX_FILES_PER_SCAN) -> list[Path]:
    """Return bounded, regular JSONL files strictly below the configured root."""

    root = Path(root)
    if limit < 1 or not root.is_dir():
        return []
    try:
        resolved_root = root.resolve()
        candidates = []
        for candidate in root.rglob("*.jsonl"):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                candidate.resolve().relative_to(resolved_root)
            except ValueError:
                continue
            candidates.append(candidate)
    except OSError:
        return []
    candidates.sort(key=lambda path: (path.stat().st_mtime_ns, str(path)))
    return candidates[-limit:]


def import_desktop_sessions(root: Path, connection, limit: int = MAX_FILES_PER_SCAN) -> dict[str, int]:
    """Import changed desktop transcripts without modifying the source files."""

    # Accept both argument orders for compatibility with early packet callers.
    if hasattr(root, "execute") and isinstance(connection, (str, Path)):
        root, connection = connection, root
    stats = {"files": 0, "turns": 0, "unsupported": 0, "skipped": 0}
    for path in discover_desktop_transcripts(Path(root), limit):
        try:
            file_stat = path.stat()
        except OSError:
            continue
        previous = connection.execute(
            "SELECT size_bytes, mtime_ns FROM desktop_import_files WHERE path=?", (str(path),)
        ).fetchone()
        if previous is not None and previous[0] == file_stat.st_size and previous[1] == file_stat.st_mtime_ns:
            stats["skipped"] += 1
            continue
        result = _import_file(connection, path, file_stat.st_size, file_stat.st_mtime_ns)
        stats["files"] += 1
        stats["turns"] += result["turns"]
        stats["unsupported"] += result["unsupported"]
    return stats


def _import_file(connection, path: Path, size_bytes: int, mtime_ns: int) -> dict[str, int]:
    records_seen = 0
    unsupported = 0
    turns: dict[str, dict[str, Any]] = {}
    session: dict[str, Any] | None = None
    current_turn_id: str | None = None
    partial_record = False

    try:
        with path.open("rb") as handle:
            for raw_line, oversized in _bounded_lines(handle):
                records_seen += 1
                if oversized:
                    unsupported += 1
                    _unsupported(connection, path, "oversized_record")
                    continue
                if not raw_line.endswith(b"\n"):
                    partial_record = True
                try:
                    record = json.loads(raw_line)
                    if not isinstance(record, dict):
                        raise ValueError("record_not_object")
                    record_type = record.get("type")
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        raise ValueError("payload_not_object")
                    if record_type == "session_meta":
                        session = _session_meta(record, payload)
                        continue
                    if session is None:
                        continue
                    timestamp = _timestamp(record.get("timestamp"))
                    if record_type == "turn_context":
                        current_turn_id = _string(payload.get("turn_id")) or current_turn_id
                        if current_turn_id:
                            turn = _turn(turns, current_turn_id)
                            turn["model"] = _bounded_identifier(payload.get("model")) or turn.get("model")
                        continue
                    if record_type == "event_msg":
                        current_turn_id = _event(turns, payload, current_turn_id, timestamp)
                        continue
                    if record_type == "response_item":
                        if payload.get("type") == "function_call":
                            _function_call(turns, payload, current_turn_id, timestamp)
                        else:
                            _response(turns, payload, current_turn_id)
                        continue
                    if record_type == "custom_tool_call" or record_type == "custom_tool_call_output":
                        # Tool outputs and arguments are intentionally not imported in P2.7.
                        # P7.1 owns a separate attribution contract for this source.
                        continue
                    if record_type not in {"world_state", "compacted", "task_started", "task_complete"}:
                        raise ValueError("unknown_record")
                except (TypeError, ValueError, json.JSONDecodeError):
                    unsupported += 1
                    _unsupported(connection, path, "unknown_record")
    except (OSError, UnicodeDecodeError):
        unsupported += 1
        _unsupported(connection, path, "read_error")
        _remember_file(connection, path, size_bytes, mtime_ns, "unsupported", records_seen, 0, unsupported)
        return {"turns": 0, "unsupported": unsupported}

    if session is None:
        unsupported += 1
        _unsupported(connection, path, "missing_desktop_session_meta")
        _remember_file(connection, path, size_bytes, mtime_ns, "unsupported", records_seen, 0, unsupported)
        return {"turns": 0, "unsupported": unsupported}

    session_id = session["session_id"]
    session_pk = _project_session(connection, session, path)
    imported_turns = 0
    for turn_id, turn in turns.items():
        if _project_turn(connection, session_pk, turn_id, turn, session["codex_version"]):
            imported_turns += 1
    status = "partial" if partial_record else "imported"
    _remember_file(connection, path, size_bytes, mtime_ns, status, records_seen, imported_turns, unsupported)
    return {"turns": imported_turns, "unsupported": unsupported}


def _session_meta(record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    originator = _string(payload.get("originator"))
    version = _string(payload.get("cli_version"))
    session_id = _bounded_identifier(payload.get("session_id")) or _bounded_identifier(payload.get("id"))
    if originator not in DESKTOP_ORIGINATORS or not version or not version.startswith(CODEX_0152_PREFIX) or not session_id:
        raise ValueError("unsupported_desktop_session")
    return {
        "session_id": session_id,
        "codex_version": version,
        "cwd": _bounded_identifier(payload.get("cwd")),
        "started_at": _timestamp(record.get("timestamp")) or _timestamp(payload.get("timestamp")),
        "source": "transcript:desktop",
    }


def _event(turns: dict[str, dict[str, Any]], payload: dict[str, Any], current: str | None, timestamp: str | None) -> str | None:
    event_type = payload.get("type")
    turn_id = _bounded_identifier(payload.get("turn_id")) or current
    if event_type == "task_started":
        turn_id = _bounded_identifier(payload.get("turn_id")) or current
        if turn_id:
            turn = _turn(turns, turn_id)
            turn["started_at"] = _timestamp(payload.get("started_at")) or timestamp
            turn["lifecycle_status"] = "in_progress"
    elif event_type == "task_complete" and turn_id:
        turn = _turn(turns, turn_id)
        turn["ended_at"] = _timestamp(payload.get("completed_at")) or timestamp
        duration = payload.get("duration_ms")
        if isinstance(duration, int) and duration >= 0:
            turn["duration_ms"] = duration
        turn["lifecycle_status"] = "completed"
        if isinstance(payload.get("last_agent_message"), str):
            turn["assistant_response"] = payload["last_agent_message"]
    elif event_type in {"turn_aborted", "task_failed"} and turn_id:
        _turn(turns, turn_id)["lifecycle_status"] = "interrupted" if event_type == "turn_aborted" else "failed"
    elif event_type == "token_count":
        info = payload.get("info")
        usage = info.get("last_token_usage") if isinstance(info, dict) else None
        values = _usage_values(usage) if isinstance(usage, dict) else None
        if values is not None and turn_id:
            _turn(turns, turn_id)["usage"] = values
    return turn_id


def _response(turns: dict[str, dict[str, Any]], payload: dict[str, Any], current: str | None) -> None:
    turn_id = current
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, dict):
        turn_id = _bounded_identifier(metadata.get("turn_id")) or turn_id
    if not turn_id or payload.get("type") != "message":
        return
    role = payload.get("role")
    text = _content_text(payload.get("content"), PROMPT_LIMIT if role == "user" else RESPONSE_LIMIT)
    if not text:
        return
    target = _turn(turns, turn_id)
    if role == "user":
        target.setdefault("user_prompt", text)
    elif role == "assistant" and payload.get("phase") == "final_answer":
        target["assistant_response"] = text


def _function_call(turns: dict[str, dict[str, Any]], payload: dict[str, Any], current: str | None, timestamp: str | None) -> None:
    """Keep only explicit structured file arguments from transcript calls."""

    turn_id = current
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, dict):
        turn_id = _bounded_identifier(metadata.get("turn_id")) or turn_id
    if not turn_id:
        return
    arguments = payload.get("arguments")
    if not isinstance(arguments, str):
        return
    try:
        tool_input = json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        return
    if not isinstance(tool_input, dict):
        return
    tool_name = _bounded_identifier(payload.get("name")) or "unknown"
    paths = _explicit_paths(tool_name, tool_input)
    if paths:
        _turn(turns, turn_id).setdefault("file_observations", []).extend((path, kind, timestamp, payload.get("call_id")) for path, kind in paths)


def _explicit_paths(tool_name: str, tool_input: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[str] = []
    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and key in PATH_KEYS and value.strip():
            found.append(value.strip())
    visit(tool_input)
    if tool_name == "apply_patch" and isinstance(tool_input.get("patch"), str):
        for line in tool_input["patch"].splitlines():
            for marker in ("*** Update File:", "*** Add File:", "*** Delete File:"):
                if line.startswith(marker):
                    found.append(line[len(marker):].strip())
    unique = list(dict.fromkeys(found))
    kind = "written" if tool_name in WRITE_TOOLS else "observed"
    return [(path, kind) for path in unique]


def _safe_desktop_path(raw_path: str, cwd: str | None) -> tuple[str, int | None]:
    candidate = Path(raw_path)
    base = Path(cwd) if cwd else None
    resolved = base / candidate if base and not candidate.is_absolute() else candidate
    try:
        resolved = resolved.resolve()
        size = resolved.stat().st_size if resolved.is_file() else None
        if base:
            try:
                return resolved.relative_to(base.resolve()).as_posix(), size
            except ValueError:
                return f"<external>/{resolved.name}", size
        return resolved.name, size
    except (OSError, RuntimeError, ValueError):
        return Path(raw_path.replace("\\", "/")).name or "<unknown>", None


def _project_session(connection, session: dict[str, Any], path: Path) -> int:
    values = {
        "transcript_path": str(path),
        "codex_version": session["codex_version"],
        "start_source": session["source"],
        "source": session["source"],
        "timestamp_source": "transcript:desktop",
    }
    for key in ("cwd", "started_at"):
        if session.get(key) is not None:
            values["cwd_initial" if key == "cwd" else "started_at"] = session[key]
            values["cwd_last" if key == "cwd" else "started_at"] = session[key]
    existing = connection.execute("SELECT * FROM sessions WHERE codex_session_id=?", (session["session_id"],)).fetchone()
    if existing is None:
        return upsert_session(connection, session["session_id"], **values)
    updates = {key: value for key, value in values.items() if value is not None}
    if existing[existing.keys().index("transcript_path")] is not None:
        updates.pop("transcript_path", None)
    if existing[existing.keys().index("start_source")] is not None:
        updates.pop("start_source", None)
    if existing[existing.keys().index("source")] is not None:
        updates.pop("source", None)
    if existing[existing.keys().index("timestamp_source")] is not None:
        updates.pop("timestamp_source", None)
    if existing[existing.keys().index("started_at")] is not None:
        updates.pop("started_at", None)
    if existing[existing.keys().index("cwd_initial")] is not None:
        updates.pop("cwd_initial", None)
    updates["updated_at"] = _latest(existing[existing.keys().index("updated_at")], session.get("started_at")) or datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    assignments = ", ".join(f"{key}=?" for key in updates)
    connection.execute(f"UPDATE sessions SET {assignments} WHERE id=?", (*updates.values(), existing[0]))
    return int(existing[0])


def _project_turn(connection, session_id: int, turn_id: str, turn: dict[str, Any], version: str) -> bool:
    existing = connection.execute("SELECT * FROM turns WHERE session_id=? AND codex_turn_id=?", (session_id, turn_id)).fetchone()
    values: dict[str, Any] = {"source": SOURCE, "lifecycle_status": turn.get("lifecycle_status", "unknown")}
    prompt_value: str | None = None
    response_value: str | None = None
    for field in ("user_prompt", "assistant_response", "model", "started_at", "ended_at", "duration_ms"):
        if turn.get(field) is not None:
            value = turn[field]
            if field in {"user_prompt", "assistant_response"}:
                if field == "user_prompt":
                    value = prompt_for_storage(value)
                value, redacted, original_size, truncated = _safe_text(value, PROMPT_LIMIT if field == "user_prompt" else RESPONSE_LIMIT)
                values["prompt_redacted" if field == "user_prompt" else "response_redacted"] = int(redacted)
                values["prompt_truncated" if field == "user_prompt" else "response_truncated"] = int(truncated)
                values["prompt_original_size_bytes" if field == "user_prompt" else "response_original_size_bytes"] = original_size
                if field == "user_prompt" and value is not None:
                    prompt_value = value
                    values["prompt_id"] = store_prompt(connection, value)
                elif field == "assistant_response" and value is not None:
                    response_value = value
            else:
                values[field] = value
    if "duration_ms" in values:
        values["duration_source"] = "transcript:desktop:task_complete"
    usage = turn.get("usage")
    if usage is not None:
        values.update(usage)
        values["usage_source"] = SOURCE
    if existing is None:
        turn_pk = upsert_turn(connection, session_id, turn_id, **values)
        if prompt_value is not None:
            store_message(connection, session_id, turn_pk, "user", prompt_value, 0)
        if response_value is not None:
            store_message(connection, session_id, turn_pk, "assistant", response_value, 1)
        _project_file_observations(connection, session_id, turn_pk, turn_id, turn)
        return True
    updates: dict[str, Any] = {}
    for key, value in values.items():
        if key == "lifecycle_status":
            if existing[existing.keys().index("lifecycle_status")] in {"completed", "failed", "interrupted"}:
                continue
        if key == "usage_source" and existing[existing.keys().index("usage_source")] is not None:
            continue
        if key in USAGE_FIELDS and existing[existing.keys().index("usage_source")] is not None:
            continue
        if key == "source" and existing[existing.keys().index("source")] is not None:
            continue
        if key.endswith("_redacted") or key.endswith("_truncated"):
            value = int(bool(existing[existing.keys().index(key)]) or bool(value))
        elif key in {"user_prompt", "assistant_response", "prompt_id", "model", "started_at", "ended_at", "duration_ms", "duration_source", "prompt_original_size_bytes", "response_original_size_bytes"}:
            if existing[existing.keys().index(key)] is not None:
                continue
        updates[key] = value
    updates["updated_at"] = _latest(existing[existing.keys().index("updated_at")], turn.get("ended_at"), turn.get("started_at")) or datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    if updates:
        assignments = ", ".join(f"{key}=?" for key in updates)
        connection.execute(f"UPDATE turns SET {assignments} WHERE id=?", (*updates.values(), existing[0]))
    if prompt_value is not None and existing[existing.keys().index("prompt_id")] is None:
        store_message(connection, session_id, int(existing[0]), "user", prompt_value, 0)
    if response_value is not None:
        store_message(connection, session_id, int(existing[0]), "assistant", response_value, 1)
    _project_file_observations(connection, session_id, int(existing[0]), turn_id, turn)
    return True


def _project_file_observations(connection, session_id: int, turn_pk: int, turn_id: str, turn: dict[str, Any]) -> None:
    session = connection.execute("SELECT cwd_last FROM sessions WHERE id=?", (session_id,)).fetchone()
    cwd = session[0] if session else None
    for index, (raw_path, kind, timestamp, call_id) in enumerate(turn.get("file_observations", [])):
        path, size = _safe_desktop_path(raw_path, cwd)
        observed_at = timestamp or turn.get("started_at") or datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        event_id = f"desktop:{turn_id}:{call_id or index}:{kind}:{path}"
        record_file_observation(
            connection, turn_pk, None, path, kind, size, observed_at,
            "transcript:desktop:structured_tool_input", "high", event_id,
        )


def _turn(turns: dict[str, dict[str, Any]], turn_id: str) -> dict[str, Any]:
    if turn_id not in turns:
        if len(turns) >= MAX_TURNS_PER_FILE:
            raise ValueError("too_many_turns")
        turns[turn_id] = {}
    return turns[turn_id]


def _content_text(content: Any, limit: int) -> str | None:
    if not isinstance(content, list):
        return None
    parts = [block.get("text") for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]
    if not parts:
        return None
    text, _, _, _ = _safe_text("\n".join(parts), limit)
    return text


def _safe_text(value: Any, limit: int) -> tuple[str, bool, int | None, bool]:
    masked, redacted = redact(str(value))
    bounded, truncated, original_size = truncate_text(masked, limit)
    return bounded, redacted, original_size, truncated


def _bounded_lines(handle):
    """Yield complete lines without retaining more than the transient ceiling."""

    while True:
        raw_line = handle.readline(MAX_LINE_BYTES + 1)
        if not raw_line:
            return
        oversized = len(raw_line) > MAX_LINE_BYTES
        if oversized and not raw_line.endswith(b"\n"):
            while True:
                remainder = handle.readline(MAX_LINE_BYTES + 1)
                if not remainder or remainder.endswith(b"\n"):
                    break
        yield raw_line, oversized


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bounded_identifier(value: Any) -> str | None:
    value = _string(value)
    return value[:IDENTIFIER_LIMIT] if value else None


def _timestamp(value: Any) -> str | None:
    value = _string(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    except ValueError:
        return None


def _latest(*values: str | None) -> str | None:
    valid = [value for value in values if value]
    return max(valid) if valid else None


def _unsupported(connection, path: Path, reason: str) -> None:
    record_maintenance(connection, "unsupported_format", {"source": "transcript:desktop", "file": path.name[:IDENTIFIER_LIMIT], "reason": reason})


def _remember_file(connection, path: Path, size: int, mtime_ns: int, status: str, records: int, turns: int, unsupported: int) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    connection.execute(
        "INSERT INTO desktop_import_files(path,size_bytes,mtime_ns,attempted_at,status,records_seen,turns_imported,unsupported_records) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET size_bytes=excluded.size_bytes,mtime_ns=excluded.mtime_ns,attempted_at=excluded.attempted_at,status=excluded.status,records_seen=excluded.records_seen,turns_imported=excluded.turns_imported,unsupported_records=excluded.unsupported_records",
        (str(path), size, mtime_ns, now, status, records, turns, unsupported),
    )
