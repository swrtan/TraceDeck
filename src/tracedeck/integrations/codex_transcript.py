"""Fail-closed, versioned transcript reconciliation adapters.

The Codex transcript format is not a stable API. Adapters are therefore
version-gated and only accept fields observed with unambiguous semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..db import record_maintenance


SUPPORTED_FIXTURE_VERSION = 1
MAX_LINE_BYTES = 2 * 1024 * 1024
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
CODEX_0152_PREFIX = "0.152."


def _usage_values(usage: dict[str, Any]) -> dict[str, int | None] | None:
    if set(usage) - set(USAGE_FIELDS) or not usage:
        return None
    if any(not isinstance(usage.get(field), int) or usage[field] < 0 for field in usage):
        return None
    if "total_tokens" not in usage:
        return None
    return {field: usage.get(field) for field in USAGE_FIELDS}


def reconcile_transcript(path: Path, connection, session_id: int, codex_version: str | None) -> dict[str, int]:
    """Read exactly *path* and reconcile only a proven versioned format."""

    path = Path(path)
    stats = {"recognized": 0, "unsupported": 0}
    if not path.is_file():
        record_maintenance(connection, "unsupported_format", {"reason": "missing_transcript"})
        return {"recognized": 0, "unsupported": 1}
    codex_version = codex_version or _detect_codex_version(path)
    if codex_version is None:
        record_maintenance(connection, "unsupported_format", {"reason": "missing_codex_version"})
        return {"recognized": 0, "unsupported": 1}
    if not (codex_version.startswith("fixture-") or codex_version.startswith(CODEX_0152_PREFIX)):
        record_maintenance(connection, "unsupported_format", {"reason": "unrecognized_codex_version"})
        return {"recognized": 0, "unsupported": 1}

    if codex_version and codex_version.startswith(CODEX_0152_PREFIX):
        return _reconcile_codex_0152(path, connection, session_id)

    try:
        with path.open("rb") as handle:
            for raw_line in handle:
                if len(raw_line) > MAX_LINE_BYTES:
                    record_maintenance(connection, "unsupported_format", {"reason": "oversized_record"})
                    stats["unsupported"] += 1
                    continue
                try:
                    record = json.loads(raw_line)
                    if not isinstance(record, dict) or record.get("tracedeck_record_version") != SUPPORTED_FIXTURE_VERSION:
                        raise ValueError("unknown_record")
                    if record.get("kind") == "turn_usage":
                        _reconcile_usage(connection, session_id, record)
                    elif record.get("kind") == "turn_error":
                        _reconcile_error(connection, session_id, record)
                    else:
                        raise ValueError("unknown_record")
                    stats["recognized"] += 1
                except (ValueError, TypeError, json.JSONDecodeError):
                    stats["unsupported"] += 1
                    record_maintenance(connection, "unsupported_format", {"reason": "unknown_record"})
    except (OSError, UnicodeDecodeError):
        stats["unsupported"] += 1
        record_maintenance(connection, "unsupported_format", {"reason": "read_error"})
    return stats


def _detect_codex_version(path: Path) -> str | None:
    """Read only the metadata line needed to select a version adapter."""

    try:
        with path.open("rb") as handle:
            for raw_line in handle:
                if len(raw_line) > MAX_LINE_BYTES:
                    return None
                record = json.loads(raw_line)
                if isinstance(record, dict) and record.get("type") == "session_meta":
                    payload = record.get("payload")
                    return payload.get("cli_version") if isinstance(payload, dict) and isinstance(payload.get("cli_version"), str) else None
    except (OSError, UnicodeDecodeError, TypeError, json.JSONDecodeError):
        return None
    return None


def _reconcile_codex_0152(path: Path, connection, session_id: int) -> dict[str, int]:
    """Reconcile the observed 0.152.x JSONL event envelope.

    ``last_token_usage`` is the per-turn usage snapshot. The cumulative
    ``total_token_usage`` field is deliberately ignored because deriving a
    delta would invent accounting semantics across turns.
    """

    stats = {"recognized": 0, "unsupported": 0}
    current_turn_id: str | None = None
    try:
        with path.open("rb") as handle:
            for raw_line in handle:
                if len(raw_line) > MAX_LINE_BYTES:
                    record_maintenance(connection, "unsupported_format", {"reason": "oversized_record"})
                    stats["unsupported"] += 1
                    continue
                try:
                    record = json.loads(raw_line)
                    if not isinstance(record, dict):
                        raise ValueError("unknown_record")
                    record_type = record.get("type")
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        raise ValueError("unknown_record")
                    if record_type == "turn_context":
                        current_turn_id = _string_or_none(payload.get("turn_id"))
                        continue
                    if record_type != "event_msg":
                        if record_type not in {"session_meta", "world_state", "response_item", "compacted"}:
                            raise ValueError("unknown_record")
                        continue
                    if payload.get("type") == "task_started":
                        current_turn_id = _string_or_none(payload.get("turn_id")) or current_turn_id
                    if payload.get("type") != "token_count":
                        continue
                    info = payload.get("info")
                    usage = info.get("last_token_usage") if isinstance(info, dict) else None
                    if usage is None:
                        continue
                    turn_id = _string_or_none(payload.get("turn_id")) or current_turn_id
                    values = _usage_values(usage) if isinstance(usage, dict) else None
                    if turn_id is None or values is None:
                        raise ValueError("ambiguous_usage")
                    _update_usage(connection, session_id, turn_id, values, "transcript:codex-0.152.x")
                    stats["recognized"] += 1
                except (ValueError, TypeError, json.JSONDecodeError):
                    stats["unsupported"] += 1
                    record_maintenance(connection, "unsupported_format", {"reason": "unknown_record"})
    except (OSError, UnicodeDecodeError):
        stats["unsupported"] += 1
        record_maintenance(connection, "unsupported_format", {"reason": "read_error"})
    return stats


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _reconcile_usage(connection, session_id: int, record: dict[str, Any]) -> None:
    turn_id = record.get("turn_id")
    usage = record.get("usage")
    values = _usage_values(usage) if isinstance(usage, dict) else None
    if not isinstance(turn_id, str) or values is None:
        raise ValueError("ambiguous_usage")
    _update_usage(connection, session_id, turn_id, values, "transcript:fixture-v1")


def _update_usage(connection, session_id: int, turn_id: str, values: dict[str, int | None], source: str) -> None:
    row = connection.execute("SELECT id, usage_source FROM turns WHERE session_id=? AND codex_turn_id=?", (session_id, turn_id)).fetchone()
    if row is None or row[1] is not None:
        return
    assignments = ", ".join(f"{field}=?" for field in USAGE_FIELDS)
    connection.execute(f"UPDATE turns SET {assignments}, usage_source=? WHERE id=?", (*[values[field] for field in USAGE_FIELDS], source, row[0]))


def _reconcile_error(connection, session_id: int, record: dict[str, Any]) -> None:
    turn_id = record.get("turn_id")
    error = record.get("error_message")
    if not isinstance(turn_id, str) or not isinstance(error, str) or not error:
        raise ValueError("ambiguous_error")
    row = connection.execute("SELECT id, error_message FROM turns WHERE session_id=? AND codex_turn_id=?", (session_id, turn_id)).fetchone()
    if row is not None and row[1] is None:
        connection.execute("UPDATE turns SET error_message=?, lifecycle_status='failed' WHERE id=?", (error[:65536], row[0]))
