"""Safe user-level Codex hook installation and removal."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")
MARKER = "tracedeck hook-event"


class HookConfigError(ValueError):
    """The hook configuration is not a supported JSON object."""


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def hook_path(home: Path | None = None) -> Path:
    return (codex_home() if home is None else Path(home)) / "hooks.json"


def _trace_handler(event: str) -> dict[str, Any]:
    return {
        "type": "command",
        "command": f"py -3 -m tracedeck hook-event {event} --owned",
        "timeout": 3 if event == "SessionEnd" else 30,
        "statusMessage": "TraceDeck local recorder",
    }


def _trace_group(event: str) -> dict[str, Any]:
    return {"hooks": [_trace_handler(event)]}


def _is_owned(handler: Any) -> bool:
    return isinstance(handler, dict) and handler.get("type") == "command" and MARKER in str(handler.get("command", ""))


def _remove_owned(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, list):
        return value, False
    changed = False
    kept_groups = []
    for group in value:
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            kept_groups.append(group)
            continue
        new_handlers = [handler for handler in handlers if not _is_owned(handler)]
        changed |= len(new_handlers) != len(handlers)
        if new_handlers:
            updated = dict(group)
            updated["hooks"] = new_handlers
            kept_groups.append(updated)
        else:
            changed = True
    return kept_groups, changed


def merge_hooks(document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not isinstance(document, dict):
        raise HookConfigError("hooks.json root must be an object")
    result = json.loads(json.dumps(document))
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise HookConfigError("hooks must be an object")
    changed = False
    for event in EVENTS:
        groups, removed = _remove_owned(hooks.get(event, []))
        groups.append(_trace_group(event))
        hooks[event] = groups
        changed |= removed or True
    return result, changed


def remove_hooks(document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not isinstance(document, dict):
        raise HookConfigError("hooks.json root must be an object")
    result = json.loads(json.dumps(document))
    hooks = result.get("hooks", {})
    if not isinstance(hooks, dict):
        raise HookConfigError("hooks must be an object")
    changed = False
    for event in list(hooks):
        hooks[event], removed = _remove_owned(hooks[event])
        changed |= removed
        if hooks[event] == []:
            del hooks[event]
    if not hooks and "hooks" in result:
        del result["hooks"]
    return result, changed


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HookConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HookConfigError("hooks.json root must be an object")
    return value


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(home: Path | None = None, dry_run: bool = False) -> str:
    path = hook_path(home)
    current = _read(path)
    merged, _ = merge_hooks(current)
    preview = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    if dry_run:
        return preview
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    _write_atomic(path, merged)
    return str(path)


def uninstall(home: Path | None = None, dry_run: bool = False) -> str:
    path = hook_path(home)
    current = _read(path)
    result, _ = remove_hooks(current)
    if dry_run:
        return json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        if result:
            _write_atomic(path, result)
        else:
            path.unlink()
    return str(path)


def status(home: Path | None = None) -> dict[str, Any]:
    document = _read(hook_path(home))
    hooks = document.get("hooks", {}) if isinstance(document, dict) else {}
    installed = {event: any(_is_owned(handler) for group in hooks.get(event, []) if isinstance(group, dict) for handler in group.get("hooks", []) if isinstance(group.get("hooks", []), list)) for event in EVENTS}
    return {"path": str(hook_path(home)), "installed": installed, "any_installed": any(installed.values())}

