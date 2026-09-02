from fastapi.testclient import TestClient

from tracedeck.config import Config
from tracedeck.db import upsert_session, upsert_turn
from tracedeck.hooks import install
from tracedeck.web.app import _collector_state
from tracedeck.web.app import create_app


def test_health_exposes_local_capacity_and_incident_state(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path))) as client:
        response = client.get("/api/health", headers={"host": "127.0.0.1"})
        assert response.status_code == 200
        body = response.json()
        assert body["capture_enabled"] is True
        assert body["data_directory_configured"] is True
        assert body["spool_limit_bytes"] == 32 * 1024 * 1024
        assert "incidents" in body


def test_dashboard_explains_coverage_and_privacy_limits(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path))) as client:
        body = client.get("/", headers={"host": "127.0.0.1"}).text
        script = client.get("/app.js", headers={"host": "127.0.0.1"}).text
        assert "Hosted tools may be missing" in body
        assert "Hosted tools may be missing" in script
        assert "Secret masking can miss" in script
        assert "Maintenance incidents" in script


def test_health_reports_empty_partial_and_hook_setup_states(monkeypatch, tmp_path):
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    install(codex_home)
    app = create_app(Config(data_dir=tmp_path / "data", codex_sessions_dir=tmp_path / "sessions"))
    with TestClient(app) as client:
        headers = {"host": "127.0.0.1"}
        empty = client.get("/api/health", headers=headers).json()
        assert empty["state"]["kind"] == "empty"
        assert empty["hook_installation"]["installed"] is True
        assert empty["hook_installation"]["installed_count"] == 6

        session_id = upsert_session(app.state.db, "partial-session", created_at="t", updated_at="t")
        upsert_turn(app.state.db, session_id, "partial-turn", lifecycle_status="partial", created_at="t", updated_at="t")
        partial = client.get("/api/health", headers=headers).json()
        assert partial["state"]["kind"] == "partial"


def test_health_reports_disabled_capture_without_hiding_existing_data(tmp_path):
    app = create_app(Config(data_dir=tmp_path, enabled=False, codex_sessions_dir=tmp_path / "sessions"))
    with TestClient(app) as client:
        body = client.get("/api/health", headers={"host": "127.0.0.1"}).json()
        assert body["state"]["kind"] == "capture_disabled"
        assert body["capture_enabled"] is False


def test_collector_state_contract_has_actionable_kinds():
    assert _collector_state(integrity="ok", enabled=True, hooks_ready=True, last_drain_at="2026-01-01T00:00:00+00:00", total_turns=0, partial_turns=0)["kind"] == "stale"
    assert _collector_state(integrity="ok", enabled=True, hooks_ready=True, last_drain_at=None, total_turns=0, partial_turns=0)["kind"] == "stale"
    assert _collector_state(integrity="ok", enabled=True, hooks_ready=True, last_drain_at="2099-01-01T00:00:00+00:00", total_turns=0, partial_turns=0)["kind"] == "empty"
    assert _collector_state(integrity="ok", enabled=True, hooks_ready=True, last_drain_at="2099-01-01T00:00:00+00:00", total_turns=1, partial_turns=1)["kind"] == "partial"
    assert _collector_state(integrity="ok", enabled=True, hooks_ready=True, last_drain_at="2099-01-01T00:00:00+00:00", total_turns=1, partial_turns=0)["kind"] == "healthy"
