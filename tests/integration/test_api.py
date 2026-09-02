from fastapi.testclient import TestClient
import time

from tracedeck.config import Config
from tracedeck.db import upsert_session, upsert_tool_call, upsert_turn
from tracedeck.hook_entry import capture
from tracedeck.normalize import project_spool
from tracedeck.spool import AtomicSpool
from tracedeck.web.app import create_app


def test_read_only_api_summary_list_and_detail(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "s", created_at="t", updated_at="t")
        turn_id = upsert_turn(db, session_id, "t", user_prompt="hello", lifecycle_status="completed", created_at="t", updated_at="t")
        headers = {"host": "127.0.0.1"}
        health = client.get("/api/health", headers=headers)
        assert health.status_code == 200
        assert health.json()["time_contract"]["storage_timezone"] == "UTC"
        assert health.json()["time_contract"]["display_timezone"] == "browser-local"
        assert health.json()["retention"]["unknown_timestamps"]
        summary = client.get("/api/summary", headers=headers).json()
        assert summary["turns"]["total"] == 1
        assert summary["token_coverage"]["sum"] is None
        assert summary["duration_coverage"]["median_ms"] is None
        assert client.get("/api/turns?page_size=201", headers=headers).status_code == 422
        item = client.get("/api/turns?page=1&page_size=10", headers=headers).json()["items"][0]
        assert item["prompt_preview"] == "hello"
        detail = client.get(f"/api/turns/{turn_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["usage"]["total_tokens"]["quality"] == "unavailable"
        assert detail.json()["time_meaning"] == "observer time"
        assert client.post("/api/summary", headers=headers).status_code == 405


def test_summary_sums_known_total_tokens_and_preserves_unknowns(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "token-session", created_at="t", updated_at="t")
        upsert_turn(db, session_id, "known-1", total_tokens=1200, duration_ms=100, created_at="t", updated_at="t")
        upsert_turn(db, session_id, "known-2", total_tokens=800, duration_ms=300, created_at="t", updated_at="t")
        upsert_turn(db, session_id, "unknown", total_tokens=None, duration_ms=None, created_at="t", updated_at="t")

        summary = client.get("/api/summary", headers={"host": "127.0.0.1"}).json()

        assert summary["token_coverage"] == {"known": 2, "total": 3, "sum": 2000}
        assert summary["duration_coverage"]["median_ms"] == 200


def test_explicit_refresh_runs_local_import_and_spool_drain(tmp_path):
    app = create_app(Config(data_dir=tmp_path, codex_sessions_dir=None))
    with TestClient(app) as client:
        response = client.post("/api/refresh", headers={"host": "127.0.0.1"})
        assert response.status_code == 200
        assert "desktop_import" in response.json()
        assert "last_drain" in response.json()


def test_turn_detail_preserves_nullable_zero_partial_and_redaction_quality(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "quality-session", created_at="t", updated_at="t")
        turn_id = upsert_turn(
            db,
            session_id,
            "quality-turn",
            lifecycle_status="partial",
            duration_ms=None,
            duration_source=None,
            input_tokens=0,
            usage_source="transcript:fixture-v1",
            created_at="t",
            updated_at="t",
        )
        upsert_tool_call(
            db,
            turn_id,
            "call-1",
            "Bash",
            lifecycle_status="partial",
            duration_ms=None,
            duration_source=None,
            arguments_redacted=1,
            arguments_truncated=1,
            pre_observed=0,
            post_observed=1,
            created_at="t",
            updated_at="t",
        )

        detail = client.get(f"/api/turns/{turn_id}", headers={"host": "127.0.0.1"}).json()

        assert detail["duration"] == {"value": None, "source": None, "quality": "unavailable"}
        assert detail["usage"]["input_tokens"] == {"value": 0, "source": "transcript:fixture-v1", "quality": "known"}
        assert detail["usage"]["total_tokens"]["quality"] == "unavailable"
        assert detail["tool_calls"][0]["duration"]["quality"] == "unavailable"
        assert detail["tool_calls"][0]["partial"] is True
        assert detail["tool_calls"][0]["redacted"] is True
        assert detail["tool_calls"][0]["truncated"] is True


def test_non_local_host_is_rejected(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path))) as client:
        assert client.get("/api/health", headers={"host": "example.com"}).status_code == 400


def test_old_open_turn_is_marked_stale_instead_of_active(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "stale-session", created_at="2000-01-01T00:00:00+00:00", updated_at="2000-01-01T00:00:00+00:00")
        turn_id = upsert_turn(db, session_id, "stale-turn", lifecycle_status="in_progress", started_at="2000-01-01T00:00:00+00:00", updated_at="2000-01-01T00:00:00+00:00")
        headers = {"host": "127.0.0.1"}
        summary = client.get("/api/summary", headers=headers).json()
        item = client.get("/api/turns?page_size=50", headers=headers).json()["items"][0]
        detail = client.get(f"/api/turns/{turn_id}", headers=headers).json()
        assert summary["turns"]["stale_in_progress"] == 1
        assert item["stale"] is True
        assert detail["stale"] is True


