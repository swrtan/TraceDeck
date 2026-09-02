import json

from tracedeck.db import connect, upsert_session, upsert_turn
from tracedeck.integrations.codex_transcript import reconcile_transcript


def test_supported_fixture_reconciles_only_nullable_usage(tmp_path):
    db = connect(tmp_path / "trace.db")
    session_id = upsert_session(db, "s", created_at="t", updated_at="t")
    turn_id = upsert_turn(db, session_id, "t", created_at="t", updated_at="t")
    path = tmp_path / "fixture.jsonl"
    path.write_text(json.dumps({"tracedeck_record_version": 1, "kind": "turn_usage", "turn_id": "t", "usage": {"input_tokens": 1, "cached_input_tokens": 2, "cache_write_input_tokens": 3, "output_tokens": 4, "reasoning_output_tokens": 5, "total_tokens": 15}}) + "\n", encoding="utf-8")

    assert reconcile_transcript(path, db, session_id, "fixture-test") == {"recognized": 1, "unsupported": 0}
    assert tuple(db.execute("SELECT input_tokens, total_tokens, usage_source FROM turns WHERE id=?", (turn_id,)).fetchone()) == (1, 15, "transcript:fixture-v1")
    before = path.read_bytes()
    assert reconcile_transcript(path, db, session_id, "fixture-test")["recognized"] == 1
    assert path.read_bytes() == before
    db.close()


def test_unknown_transcript_is_recorded_without_guesses(tmp_path):
    db = connect(tmp_path / "trace.db")
    session_id = upsert_session(db, "s", created_at="t", updated_at="t")
    upsert_turn(db, session_id, "t", created_at="t", updated_at="t")
    path = tmp_path / "unknown.jsonl"
    path.write_text('{"type":"turn.completed","usage":{"total_tokens":99}}\n', encoding="utf-8")

    assert reconcile_transcript(path, db, session_id, "0.151.0-alpha.7.2") == {"recognized": 0, "unsupported": 1}
    assert db.execute("SELECT total_tokens FROM turns").fetchone()[0] is None
    assert db.execute("SELECT kind FROM maintenance_events").fetchone()[0] == "unsupported_format"
    db.close()


def test_codex_0152_token_count_reconciles_last_usage_per_turn(tmp_path):
    db = connect(tmp_path / "trace.db")
    session_id = upsert_session(db, "s", created_at="t", updated_at="t")
    first_turn = upsert_turn(db, session_id, "turn-1", created_at="t", updated_at="t")
    second_turn = upsert_turn(db, session_id, "turn-2", created_at="t", updated_at="t")
    usage = {"input_tokens": 11, "cached_input_tokens": 7, "cache_write_input_tokens": 0,
             "output_tokens": 5, "reasoning_output_tokens": 2, "total_tokens": 16}
    cumulative = {"input_tokens": 22, "cached_input_tokens": 14, "cache_write_input_tokens": 0,
                  "output_tokens": 10, "reasoning_output_tokens": 4, "total_tokens": 32}
    path = tmp_path / "codex-0152.jsonl"
    records = [
        {"type": "session_meta", "payload": {"cli_version": "0.152.0", "session_id": "s"}},
        {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": usage, "total_token_usage": cumulative}}},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    assert reconcile_transcript(path, db, session_id, None) == {"recognized": 1, "unsupported": 0}
    assert tuple(db.execute("SELECT input_tokens, total_tokens, usage_source FROM turns WHERE id=?", (first_turn,)).fetchone()) == (11, 16, "transcript:codex-0.152.x")
    assert db.execute("SELECT total_tokens FROM turns WHERE id=?", (second_turn,)).fetchone()[0] is None
    db.close()
