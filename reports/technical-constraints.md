# TraceDeck MVP technical constraints

Status: baseline v0.1  
Date: 2026-09-01  
Scope: local Windows MVP

This document is the measurable engineering budget for TraceDeck. A value marked **hard** must never be exceeded in a supported configuration. A **target** is an acceptance-test objective measured on the reference machine described below.

## 1. Reference environment and measurement

- Reference machine: Windows 11 x64, 4 logical CPU cores, 8 GiB RAM, SSD, Python 3.12.
- Supported Python versions: 3.11 through 3.13 for the MVP.
- Latency targets use p95 and p99 over at least 1,000 warmed-up samples.
- CPU percentages refer to one logical core. Browser CPU and memory are excluded from service measurements.
- Acceptance-scale database: 10,000 turns and 100,000 tool calls, with representative text sizes.

## 2. Capture overhead and freshness

| Measure | Target | Hard behavior |
| --- | ---: | --- |
| Synchronous hook wall time | <=100 ms p95; <=250 ms p99 | Timeout internally at 1 s |
| Total added latency per Codex turn | <=300 ms p95 | TraceDeck failure never blocks Codex |
| Local event normalization | <=25 ms p95 | Oversized fields are bounded before persistence |
| Event-to-dashboard visibility | <=1 s p95; <=3 s p99 | Show a stale-data warning after 10 s |
| Collector cold start | <=1.5 s p95 | Health endpoint available within 3 s |
| Graceful shutdown flush | <=2 s | Leave unflushed events in the bounded local spool |

Capture hooks should be asynchronous where ordering and durability allow it. Any synchronous shim must do only bounded validation and enqueueing. On normal success, hooks that accept empty output exit with code 0 and no output. `Stop` and other events that require JSON return only neutral JSON such as `{"continue": true}` and no model-visible message, context, stop reason, or continuation decision.

## 3. Dashboard performance

| Operation | Target on acceptance-scale database |
| --- | ---: |
| Summary API | <=150 ms p95 |
| Recent-turn list API | <=150 ms p95 |
| Turn-detail API | <=200 ms p95 |
| First meaningful local page render | <=750 ms p95 |
| Search/filter response | <=250 ms p95 |

- Default page size: 50 rows; hard maximum: 200 rows.
- List endpoints return summaries only. Full prompt, response, and arguments load on the detail page.
- Queries must be indexed and bounded; no unpaginated full-table API is allowed.
- Dashboard updates may poll no more frequently than once per second.

## 4. Memory, CPU, and process limits

| Resource | Target | Hard maximum |
| --- | ---: | ---: |
| Service RSS | <=150 MiB p95 | 250 MiB |
| Per-invocation hook shim RSS | <=60 MiB p95 | 100 MiB |
| Idle CPU | <1% average | 3% over one minute |
| Active ingestion CPU | <5% average over one minute | 20% short burst |
| Worker processes | 1 | 1 |
| Background worker threads | <=4 | 8 |
| Open file handles | <=32 | 64 |

The MVP uses one local service and one SQLite writer. It must not require Redis, Docker, Node.js, a separate database server, or any cloud service.

## 5. Throughput and concurrency

- Sustained ingestion target: 20 events/second.
- Burst target: 100 events/second for 10 seconds without loss.
- Concurrent active Codex sessions: 20.
- Concurrent dashboard clients: 5.
- Events may arrive out of order; correlation uses stable source identifiers and timestamps rather than arrival order alone.
- Duplicate delivery must be idempotent through unique source-event or entity keys.

These are local-MVP limits, not multi-user server capacity claims.

## 6. Storage and retention

| Item | Limit |
| --- | ---: |
| SQLite persistent data, default hard cap | 1 GiB |
| SQLite WAL | 64 MiB |
| Recovery spool | 32 MiB |
| Rotating application logs | 20 MiB total |
| Total TraceDeck working disk | 1.25 GiB hard maximum |

