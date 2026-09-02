"""Fail-open command entry point executed by Codex lifecycle hooks."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

from .config import load_config
from .spool import AtomicSpool, HookEnvelope, MAX_EVENT_BYTES


HOOK_EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"}


def neutral_output(event_type: str) -> None:
    if event_type == "Stop":
        sys.stdout.write('{"continue":true}\n')
        sys.stdout.flush()


def capture(event_type: str, stdin=None) -> int:
    """Capture one hook input without blocking or exposing model-visible content."""

    if event_type not in HOOK_EVENTS:
        return 2
    if os.environ.get("TRACEDECK_ENABLED", "1").strip() == "0":
        neutral_output(event_type)
        return 0

    stream = sys.stdin.buffer if stdin is None else stdin
    raw = stream.read(MAX_EVENT_BYTES + 1)
    if len(raw) <= MAX_EVENT_BYTES:
        try:
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("hook input must be an object")
            config = load_config()
            envelope = HookEnvelope(
                event_type=event_type,
                data=data,
                observed_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                monotonic_ns=time.monotonic_ns(),
                codex_version=data.get("codex_version"),
            )
            AtomicSpool(config.data_dir / "spool").write(envelope)
        except Exception:
            # Capture is fail-open: never change Codex's lifecycle because TraceDeck failed.
            pass
    neutral_output(event_type)
    return 0

