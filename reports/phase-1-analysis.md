# TraceDeck Phase 1 analysis

Status: complete, awaiting explicit Phase 2 approval  
Date: 2026-09-01  
Locally observed Codex CLI: `codex-cli 0.151.0-alpha.7.2`

## Executive conclusion

The product concept is viable as a local, passive flight recorder, with one important qualification: supported lifecycle hooks can reliably provide session and turn identifiers, prompt text, the latest assistant message when available, model, local tool names, tool identifiers, arguments, and selected lifecycle events. They do not guarantee source timestamps, exact token usage, semantic task success, complete tool coverage, or a stable transcript format.

The smallest architecture that preserves normal Codex use is:

```text
Codex lifecycle hooks
  -> bounded TraceDeck hook shim
  -> one atomic local spool file per event
  -> TraceDeck collector and normalizer
  -> SQLite
  -> read-only FastAPI API
  -> local static HTML/CSS/JavaScript dashboard

Optional reconciliation:
hook-provided transcript path
  -> Codex-version-matched parser
  -> exact fields only when recognized
```

TraceDeck must not start turns through App Server, proxy model traffic, call account usage APIs, or attach to undocumented daemon sockets. Those approaches would either change the user's normal workflow, add network/auth behavior, or depend on an unsupported interface.

## Corrections to the original brief

| Original assumption | Correction |
| --- | --- |
| Every desired turn field can probably be captured | Fields are capability-driven. Unsupported values remain null and carry a source/quality marker. |
| `success/error` can summarize a turn | Hook completion is lifecycle completion, not proof that the user's goal succeeded. Use completed, failed, interrupted, partial, and unknown. |
| Token counts are part of the normal hook payload | The documented hook schema does not expose token usage. Exact token counts require a recognized local transcript/event source and otherwise remain null. |
| All hook handlers should emit no stdout | `Stop` expects JSON on successful exit. Return only neutral JSON with no model-visible context or behavior change. |
| Tool hooks provide a complete timeline | Local shell, `apply_patch`, MCP, and most local function tools are covered; hosted and specialized paths can be absent. Display coverage gaps. |
| Tool duration is directly available | Hook payloads do not guarantee duration. Derive an observer duration only when matching start/end events are available and label its source. |
| Session end is immediate | `SessionEnd` can occur after the conversation is archived/deleted, Codex closes, or the unloaded idle grace period expires. It is not a turn-end signal. |
| Transcript files are a stable API | OpenAI Docs explicitly describes the hook transcript path as convenient but unstable. Parsers must be versioned and optional. |
| App Server is a passive event feed for the existing desktop app | App Server is a client protocol for starting/resuming/subscribing to threads. No documented general attach path to arbitrary host sessions was established. |
| One start command can silently install everything | Hook installation changes user-level Codex configuration and trust state. It must be an explicit, one-time, reversible operation. Runtime start remains one command. |
| Three tables are sufficient for every concern | Keep the three domain tables, plus schema migration and maintenance/ingestion incident tables. Do not build a full raw-event store. |

## Officially documented integration points

### Lifecycle hooks — primary MVP source

The required event set is deliberately limited:

| Hook | Fields used | TraceDeck behavior |
| --- | --- | --- |
| `SessionStart` | `session_id`, `transcript_path`, `cwd`, `model`, `source` | Upsert the session and remember the possible reconciliation path. |
| `UserPromptSubmit` | common fields, `turn_id`, `prompt` | Create/upsert the turn and record observer start time. |
| `PreToolUse` | common fields, `turn_id`, `tool_name`, `tool_use_id`, `tool_input` | Create/upsert tool call with arguments and observer start time. |
| `PostToolUse` | PreToolUse fields plus `tool_response` | Finalize the matching tool call; normalize only supported status/error details and discard raw response. |
| `Stop` | common fields, `turn_id`, `last_assistant_message` | Record latest final message when available and observer end time; return neutral JSON. |
| `SessionEnd` | common fields, `reason` | Mark the session ended; do not use it to end a turn. |

Do not install `PermissionRequest`. TraceDeck observes; it does not approve, deny, block, rewrite, continue, or otherwise influence Codex.

Hooks receive `session_id`, optional `transcript_path`, `cwd`, event name, and active model as common fields. Turn-scoped hooks include `turn_id`. Tool hooks expose canonical names, a stable tool-use ID for the invocation, arguments, and a tool-specific response on completion. The official documentation also warns that hosted tools and specialized paths may bypass local tool hooks.

### App Server — research/reference source, not MVP capture

