"""Bounded, file-based recovery spool for hook events."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


ENVELOPE_VERSION = 1
MAX_EVENT_BYTES = 8 * 1024 * 1024
MAX_SPOOL_BYTES = 32 * 1024 * 1024
MAX_INCIDENT_BYTES = 4 * 1024


class SpoolError(RuntimeError):
    """Base error for bounded spool operations."""


class OversizedEvent(SpoolError):
    """The encoded event exceeded the transient parser ceiling."""


class SpoolFull(SpoolError):
    """The bounded recovery spool cannot accept another event."""


@dataclass(frozen=True, slots=True)
class HookEnvelope:
    event_type: str
    data: dict[str, Any]
    observed_at: str
    monotonic_ns: int
    codex_version: str | None = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    envelope_version: int = ENVELOPE_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        return (json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class AtomicSpool:
    """Write and drain one JSON event file at a time."""

    def __init__(self, root: Path, max_bytes: int = MAX_SPOOL_BYTES) -> None:
        self.root = Path(root)
        self.pending = self.root / "pending"
        self.quarantine = self.root / "quarantine"
        self.incidents = self.root / "incidents"
        self.max_bytes = max_bytes
        for directory in (self.pending, self.quarantine, self.incidents):
            directory.mkdir(parents=True, exist_ok=True)

    def usage_bytes(self) -> int:
        return sum(path.stat().st_size for directory in (self.pending, self.quarantine, self.incidents)
                   for path in directory.glob("*") if path.is_file())

    def write(self, envelope: HookEnvelope) -> Path:
        payload = envelope.to_bytes()
        if len(payload) > MAX_EVENT_BYTES:
            self._incident("oversized_event", {"event_id": envelope.event_id, "size_bytes": len(payload)})
            raise OversizedEvent("event exceeds 8 MiB")
        if self.usage_bytes() + len(payload) > self.max_bytes:
            self._incident("spool_full", {"event_id": envelope.event_id})
            raise SpoolFull("recovery spool limit reached")

        name = f"{time.time_ns():020d}-{envelope.event_id}-{uuid.uuid4().hex}.json"
        temporary = self.pending / f".{name}.tmp"
        destination = self.pending / name
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination

    def drain(self, handler: Callable[[HookEnvelope], None]) -> tuple[int, int]:
        """Handle pending events in order; return (processed, quarantined)."""

        processed = quarantined = 0
        seen_ids: set[str] = set()
        for path in sorted(self.pending.glob("*.json")):
            try:
                raw = path.read_bytes()
                if len(raw) > MAX_EVENT_BYTES:
                    raise OversizedEvent("event exceeds 8 MiB")
                value = json.loads(raw)
                envelope = HookEnvelope(
                    event_type=value["event_type"], data=value["data"],
                    observed_at=value["observed_at"], monotonic_ns=int(value["monotonic_ns"]),
                    codex_version=value.get("codex_version"), event_id=value["event_id"],
                    envelope_version=int(value["envelope_version"]), metadata=value.get("metadata", {}),
                )
                if envelope.envelope_version != ENVELOPE_VERSION or envelope.event_id in seen_ids:
                    raise ValueError("duplicate or unsupported envelope")
                handler(envelope)
                seen_ids.add(envelope.event_id)
                path.unlink()
                processed += 1
            except Exception as exc:
                destination = self.quarantine / path.name
                os.replace(path, destination)
                self._incident("malformed_event", {"file": path.name, "error_type": type(exc).__name__})
                quarantined += 1
        return processed, quarantined

    def _incident(self, kind: str, details: dict[str, Any]) -> None:
        payload = json.dumps({"kind": kind, **details}, separators=(",", ":"))[:MAX_INCIDENT_BYTES - 1] + "\n"
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        path = self.incidents / name
        try:
            path.write_text(payload, encoding="utf-8")
        except OSError:
            # Fail-open: incident reporting must never block event capture.
            return

