"""Bounded, source-authoritative tool attribution helpers."""

from __future__ import annotations

from typing import Any


def canonical_mcp_attribution(tool_name: str | None) -> dict[str, Any]:
    """Parse only the documented ``mcp__server__tool`` hook identity."""

    if not isinstance(tool_name, str):
        return {"mcp_server": None, "mcp_tool": None, "attribution_source": None}
    parts = tool_name.split("__")
    if len(parts) != 3 or parts[0] != "mcp" or not parts[1] or not parts[2] or any("\n" in part or "\r" in part for part in parts):
        return {"mcp_server": None, "mcp_tool": None, "attribution_source": None}
    return {"mcp_server": parts[1], "mcp_tool": parts[2], "attribution_source": "hook:canonical_tool_name"}