def test_background_collector_makes_new_event_visible_without_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACEDECK_DATA_DIR", str(tmp_path))
    with TestClient(create_app(Config(data_dir=tmp_path))) as client:
        capture("SessionStart", __import__("io").BytesIO(b'{"session_id":"live"}'))
        capture("UserPromptSubmit", __import__("io").BytesIO(b'{"session_id":"live","turn_id":"turn-live","prompt":"hello"}'))
        capture("Stop", __import__("io").BytesIO(b'{"session_id":"live","turn_id":"turn-live","last_assistant_message":"done"}'))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if client.get("/api/summary", headers={"host": "127.0.0.1"}).json()["turns"]["total"] == 1:
                break
            time.sleep(0.05)
        assert client.get("/api/summary", headers={"host": "127.0.0.1"}).json()["turns"]["total"] == 1


def test_turn_filters_have_deterministic_date_boundaries_and_unknowns_are_excluded(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "filter-session", created_at="2026-09-02T00:00:00+00:00", updated_at="2026-09-02T00:00:00+00:00")
        upsert_turn(db, session_id, "first", source="hook", started_at="2026-09-01T23:59:59.000+00:00", total_tokens=10, duration_ms=100, created_at="2026-09-01T23:59:59.000+00:00", updated_at="2026-09-01T23:59:59.000+00:00")
        upsert_turn(db, session_id, "boundary", source="hook", started_at="2026-09-02T12:00:00.000+00:00", total_tokens=20, duration_ms=200, created_at="2026-09-02T12:00:00.000+00:00", updated_at="2026-09-02T12:00:00.000+00:00")
        upsert_turn(db, session_id, "unknown-time", source="hook", total_tokens=30, duration_ms=300, created_at="2026-09-02T12:00:00.000+00:00", updated_at="2026-09-02T12:00:00.000+00:00")
        headers = {"host": "127.0.0.1"}

        response = client.get("/api/turns?start_date=2026-09-02&end_date=2026-09-02&min_tokens=15&max_duration_ms=250", headers=headers)
        body = response.json()
        assert response.status_code == 200
        assert [item["codex_turn_id"] for item in body["items"]] == ["boundary"]
        assert body["active_filters"] == {"start_date": "2026-09-02", "end_date": "2026-09-02", "timezone": "browser-local", "unknown_timestamps_match": False}

        assert client.get("/api/turns?start_date=not-a-date", headers=headers).status_code == 422
        assert client.get("/api/turns?start_date=2026-09-03&end_date=2026-09-02", headers=headers).status_code == 422

        summary = client.get("/api/summary?start_date=2026-09-02&end_date=2026-09-02", headers=headers).json()
        assert summary["turns"]["total"] == 2
        assert summary["token_coverage"]["sum"] == 50
        assert summary["duration_coverage"]["median_ms"] == 250
        assert summary["active_filters"]["summary_fallback"] == "created_at when started_at is unavailable"


def test_analytics_states_and_range_preserve_known_zero(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "analytics-session", created_at="2026-09-02T00:00:00+00:00", updated_at="2026-09-02T00:00:00+00:00")
        upsert_turn(db, session_id, "known-zero", model="local", started_at="2026-09-02T12:00:00.000+00:00", total_tokens=0, created_at="2026-09-02T12:00:00.000+00:00", updated_at="2026-09-02T12:00:00.000+00:00")
        upsert_turn(db, session_id, "unknown-token", model="local", started_at="2026-09-02T13:00:00.000+00:00", created_at="2026-09-02T13:00:00.000+00:00", updated_at="2026-09-02T13:00:00.000+00:00")
        headers = {"host": "127.0.0.1"}

        body = client.get("/api/analytics?start_date=2026-09-02&end_date=2026-09-02", headers=headers).json()
        assert body["state"] == "partial"
        assert body["coverage"] == {"known_token_turns": 1, "total_turns": 2}
        assert body["daily"][0]["tokens"] == 0
        assert client.get("/api/analytics?start_date=bad", headers=headers).status_code == 422

        empty = client.get("/api/analytics?start_date=2026-09-03&end_date=2026-09-03", headers=headers).json()
        assert empty["state"] == "no_data"