App Server exposes authoritative turn and item lifecycle events, final item state, command duration/status, MCP arguments/results, hosted web-search items, and `thread/tokenUsage/updated`. The locally generated v2 schema for the observed Codex build contains:

- `inputTokens`
- `cachedInputTokens`
- `cacheWriteInputTokens`
- `outputTokens`
- `reasoningOutputTokens`
- `totalTokens`

Those fields justify nullable database columns and fixtures, but not an App Server collector. The documented flow requires a client to initialize, start or resume a thread, and keep reading its subscribed event stream. Until OpenAI documents a passive subscription mechanism for already-running Codex host tasks, using App Server would change TraceDeck from observer to client/host.

`account/usage/read` is also unsuitable: it is authenticated, service-backed aggregate account usage, not per-turn passive local telemetry.

### Local transcript — optional reconciliation source

The hook payload can contain `transcript_path`, but its format may change. The MVP parser contract is therefore:

1. Read only the exact hook-provided path; never scan the whole Codex directory.
2. Identify the observed Codex version and parser version.
3. Parse only known record variants covered by sanitized fixtures.
4. Extract exact usage, failure, or missing tool information only when semantics are unambiguous.
5. On an unknown variant, record `unsupported_format`, leave fields null, and continue normal capture.
6. Never modify, move, lock, or truncate the Codex transcript.

## Field reliability matrix

| Desired field | Primary source | Reliability | Rule |
| --- | --- | --- | --- |
| Codex session ID | Common hook input | High | Required domain key. |
| Codex turn ID | Turn-scoped hook input | High | Required when turn hooks fire. |
| User prompt | `UserPromptSubmit.prompt` | High for text prompt | Redact before persistence; record truncation. |
| Final assistant response | `Stop.last_assistant_message` | Medium/high, nullable | Do not reconstruct from commentary. |
| Model | Common hook input | High at event time | Store per turn; session keeps initial/last observed model. |
| CWD | Common hook input | High at event time | Store session value and update last observed value. |
| Session start/end | `SessionStart` / `SessionEnd` observer time | Medium | Label timestamps as observed. |
| Turn start/end | prompt/stop observer time | Medium | Label timestamps as observed. |
| Turn duration | Derived from matched observer endpoints | Medium | Null when endpoints or clock continuity are missing. |
| Input/output/cached/reasoning tokens | Versioned reconciliation source | Low/conditional | Exact only; otherwise null. |
| Cache-write tokens | Versioned reconciliation source | Low/conditional | Exact only; otherwise null. |
| Tool name/call ID/arguments | `PreToolUse` or `PostToolUse` | High for covered local tools | Store redacted bounded arguments. |
| Tool duration | Matched Pre/Post observer endpoints | Medium | Null when one side is missing. |
| Tool status/error | Tool-specific Post response adapter | Medium/conditional | Unknown unless recognized; never guess. |
| Overall lifecycle status | Stop plus recognized reconciliation records | Medium | Completed is not semantic success. |
| Semantic goal success | None | Unavailable | Out of scope; do not infer. |
| Hosted-tool completeness | None through hooks | Incomplete | Display coverage limitation. |

## Corrected data model

SQLite uses the three requested domain concepts plus operational metadata.

### `sessions`

- `id` integer primary key
- `codex_session_id` text unique not null
- `transcript_path` text null
- `cwd_initial` text null
- `cwd_last` text null
- `model_initial` text null
- `model_last` text null
- `start_source` text null
- `started_at` text null
- `ended_at` text null
- `timestamp_source` text not null
- `lifecycle_status` text not null
- `codex_version` text null
- `created_at`, `updated_at` text not null

### `turns`

- `id` integer primary key
- `session_id` foreign key not null
- `codex_turn_id` text null
- `user_prompt` text null
- `assistant_response` text null
- `model` text null
- `started_at`, `ended_at` text null
- `duration_ms` integer null
- `duration_source` text null
- `input_tokens` integer null
- `cached_input_tokens` integer null
- `cache_write_input_tokens` integer null
- `output_tokens` integer null
- `reasoning_output_tokens` integer null
- `total_tokens` integer null
- `usage_source` text null
- `lifecycle_status` text not null
- `error_message` text null
- prompt/response redaction and truncation metadata
- `created_at`, `updated_at` text not null

Unique key: `(session_id, codex_turn_id)` when the Codex turn ID is present.

### `tool_calls`

- `id` integer primary key
- `turn_id` foreign key not null
- `call_id` text null
- `tool_name` text not null
- `arguments_json` text null
- `started_at`, `ended_at` text null
- `duration_ms` integer null
- `duration_source` text null
- `lifecycle_status` text not null
- `error_message` text null
- argument redaction and truncation metadata
- `pre_observed`, `post_observed` integer not null
- `created_at`, `updated_at` text not null

