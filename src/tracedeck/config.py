"""Typed, side-effect-free TraceDeck configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_STORAGE_MB = 1024
MIN_MAX_STORAGE_MB = 100
MAX_MAX_STORAGE_MB = 10240


class ConfigurationError(ValueError):
    """Raised when an environment setting is invalid."""


def _default_data_dir() -> Path:
    """Return the platform-local data path without creating it."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "TraceDeck"
    return Path.home() / ".tracedeck"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or not value.strip() else value.strip()


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"TRACEDECK_ENABLED must be a boolean, got {value!r}")


def _parse_int(name: str, value: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer, got {value!r}") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return parsed


@dataclass(frozen=True, slots=True)
class Config:
    """Runtime settings. Loading this object performs no filesystem or DB I/O."""

    data_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    enabled: bool = True
    max_storage_mb: int = DEFAULT_MAX_STORAGE_MB
    log_level: str = "INFO"
    codex_sessions_dir: Path | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Config":
        values = os.environ if environ is None else environ
        raw_data_dir = values.get("TRACEDECK_DATA_DIR", str(_default_data_dir())).strip()
        if not raw_data_dir:
            raise ConfigurationError("TRACEDECK_DATA_DIR must not be empty")
        data_dir = Path(raw_data_dir)

        host = values.get("TRACEDECK_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
        if host != DEFAULT_HOST:
            raise ConfigurationError("TraceDeck must bind only to 127.0.0.1")

        port = _parse_int("TRACEDECK_PORT", values.get("TRACEDECK_PORT", str(DEFAULT_PORT)), 1, 65535)
        enabled = _parse_bool(values.get("TRACEDECK_ENABLED", "1"))
        max_storage_mb = _parse_int(
            "TRACEDECK_MAX_STORAGE_MB",
            values.get("TRACEDECK_MAX_STORAGE_MB", str(DEFAULT_MAX_STORAGE_MB)),
            MIN_MAX_STORAGE_MB,
            MAX_MAX_STORAGE_MB,
        )
        log_level = _env_from(values, "TRACEDECK_LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("TRACEDECK_LOG_LEVEL must be a standard log level")
        sessions_dir = Path(_env_from(values, "TRACEDECK_CODEX_SESSIONS_DIR", str(Path.home() / ".codex" / "sessions")))
        return cls(data_dir=data_dir, host=host, port=port, enabled=enabled,
                   max_storage_mb=max_storage_mb, log_level=log_level,
                   codex_sessions_dir=sessions_dir)


def _env_from(values: dict[str, str], name: str, default: str) -> str:
    value = values.get(name)
    return default if value is None or not value.strip() else value.strip()


def load_config(environ: dict[str, str] | None = None) -> Config:
    """Load and validate configuration from environment variables."""

    return Config.from_env(environ)
