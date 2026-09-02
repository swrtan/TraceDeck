# S7.2 plugin/MCP attribution investigation

Status: investigated; MCP attribution is feasible, plugin ownership attribution is conditional and not approved for MVP implementation.
Date: 2026-09-02

## Question

Can TraceDeck identify MCP servers, MCP tools, and the owning plugin using the existing passive Codex lifecycle-hook architecture without starting, proxying, or controlling Codex sessions?

## Official source findings

The current OpenAI Hooks documentation states that `PreToolUse` and `PostToolUse` cover MCP tools and that the hook payload contains `tool_name` and `tool_use_id`. The canonical MCP tool name is shown as `mcp__server__tool`, and the matcher examples use names such as `mcp__filesystem__read_file`.

Source: https://learn.chatgpt.com/docs/hooks

The current MCP documentation states that Codex configures ordinary servers under `mcp_servers.<server-name>`. It also states that installed plugins can bundle MCP servers and that plugin-owned server configuration appears under `plugins.<plugin>.mcp_servers.<server>`.

Source: https://learn.chatgpt.com/docs/extend/mcp

The App Server documentation exposes `mcpServerStatus/list`, which can list MCP servers, tools, resources, and auth status. It also lists plugin discovery/read methods, but marks plugin methods as under development and says they should not be called from production clients.

Source: https://learn.chatgpt.com/docs/app-server

## Feasibility result

### MCP server and tool attribution — feasible

For a hook event whose `tool_name` exactly matches the documented canonical form, TraceDeck can parse:

- `mcp_server`: the middle namespace segment
- `mcp_tool`: the remaining tool name segment
- `attribution_source`: `hook:canonical_tool_name`

This does not require an external service, an API key, an App Server connection, or a new runtime dependency. The existing `tool_calls.tool_name` value is already captured and bounded. The parser must fail closed for names that do not match the canonical pattern and leave the derived fields null.

This attribution applies only to MCP calls that pass through the local tool hook path. Hosted tools remain outside the hook coverage documented by OpenAI and must remain visibly unknown/unavailable.

### Plugin ownership attribution — not reliably available from the hook alone

The hook payload identifies the canonical MCP tool name, not the plugin that supplied the server. A local config lookup may find a matching `plugins.<plugin>.mcp_servers.<server>` entry, but this is not sufficient as a universal runtime attribution contract because:

- effective configuration can be layered or changed after the event;
- server names may collide across configuration scopes or plugins;
- the hook event does not carry a plugin id or manifest version;
- plugin discovery/read methods in App Server are currently documented as under development;
- using App Server would change TraceDeck from a passive observer into a client that depends on a controllable session protocol.

Therefore plugin ownership must remain null unless a future supported passive source provides an explicit plugin identifier and version for the tool invocation.

## Privacy and architecture constraints

TraceDeck may inspect local configuration only in a future, separately approved packet and only for bounded names/identifiers. It must never persist MCP commands, URLs, OAuth data, bearer tokens, authorization headers, environment values, or raw MCP responses. It must not launch MCP servers or call `mcpServerStatus/list` as part of normal collection.

## Recommended implementation boundary

Split the original S7.2 item:

1. `P7.1a` — derive and display canonical MCP server/tool identifiers from local hook events. Add parser fixtures, malformed-name tests, nullable API fields, and coverage wording.
2. `S7.2b` — plugin ownership source investigation only. Close as unsupported for the MVP unless a stable passive source appears.

Do not add plugin columns or config-based ownership inference in `P7.1a`. The server/tool parser is safe to implement independently because it is directly supported by the hook contract.

## Decision

MCP server/tool attribution: **implementable with current MVP architecture**.

Plugin attribution: **not implementable as a trustworthy MVP feature today**. Keep it as an explicitly unsupported/unknown state and revisit only after a documented passive source becomes available.