- `TRACEDECK_MAX_STORAGE_MB` defaults to `1024`; supported range is 100 to 10240 MiB.
- Enforce the configured database ceiling with SQLite `max_page_count`. Measure live occupancy excluding reusable free pages, use incremental auto-vacuum, and checkpoint the WAL before deciding that capacity cannot be recovered.
- There is no age-based deletion by default.
- At 90% of the configured persistent-data cap, prune the oldest completed sessions in whole-session units until usage is below 80%.
- Never prune an active session. Never delete individual child rows independently from their session.
- Record the count, time range, and reason for every prune operation and show it in the dashboard.
- If pruning cannot restore capacity, pause new persistence, keep a bounded recovery spool, and show a persistent warning. When the spool fills, count and report dropped events; never discard silently.
- Database, WAL, spool, and logs all live in one configurable local data directory.
- Automatic backups are outside MVP scope because they would make the disk ceiling ambiguous. Manual export can be designed later.
- Pruning must also collect compressed content rows no longer referenced by any live turn or normalized message; shared content referenced by another session must be preserved.

## 7. Per-record size limits

Limits are measured after UTF-8 encoding and before SQLite insertion.

| Field | Maximum stored size |
| --- | ---: |
| User prompt | 256 KiB |
| Final assistant response | 1 MiB |
| Tool arguments | 256 KiB per call |
| Error message | 64 KiB |
| CWD, model, status, and identifiers | 4 KiB each |
| Raw incoming event envelope | 8 MiB transient parser ceiling |

- Oversized values are truncated deterministically with head and tail preservation.
- Store `is_truncated` and `original_size_bytes`; never make truncation invisible.
- Raw event envelopes are normalized and then discarded.
- If an incoming hook envelope exceeds 8 MiB, do not load it fully into memory. Increment and surface an `oversized_event` incident, preserve only bounded diagnostic metadata when available, and return a neutral fail-open hook result.
- Raw tool responses are not persisted in the MVP. Store tool identity, arguments, timing, status, and a bounded error message only.
- File observations store only bounded, explicit tool-input paths, access kind (`observed` or `written`), event-time file size when safely measurable, source, and confidence; file contents are never read or persisted. Absolute paths are reduced to a workspace-relative path or an external basename.
- Attachments, binary blobs, screenshots, terminal recordings, and file contents are outside MVP storage scope.

## 8. Database correctness

- SQLite runs in WAL mode with foreign keys enabled and a 5-second busy timeout.
- Use one writer connection or a serialized write queue; readers must not block ingestion.
- Schema changes use numbered, forward-only migrations inside transactions.
- Required uniqueness: `codex_session_id`; `(session_id, codex_turn_id)` when a turn ID exists; and `(turn_id, call_id)` when a call ID exists.
- Timestamps are UTC and stored with millisecond precision. Display uses the browser's local timezone.
- Durations use a monotonic clock when both endpoints are observed locally; otherwise they remain null.
- Token fields remain null unless Codex provides exact values. Prefer a source-provided total; do not recompute `total_tokens` by adding cached or reasoning fields whose accounting may overlap input or output totals.
- Interrupted or incomplete records remain visible with an explicit status; they are not silently converted to success.
- Run a lightweight integrity check at startup and expose corruption as a local error without modifying the source Codex data.

## 9. Integration boundaries

