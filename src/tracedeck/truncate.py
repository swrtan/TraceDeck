"""Deterministic UTF-8 byte-size truncation."""

from __future__ import annotations


def truncate_text(value: str | None, limit: int) -> tuple[str | None, bool, int | None]:
    if value is None:
        return None, False, None
    encoded = value.encode("utf-8")
    original_size = len(encoded)
    if original_size <= limit:
        return value, False, original_size
    marker = "\n…[truncated]…\n".encode("utf-8")
    available = max(0, limit - len(marker))
    head = available // 2
    tail = available - head
    result = (encoded[:head] + marker + encoded[-tail:] if tail else encoded[:head] + marker)[:limit]
    return result.decode("utf-8", errors="ignore"), True, original_size

