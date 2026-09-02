"""Best-effort structured secret redaction before persistence."""

from __future__ import annotations

import re
from typing import Any


SECRET_KEY = re.compile(r"(api[_-]?key|authorization|bearer|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|private[_-]?key)", re.I)
SECRET_VALUE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+|\b(sk-[A-Za-z0-9_-]{12,})\b|\b((?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*)[^\s,;]+")
REDACTED = "[REDACTED]"


def redact(value: Any, key: str | None = None) -> tuple[Any, bool]:
    if key is not None and SECRET_KEY.search(key):
        return REDACTED, value is not None
    if isinstance(value, dict):
        result = {}
        changed = False
        for child_key, child in value.items():
            result[child_key], child_changed = redact(child, str(child_key))
            changed |= child_changed
        return result, changed
    if isinstance(value, list):
        result = []
        changed = False
        for child in value:
            redacted, child_changed = redact(child)
            result.append(redacted)
            changed |= child_changed
        return result, changed
    if isinstance(value, str):
        replaced = SECRET_VALUE.sub(lambda match: (match.group(1) or match.group(3)) + REDACTED if match.group(1) or match.group(3) else REDACTED, value)
        return replaced, replaced != value
    return value, False