def test_detail_exposes_canonical_mcp_attribution_and_leaves_regular_tools_nullable(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "mcp-session", created_at="t", updated_at="t")
        turn_id = upsert_turn(db, session_id, "mcp-turn", created_at="t", updated_at="t")
        upsert_tool_call(db, turn_id, "mcp-call", "mcp__fixture__read", created_at="t", updated_at="t")
        upsert_tool_call(db, turn_id, "shell-call", "Bash", created_at="t", updated_at="t")
        tools = client.get(f"/api/turns/{turn_id}", headers={"host": "127.0.0.1"}).json()["tool_calls"]
        by_name = {tool["tool_name"]: tool for tool in tools}
        assert by_name["mcp__fixture__read"]["mcp_server"] == "fixture"
        assert by_name["mcp__fixture__read"]["mcp_tool"] == "read"
        assert by_name["Bash"]["mcp_server"] is None
        assert by_name["Bash"]["attribution_source"] is None


def test_prompt_unavailable_reason_is_explicit_for_automatic_turns(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "reason-session", created_at="t", updated_at="t")
        compact_id = upsert_turn(db, session_id, "auto-compact-1", created_at="t", updated_at="t")
        review_id = upsert_turn(db, session_id, "review", model="codex-auto-review", created_at="t", updated_at="t")
        headers = {"host": "127.0.0.1"}
        compact = client.get(f"/api/turns/{compact_id}", headers=headers).json()
        review = client.get(f"/api/turns/{review_id}", headers=headers).json()
        assert compact["prompt_status"]["reason"].startswith("Auto-compact")
        assert review["prompt_status"]["reason"].startswith("Auto-review")


def test_sessions_and_compare_support_sessions_and_historical_ranges(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        first = upsert_session(db, "session-a", created_at="2026-09-01", updated_at="2026-09-01")
        second = upsert_session(db, "session-b", created_at="2026-09-02", updated_at="2026-09-02")
        upsert_turn(db, first, "turn-a", started_at="2026-09-01T10:00:00+00:00", total_tokens=10, duration_ms=100, created_at="2026-09-01", updated_at="2026-09-01")
        upsert_turn(db, second, "turn-b", started_at="2026-09-02T10:00:00+00:00", total_tokens=20, duration_ms=200, created_at="2026-09-02", updated_at="2026-09-02")
        headers = {"host": "127.0.0.1"}
        assert client.get("/api/sessions", headers=headers).json()["items"][0]["turns"] == 1
        session_compare = client.get(f"/api/compare?session_a={first}&session_b={second}", headers=headers)
        assert session_compare.status_code == 200
        assert session_compare.json()["a"]["known_tokens"] == 10
        range_compare = client.get("/api/compare?start_a=2026-09-01&end_a=2026-09-01&start_b=2026-09-02&end_b=2026-09-02", headers=headers)
        assert range_compare.status_code == 200
        assert range_compare.json()["b"]["known_tokens"] == 20


def test_csv_exports_are_bounded_and_separate(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "csv-session", created_at="t", updated_at="t")
        turn_id = upsert_turn(db, session_id, "csv-turn", user_prompt="export me", assistant_response="final answer", created_at="t", updated_at="t")
        upsert_tool_call(db, turn_id, "csv-call", "mcp__fixture__read", created_at="t", updated_at="t")
        headers = {"host": "127.0.0.1"}
        turns = client.get("/api/export/turns.csv?max_rows=1", headers=headers)
        tools = client.get("/api/export/tools.csv?max_rows=1", headers=headers)
        assert turns.status_code == 200
        assert "export me" in turns.text
        assert "final answer" in turns.text
        assert tools.status_code == 200
        assert "mcp__fixture__read" in tools.text


def test_csv_export_neutralizes_spreadsheet_formulas(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "csv-safe-session", created_at="t", updated_at="t")
        upsert_turn(db, session_id, "csv-safe-turn", user_prompt="=HYPERLINK(\"https://example.invalid\")", created_at="t", updated_at="t")
        response = client.get("/api/export/turns.csv?max_rows=1", headers={"host": "127.0.0.1"})
        assert response.status_code == 200
        assert "'=HYPERLINK" in response.text


def test_file_csv_export_includes_observation_and_write_metadata(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        session_id = upsert_session(db, "file-csv-session", created_at="t", updated_at="t")
        turn_id = upsert_turn(db, session_id, "file-csv-turn", created_at="t", updated_at="t")
        db.execute("INSERT INTO file_observations(turn_id, path, access_kind, size_bytes, observed_at, source, confidence, source_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (turn_id, "src/app.py", "observed", 128, "t", "hook:structured_tool_input", "high", "file-csv-event"))
        response = client.get("/api/export/files.csv?max_rows=1", headers={"host": "127.0.0.1"})
        assert response.status_code == 200
        assert "src/app.py" in response.text
        assert "observed" in response.text
        assert "128" in response.text