- Primary integration: supported Codex lifecycle hooks.
- Required hooks are `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, and `SessionEnd`. Do not install `PermissionRequest` or any hook that makes policy decisions.
- Transcript paths may be used only as a versioned, best-effort fallback because the official documentation states that transcript format is not stable.
- App Server is not the passive MVP collector. Its rich event stream applies to client-driven or subscribed threads, and Phase 1 did not establish a supported way to attach it to arbitrary already-running Codex desktop tasks.
- Hosted tools that do not pass through local tool hooks must not be fabricated in the timeline.
- The collector never proxies Codex or OpenAI traffic and never calls an OpenAI API or another model.
- `TRACEDECK_ENABLED=0` makes all hooks exit immediately without database, network, or spool writes.
- TraceDeck hook errors are fail-open and cannot block, rewrite, approve, deny, or otherwise change Codex behavior.
- Normal hook output contains no model-visible content. Hooks that permit empty output emit nothing; `Stop` emits only its neutral JSON contract. No `additionalContext`, `systemMessage`, stop reason, or continuation decision is emitted.

Official basis:

- [Codex Hooks](https://learn.chatgpt.com/docs/hooks) documents lifecycle events, common identifiers, tool coverage, background execution, and the unstable transcript-format warning.
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) documents user- and project-level configuration boundaries.

## 10. Network and web security

- Bind only to IPv4 `127.0.0.1`; default port is `8765` and may be changed locally.
- Make no outbound network requests at runtime, including analytics, update checks, fonts, CDNs, source maps, and favicons.
- Ship all HTML, CSS, JavaScript, and fonts locally.
- Reject non-local Host headers. Disable CORS by default.
- The MVP API is read-only except for explicit local maintenance actions added later.
- Set a restrictive Content Security Policy and `X-Content-Type-Options: nosniff`.
- Never expose a `0.0.0.0` binding option in the MVP.
- CSV exports prefix text cells beginning with `=`, `+`, `-`, or `@` so spreadsheet applications do not evaluate prompt, response, identifier, or tool text as formulas.

## 11. Privacy and secret handling

- Never capture environment-variable dumps, authorization headers, cookies, or credential stores.
- Redact structured keys matching `authorization`, `api_key`, `apikey`, `token`, `secret`, `password`, `cookie`, and close variants before persistence.
- Apply conservative pattern redaction to prompts, responses, tool arguments, and errors. Mark records when redaction occurred.
- Redaction is best-effort; the dashboard must warn that arbitrary secrets embedded in free text cannot be detected perfectly.
- Restrict the local data directory to the current OS user where the platform permits it.
- The MVP database is not encrypted at rest; compression and SHA-256 content hashing are storage/deduplication mechanisms, not encryption.
- Do not log prompt text, response text, tool arguments, or SQL parameter values.
- Provide a single documented way to find and delete the local TraceDeck data directory; UI deletion is outside the initial MVP unless explicitly approved.

## 12. Reliability and recovery

- Collector availability target: 99.5% during the periods it is intentionally running; this is a local process objective, not a hosted SLA.
- A crash may lose at most the event currently being enqueued; acknowledged queued events must survive process restart.
- Reprocessing the spool must be idempotent.
- A malformed event is quarantined within the bounded spool budget, counted, and surfaced; it must not stop later events.
- Database-full, database-locked, schema-version, and corruption errors must have distinct local error codes.
- Logs are structured, local, redacted, and rotated within the 20 MiB budget.

## 13. Compatibility and UI constraints

- MVP platform: Windows 11 x64. Windows 10, macOS, Linux, WSL, remote Codex, and cloud tasks are not claimed until tested.
- Supported browsers: the current and previous major versions of Edge, Chrome, and Firefox.
- Plain HTML/CSS/JavaScript only; no frontend build step and no framework runtime.
- Usable widths: 360 px through 1920 px without horizontal page scrolling; wide tables may scroll within their own container.
- All dashboard actions must be keyboard reachable. Text and status colors must meet WCAG 2.1 AA contrast.
- A missing metric displays as `Unavailable`, never `0`.

## 14. Validation gates before Phase 4 is considered complete

- Unit tests: normalization, redaction, truncation, duration calculation, status mapping, and token nullability.
- Database tests: migrations, foreign keys, idempotency, WAL recovery, pruning, and storage-limit behavior.
- Integration tests: session start/end, user prompt, turn stop, successful/failed tool use, missing fields, duplicate events, and out-of-order events.
- Hook contract tests: neutral stdout for every event type, existing-hook coexistence, idempotent install/uninstall, backup restoration, disabled recording, timeout behavior, and oversized input.
- Security tests: loopback binding, Host-header rejection, no outbound requests, and secret fixtures absent from database and logs.
- Performance tests must demonstrate every target in Sections 2 through 5 on the reference environment.
- A test failure cannot be waived by replacing missing measurements with estimates.

## 15. Explicit non-goals for these budgets

- Multi-user or remote access
- Cloud synchronization
- Mobile browsers as a supported target
- Full-text indexing of every payload
- Raw tool-output retention
- Video, audio, screenshot, or terminal recording
- Guaranteed discovery of every secret in arbitrary text
- Guaranteed coverage of Codex events that the supported local interfaces do not expose
