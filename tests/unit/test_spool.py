import json

import pytest

from tracedeck.spool import AtomicSpool, HookEnvelope, OversizedEvent


def envelope(event_id="event-1", data=None):
    return HookEnvelope("UserPromptSubmit", data or {"prompt": "hello"}, "2026-09-01T00:00:00Z", 1, event_id=event_id)


def test_write_is_atomic_and_drain_is_ordered(tmp_path):
    spool = AtomicSpool(tmp_path / "spool")
    spool.write(envelope("a"))
    spool.write(envelope("b"))
    assert not list(spool.pending.glob(".*.tmp"))
    received = []
    assert spool.drain(received.append) == (2, 0)
    assert [item.event_id for item in received] == ["a", "b"]
    assert not list(spool.pending.glob("*.json"))


def test_malformed_event_is_quarantined_and_later_event_survives(tmp_path):
    spool = AtomicSpool(tmp_path / "spool")
    (spool.pending / "00000000000000000001-bad.json").write_text("not json", encoding="utf-8")
    spool.write(envelope("good"))
    received = []
    assert spool.drain(received.append) == (1, 1)
    assert [item.event_id for item in received] == ["good"]
    assert len(list(spool.quarantine.glob("*.json"))) == 1


def test_oversized_event_is_rejected_and_recorded(tmp_path):
    spool = AtomicSpool(tmp_path / "spool")
    with pytest.raises(OversizedEvent):
        spool.write(envelope(data={"payload": "x" * (8 * 1024 * 1024)}))
    incident = next(spool.incidents.glob("*.json"))
    assert json.loads(incident.read_text(encoding="utf-8"))["kind"] == "oversized_event"

