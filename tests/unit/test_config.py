from pathlib import Path

import pytest

from tracedeck.config import ConfigurationError, load_config


def test_defaults_are_safe_and_side_effect_free(tmp_path):
    config = load_config({"TRACEDECK_DATA_DIR": str(tmp_path / "data")})

    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.enabled is True
    assert config.max_storage_mb == 1024
    assert config.log_level == "INFO"
    assert config.data_dir == tmp_path / "data"
    assert config.codex_sessions_dir == Path.home() / ".codex" / "sessions"
    assert not config.data_dir.exists()


def test_disabled_recording_is_parsed_without_side_effects(tmp_path):
    config = load_config({
        "TRACEDECK_DATA_DIR": str(tmp_path / "data"),
        "TRACEDECK_ENABLED": "0",
    })

    assert config.enabled is False
    assert not config.data_dir.exists()


@pytest.mark.parametrize("name,value", [
    ("TRACEDECK_PORT", "0"),
    ("TRACEDECK_PORT", "65536"),
    ("TRACEDECK_MAX_STORAGE_MB", "99"),
    ("TRACEDECK_MAX_STORAGE_MB", "10241"),
])
def test_numeric_ranges_are_validated(name, value, tmp_path):
    with pytest.raises(ConfigurationError):
        load_config({"TRACEDECK_DATA_DIR": str(tmp_path), name: value})


def test_only_loopback_host_is_allowed(tmp_path):
    with pytest.raises(ConfigurationError, match="127.0.0.1"):
        load_config({"TRACEDECK_DATA_DIR": str(tmp_path), "TRACEDECK_HOST": "0.0.0.0"})


def test_log_level_is_normalized(tmp_path):
    config = load_config({"TRACEDECK_DATA_DIR": str(tmp_path), "TRACEDECK_LOG_LEVEL": "debug"})
    assert config.log_level == "DEBUG"


def test_desktop_sessions_root_can_be_configured(tmp_path):
    config = load_config({
        "TRACEDECK_DATA_DIR": str(tmp_path / "data"),
        "TRACEDECK_CODEX_SESSIONS_DIR": str(tmp_path / "sessions"),
    })
    assert config.codex_sessions_dir == tmp_path / "sessions"