Unique key: `(turn_id, call_id)` when the call ID is present.

### Operational tables

- `schema_migrations(version, applied_at)`
- `maintenance_events(id, kind, occurred_at, details_json)` for pruning, corruption, dropped/oversized events, unsupported transcript formats, and recovery actions

Do not persist raw hook envelopes or raw tool responses after successful normalization.

## Status semantics

Allowed turn lifecycle states:

- `in_progress`
- `completed`
- `interrupted`
- `failed`
- `partial`
- `unknown`

Allowed tool lifecycle states:

- `in_progress`
- `completed`
- `failed`
- `declined`
- `partial`
- `unknown`

`completed` means the relevant lifecycle produced a normal completion signal. It does not mean the code is correct, tests passed, or the user goal was met.

## Dashboard corrections

- Rename success/error totals to completed, failed, interrupted, partial, and unknown lifecycle counts.
- Show **Known tokens** and token coverage, for example `42 of 100 turns (42%)`, instead of treating missing usage as zero.
- Compute average duration only across turns with known duration and display duration coverage.
- Show `Unavailable` for missing metrics.
- Show badges for redacted, truncated, partial, unsupported transcript format, and incomplete tool coverage.
- The timeline is a best-effort local-tool timeline. It must not imply that hosted tools were absent merely because hooks did not observe them.
- Detail pages show data source and quality for derived usage and duration fields.

## Installation and runtime lifecycle

One-time installation:

```text
python -m tracedeck hooks install
```

The installer must:

1. Target user-level Codex hooks so ordinary tasks are covered.
2. Parse and merge existing configuration instead of overwriting it.
3. Back up the previous file.
4. Add only the six TraceDeck handlers with a recognizable ownership marker.
5. Be idempotent.
6. Provide `python -m tracedeck hooks uninstall` that removes only TraceDeck-owned handlers.
7. Print the trust/review step the user must complete in Codex.

Normal runtime:

```text
python -m tracedeck
```

The collector drains the spool, runs migrations, and serves the dashboard on `127.0.0.1:8765`. Hook capture can continue writing bounded spool files while the dashboard process is stopped. `TRACEDECK_ENABLED=0` disables new capture immediately but does not hide or delete previously stored data.

## Minimal file structure

```text
pyproject.toml
src/tracedeck/
  __init__.py
  __main__.py
  cli.py
  config.py
  clock.py
  redact.py
  truncate.py
  spool.py
  hook_entry.py
  normalize.py
  db.py
  migrations/
    001_initial.sql
  integrations/
    codex_hooks.py
    codex_transcript.py
  web/
    app.py
    static/
      index.html
      app.js
      styles.css
tests/
  fixtures/
    hooks/
    transcripts/
  unit/
  integration/
  performance/
```

Direct runtime dependencies should be limited to FastAPI and Uvicorn. Use the Python standard library for SQLite, JSON, filesystem operations, redaction, and CLI parsing. The frontend has no build step.

## Primary risks

1. **Transcript drift:** token and failure reconciliation can break after a Codex update. Mitigation: versioned fixtures, parser isolation, null-on-unknown behavior.
2. **Hook overhead:** Windows Python startup may threaten the latency budget. Mitigation: benchmark the hook shim first; keep it limited to bounded read, redaction, and atomic rename.
3. **Oversized PostToolUse payloads:** raw tool responses can be large even though TraceDeck does not store them. Mitigation: bounded input handling and visible oversize incidents.
4. **Configuration safety:** hook installation can damage existing user configuration. Mitigation: structural merge, backup, ownership marker, dry run, idempotent uninstall.
5. **False completeness:** hook timelines omit hosted/specialized tools. Mitigation: visible coverage language and no fabricated events.
6. **Secret detection limits:** arbitrary secrets in free text cannot be detected perfectly. Mitigation: pre-persistence structured-key and pattern redaction, no payload logging, user-facing warning.
7. **Lifecycle ambiguity:** Stop and SessionEnd do not prove semantic success. Mitigation: lifecycle-only status vocabulary.

## Phase 1 gate

Phase 1 is complete when this report, `technical-constraints.md`, `notes.md`, `backlog.md`, and `implementation-plan.md` agree. No application code is authorized by this report. Phase 2 starts only after explicit user approval.

## Sources

- [OpenAI Docs — Codex Hooks](https://learn.chatgpt.com/docs/hooks)
- [OpenAI Docs — Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [OpenAI Docs — Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [OpenAI model documentation — GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
