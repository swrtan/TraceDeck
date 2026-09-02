import io
import json
import sqlite3
import pytest

from tracedeck.config import Config
from tracedeck.db import connect
from tracedeck.hook_entry import capture
from tracedeck.hooks import install, uninstall
from tracedeck.spool import AtomicSpool, HookEnvelope, SpoolFull
from tracedeck.web.app import create_app
from fastapi.testclient import TestClient


def test_disabled_capture_is_fail_open_and_has_no_files(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACEDECK_ENABLED", "0")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("TRACEDECK_DATA_DIR", str(data_dir))

    assert capture("UserPromptSubmit", io.BytesIO(b'{"prompt":"not persisted"}')) == 0
    assert not data_dir.exists()


def test_corrupt_database_fails_closed_at_startup(tmp_path):
    path = tmp_path / "trace.db"
    path.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        connect(path)


def test_locked_database_does_not_bypass_sqlite_lock(tmp_path):
    path = tmp_path / "trace.db"
    first = connect(path)
    second = sqlite3.connect(path, timeout=0)
    try:
        first.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second.execute("BEGIN IMMEDIATE")
    finally:
        first.rollback()
        second.close()
        first.close()


def test_spool_full_is_bounded_and_visible(tmp_path):
    spool = AtomicSpool(tmp_path / "spool", max_bytes=1)
    envelope = HookEnvelope("UserPromptSubmit", {"prompt": "hello"}, "now", 1)

    with pytest.raises(SpoolFull):
        spool.write(envelope)
    incident = next(spool.incidents.glob("*.json"))
    assert json.loads(incident.read_text(encoding="utf-8"))["kind"] == "spool_full"


def test_installer_failure_leaves_existing_config_and_uninstall_preserves_it(monkeypatch, tmp_path):
    home = tmp_path / ".codex"
    home.mkdir()
    path = home / "hooks.json"
    original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "custom"}]}]}}
    path.write_text(json.dumps(original), encoding="utf-8")

    def fail_write(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr("tracedeck.hooks._write_atomic", fail_write)
    with pytest.raises(OSError):
        install(home)
    assert json.loads(path.read_text(encoding="utf-8")) == original

    monkeypatch.undo()
    install(home)
    uninstall(home)
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_api_security_headers_and_external_asset_free_dashboard(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.get("/", headers={"host": "127.0.0.1"})
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "https://" not in response.text
        assert "http://" not in response.text

        assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
