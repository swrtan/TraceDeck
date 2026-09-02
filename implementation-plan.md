# TraceDeck implementation plan

Status: P2–P3 implementation, P2.7 desktop import, P4.1–P4.3 validation, P5.1–P5.4 dashboard hardening, P6.1–P6.4 time/history/analytics/export, P7.1 local MCP attribution, and file-observation hardening complete; S7.2 plugin investigation and P7.3 saved presets remain deferred; P4.4 packaging is in progress.
Target coding model: `gpt-5.6-luna`

This plan converts the completed Phase 1 design into bounded work packets. Each coding agent owns exactly one packet, reads the required project documents, implements only that packet, runs targeted tests, updates `backlog.md` and `backlog/log.md`, and stops.

## Agent operating contract

Every agent begins by reading:

1. `AGENTS.md`
2. `notes.md`
3. `reports/phase-1-analysis.md`
4. `reports/technical-constraints.md`
5. this plan

Rules:

- Preserve the hook-first passive architecture.
- Do not call an LLM, OpenAI API, account usage endpoint, or any external network service.
- Do not start or proxy Codex turns through App Server.
- Do not infer unavailable fields.
- Use nullable fields and explicit source/quality markers.
- Do not persist raw tool responses, raw hook envelopes, secrets, or authorization material.
- Keep the server bound to `127.0.0.1`.
- Run the packet's targeted tests before changing its backlog item to complete.
- If an upstream schema differs from Phase 1 assumptions, stop and record evidence in `reports/`; do not silently redesign adjacent phases.

GPT-5.6 Luna is documented as an efficient, high-volume model and supports reasoning levels through `max`. Use `medium` for routine packets and `high` for schema, reconciliation, storage, and performance packets. Keep prompts narrow and include exact acceptance criteria.

## Dependency flow

```text
P2.1 scaffold
  -> P2.2 database
  -> P2.3 spool contract
  -> P2.4 hook installation/capture
  -> P2.5 normalization/projection
  -> P2.6 transcript reconciliation
  -> P3.1 API
  -> P3.2 dashboard
  -> P3.3 health/coverage UI
  -> P4.1 security/privacy
  -> P4.2 performance/storage
  -> P4.3 real Codex validation
  -> P4.4 packaging/release
```

Do not run P2 packets in parallel until the prior packet's contracts and tests are merged. P3.2 may begin after P3.1 response schemas are stable. Phase 4 runs against the integrated branch.

## Phase 1 — Investigation and design

Status: complete.

Deliverables:

- Corrected capability map
- Hook-first architecture
- Nullable/source-aware schema
- Technical budgets
- Minimal file structure
- Known limitations
- Ordered implementation packets

Gate: explicit user approval is required before P2.1.

## Phase 2 — Collector and persistence

### P2.1 — Project scaffold and configuration

Reasoning: medium

Scope:

- Create `pyproject.toml` and the `src/tracedeck` package layout.
- Implement typed configuration loading with defaults for data directory, host, port, enabled flag, storage cap, and log level.
- Make `python -m tracedeck --help` work without starting services.
- Add pytest structure and configuration tests.

Acceptance:

- Python 3.11–3.13 metadata is declared.
- Runtime dependencies are limited to FastAPI and Uvicorn unless a documented blocker is approved.
- `TRACEDECK_ENABLED=0` is parsed without side effects.
- No network or database write occurs during `--help`.

Stop after scaffold/config tests pass.

### P2.2 — SQLite schema and migrations

Reasoning: high

Scope:

- Implement the schema from `reports/phase-1-analysis.md`.
- Add forward-only transactional migrations.
- Configure WAL, foreign keys, busy timeout, page limits, incremental vacuum, and indexes.
- Implement repositories for sessions, turns, tool calls, and maintenance events.

Acceptance:

- Migration from an empty database is deterministic and idempotent.
- Uniqueness rules upsert duplicate hook observations.
- Nullable usage fields remain null.
- Foreign-key, pruning, and page-limit tests pass.
- No dashboard or hook installer work is included.

### P2.3 — Hook envelope and atomic recovery spool

Reasoning: high

Scope:

- Define a versioned internal hook envelope containing observed UTC time, monotonic time, event type, Codex version when available, redaction/truncation metadata, and bounded event data.
- Write one temporary file and atomically rename it into the spool per event.
- Implement ordered drain, quarantine, duplicate safety, size accounting, and bounded incident reporting.

Acceptance:

- A crash before rename produces no visible partial event.
- A crash after rename leaves a replayable event.
- Concurrent writers do not overwrite each other.
- Oversized and malformed events do not block later events.
- Spool and incident size limits match `technical-constraints.md`.

### P2.4 — Safe hook installer, uninstaller, and capture shim

Reasoning: high

Scope:

- Implement `python -m tracedeck hooks install`, `--dry-run`, `status`, and `uninstall`.
- Structurally merge user-level hooks, preserve existing entries, back up before mutation, and tag TraceDeck ownership.
- Register only `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, and `SessionEnd`.
- Implement event-specific neutral output contracts and the immediate disabled path.

Acceptance:

- Repeated install is idempotent.
- Uninstall removes only TraceDeck handlers.
- Existing hooks survive byte-equivalent or semantically equivalent round trips as documented.
- `Stop` emits valid neutral JSON; other events emit no model-visible content.
- No handler can block, deny, approve, rewrite, continue, or inject context.
- Hook latency is measured on the Windows reference environment before completion.

### P2.5 — Normalization and SQLite projection

Reasoning: high

Scope:

- Normalize the six hook types into the domain schema.
- Implement secret redaction, deterministic head/tail truncation, lifecycle states, source markers, observer timing, and tool Pre/Post correlation.
- Inspect recognized tool responses only for status/error mapping, then discard them.
- Drain spool events transactionally and idempotently.

Acceptance:

- Out-of-order and duplicate fixtures converge to the same database state.
- Missing Pre or Post produces `partial`, not fabricated timing or status.
- Stop records nullable assistant output and lifecycle completion only.
- Prompt/response/arguments never appear in logs.
- Raw envelope and raw tool response are absent from SQLite after projection.

### P2.6 — Versioned transcript reconciliation

Reasoning: high

Scope:

- Build a parser adapter selected by observed Codex version.
- Read only hook-provided transcript paths.
- Create sanitized fixtures from explicitly consented local sessions.
- Reconcile exact token usage and lifecycle errors only when the record semantics are proven.

Acceptance:

- Unknown versions/records produce `unsupported_format` maintenance events and no guessed fields.
- Transcript files remain read-only and unlocked after parsing.
- Existing hook-derived values are not overwritten by lower-quality data.
- Each extracted field records `usage_source` or equivalent provenance.
- If exact token semantics cannot be established, token support remains nullable and the packet may finish with documented non-support.

Phase 2 gate:

- All P2 tests pass.
- A mocked session flows from hook stdin through spool to SQLite.
- No model/API/network call occurs.
- Technical overhead targets have an initial measured result.
- User approval is required before Phase 3.

## Phase 3 — Local dashboard

### P3.1 — Read-only query layer and API

Reasoning: medium

Scope:

- Implement FastAPI startup/shutdown around the collector and database.
- Add health, summary, recent-turn, and turn-detail endpoints.
- Enforce pagination, bounded responses, loopback binding, Host validation, disabled CORS, CSP, and no outbound resources.

Acceptance:

- Summary totals distinguish known values from unknown coverage.
- Detail response includes source/quality, redaction, truncation, and partial markers.
- API is read-only except documented local maintenance endpoints approved later.
- API performance targets pass on the acceptance dataset.

### P3.2 — Home and turn-detail interface

Reasoning: medium

Scope:

- Build static HTML/CSS/JavaScript with no frontend build step.
- Implement summary cards, recent-turn table, detail view, usage, duration, and local-tool timeline.
- Use accessible status text in addition to color.

Acceptance:

- Missing metrics display `Unavailable`, not zero.
- Counts use lifecycle wording, not semantic success.
- Tables are keyboard usable and responsive from 360–1920 px.
- No CDN, font request, analytics, or external asset exists.

### P3.3 — Coverage, privacy, health, and capacity indicators

Reasoning: medium

Scope:

- Show token and duration coverage.
- Show partial timeline, hosted-tool limitation, redaction, truncation, unsupported transcript, stale collector, pruning, and dropped-event warnings.
- Add local data-directory and capture-enabled status without exposing secrets.

Acceptance:

- Every maintenance incident type has a visible, understandable state.
- The UI never implies complete tool coverage when the source cannot provide it.
- Privacy warnings explain best-effort secret detection.

Phase 3 gate:

- Mocked data is visible end to end.
- Home and detail acceptance criteria pass in supported browsers.
- User approval is required before release validation.

## Phase 4 — Validation and release

### P4.1 — Privacy, security, and failure-mode validation

Reasoning: high

Validate redaction fixtures, Host rejection, loopback-only binding, no outbound traffic, safe logs, disabled recording, corrupt database, full disk, locked database, malformed events, installer rollback, and uninstall preservation.

### P4.2 — Performance, concurrency, and storage validation

Reasoning: high

Build the 10,000-turn/100,000-tool acceptance dataset and measure hook p95/p99, event freshness, API latency, memory, CPU, spool bursts, WAL bounds, pruning, and the 1.25 GiB working-disk ceiling. Record results in `reports/` without relaxing targets silently.

### P4.3 — Consented real-Codex validation

Reasoning: high

Run representative local sessions covering a normal turn, failed shell command, `apply_patch`, MCP tool, long-running unified exec, missing assistant message, interrupted turn, duplicate hook delivery, Codex restart, and transcript-format mismatch. Sanitize all fixtures before committing them.

### P4.4 — Packaging, operator documentation, and release gate

Reasoning: medium

Provide the one-time install, normal start, disable, status, uninstall, data-location, data-deletion, troubleshooting, and limitations documentation. Verify installation in a clean Python environment and produce a final definition-of-done report.

Phase 4 release gate:

1. `python -m tracedeck` starts the local dashboard with one command after explicit one-time hook installation.
2. Normal Codex prompting is unchanged.
3. TraceDeck makes no model or external runtime network calls.
4. Prompt and final response appear when hooks provide them.
5. Exact usage appears only when a supported local source provides it.
6. Covered local tool calls appear chronologically with inspectable redacted arguments.
7. Missing duration, status, usage, or tool coverage is visible rather than fabricated.
8. Failed, interrupted, partial, malformed, pruned, and dropped activity is surfaced.
9. Storage, memory, CPU, latency, privacy, and loopback tests pass.
10. Install and uninstall preserve unrelated Codex configuration.

## Post-release enhancement candidates

The following ideas are recorded for future scoping and are not part of the active packet sequence or release gate:

- Date/time retention and date-range filtering across sessions, turns, and tool calls.
- Local CSV export of available model, prompt, response, tool, and token data with nullability, provenance, redaction, and truncation metadata preserved.
- Combined filters for time, model, lifecycle, tokens, duration, tools, and data-quality markers.
- Local time-series and distribution charts for activity, tokens, duration, lifecycle, tools, and coverage.
- Tool usage attribution and inspection by session/turn.
- Plugin and MCP attribution when an authoritative passive local source exposes plugin, server, and method identity; unknown values remain unknown.
- Saved filter views and export presets.

Before any candidate becomes a coding packet, define its source contract, schema impact, privacy limits, storage/performance budget, and targeted acceptance tests. In particular, plugin/MCP attribution must not be inferred from a tool name, and unavailable token or timestamp values must not be fabricated.

## Current execution plan after validation and dashboard audit

The original P2–P4 packets are historical implementation packets. They are complete except for P4.4, which remains the final release gate. The next work must address the findings in `reports/dashboard-design-audit.md` before packaging is declared complete.

### Dependency and risk policy

- A packet may start only after its listed upstream response/data contract is stable.
- A source-discovery item that cannot prove an authoritative passive source must end as a documented non-support decision, not as inferred product data.
- UI work must preserve nullable values, lifecycle wording, source/quality markers, and local-only operation.
- Each packet owns targeted tests and a work-log entry; it stops at its boundary.

```text
D0 scope and source decisions
  -> P5.1 state/recovery contract
  -> P5.2 detail nullability and accessibility
  -> P5.3 responsive History surface
  -> P5.4 navigation, hierarchy, and first-run surface
  -> P6.1 time/retention contract
  -> P6.2 History query, filters, and pagination
  -> P6.3 Analytics surface and nullable charts
  -> P6.4 bounded local CSV export
  -> P7.1 tool attribution
  -> S7.2 plugin/MCP source investigation (gated spike)
  -> P7.3 saved filters and export presets
  -> P4.4 packaging and final release gate
```

### Phase D0 — Product scope and source-contract decisions

Status: complete; scope decisions recorded in `notes.md` on 2026-09-02. No runtime implementation in this decision packet.

Decide whether the supplied Figma surfaces are the target for the next release: Home/History, dedicated Turn Detail, and Analytics. Confirm that lifecycle/coverage cards remain secondary to attention-needed states, and define the minimum supported responsive behavior. Record decisions in `notes.md` only after approval.

Resolve or explicitly defer:

- Whether the next release includes Analytics or only a reliable Home/History and Turn Detail.
- Date/time display timezone, event-time versus ingestion-time labels, and retention/pruning presentation.
- Whether CSV export is in the release gate or remains post-release.
- The supported browser/device test matrix and the level of screen-reader validation possible locally.

Approved scope: Analytics and bounded local CSV export are included in the next release scope; local-time display with explicit observer/ingestion labels is required; canonical MCP server/tool attribution is included; plugin ownership remains unsupported/unknown for MVP; isolated release validation covers the remaining CPU/RSS, disk-pressure, and packet-observation gaps.

Acceptance: the approved scope and source decisions are recorded in `notes.md`, every selected item has a source contract and acceptance test, and deferred items remain in `backlog.md`.

### Phase 5 — Dashboard reliability and core usability

#### P5.1 — Loading, unavailable, empty, stale, partial, and first-run states

Status: complete on 2026-09-02; targeted and full pytest validation passed.

Depends on: existing health/summary API and capture status. Implement a user-facing state model with retry, last-successful-refresh, collector start/status guidance, hook-installed status, and next action. Preserve last known data where safe.

Acceptance: backend failure, empty database, stale collector, partial data, disabled capture, and healthy populated states are distinct, actionable, and covered by focused UI/API tests.

#### P5.2 — Turn-detail nullability and accessibility hardening

Status: complete on 2026-09-02; targeted and full pytest validation passed.

Depends on: stable P5.1 state model and existing turn-detail response schema. Fix nullable duration/usage rendering, add text summaries for charts, verify status contrast, live-region/focus behavior, and ensure lifecycle completion is not presented as semantic task success.

Acceptance: null, partial, unavailable, and redacted data never throws or renders as zero; keyboard and assistive-text paths expose the same meaning as color and charts.

#### P5.3 — Responsive Home/History surface

Status: complete on 2026-09-02; targeted and full pytest validation passed.

Depends on: existing paginated turn endpoint; no new database source required. Replace the 600 px mobile table overflow with compact rows/cards below the mobile breakpoint while preserving essential lifecycle, model, duration, and token-coverage information. Optimize the desktop History view for rapid scanning with readable density, right-aligned key values, stable headings, and a sticky filter bar.

Acceptance: essential information is usable at 360 px and desktop widths; detail navigation remains keyboard accessible; mobile behavior has focused viewport tests; list density and sticky filters remain usable while scrolling.

#### P5.4 — Navigation, hierarchy, and first-run composition

Status: complete on 2026-09-02; targeted and full pytest validation passed.

Depends on: P5.1–P5.3. Add a lightweight client-side surface switch or routes for Home/History and Turn Detail, make attention-needed states primary, add a setup/status surface, and support `/` search focus, `f` filter open, `Esc` clear/close, and `Enter` detail navigation. Do not add a frontend build framework.

Acceptance: the user can reach Home/History, a focused Turn Detail, and setup/status from the local UI; lifecycle breakdown remains available but secondary; keyboard actions work without stealing focus from text inputs; no external assets are introduced.

Phase 5 gate: all dashboard state, responsive, accessibility, and nullable-data tests pass; the Figma comparison is updated with evidence for implemented and intentionally deferred surfaces.

### Phase 6 — History, time, analytics, and export

#### P6.1 — Durable time and retention contract

Status: complete on 2026-09-02; targeted and full pytest validation passed.

Depends on: D0 decisions and existing observer timestamps. Define display timezone, event/ingestion labels, retention messaging, and query semantics without inventing source timestamps. Add only the smallest schema/API changes required.

Acceptance: every displayed time identifies its meaning; pruning and unknown timestamps remain visible; migration, retention, and timezone tests pass.

#### P6.2 — History search, filters, date range, and pagination

Status: complete on 2026-09-02; targeted and full pytest validation passed.

Depends on: P6.1 and P5.4 navigation. Add bounded read-only query parameters for search, model, lifecycle, tool, token-known coverage, duration, quality/source markers, date range, and pagination. Provide relative date presets and custom ranges with explicit timezone and boundary semantics. Keep count/coverage semantics explicit for nullable fields.

Acceptance: combined filters are deterministic, bounded, indexed where justified, and never treat unknown values as zero or as a match for a known range; active date ranges are visible and zero-result, known-zero, and unavailable-data states are distinct.

#### P6.3 — Analytics surface and nullable charts

Status: complete on 2026-09-02; targeted and full pytest validation passed.

Depends on: P6.2 query contracts and actual coverage behavior. Add token trend, model usage, heavy-token turns, and tool usage only where source data exists. Render unknown/insufficient coverage explicitly and never plot unavailable values as zero; a known zero must remain distinguishable from missing coverage.

Acceptance: populated, empty, partial, and token-unavailable datasets have distinct charts/text states; chart summaries are accessible; API and rendering remain within existing budgets.

#### P6.4 — Bounded local CSV export

Status: complete on 2026-09-02; bounded export implementation and focused validation passed. Follow-up hardening is included in the same local-only contract.

Depends on: P6.2 stable filters and P6.1 time semantics. Export only available fields with nullable values, provenance, redaction/truncation markers, and lifecycle status. Never export raw tool responses, authorization material, or unbounded payloads.

Acceptance: export is local-only, bounded, deterministic for a fixed query, privacy-tested, spreadsheet-formula-safe, and does not create a new external dependency. Full-response cutoff and original byte size are explicit in the CSV.

Phase 6 gate: History and Analytics answer the agreed questions, exports preserve data quality semantics, and performance/storage regressions are measured.

### Phase 7 — Attribution and repeatable analysis

#### P7.1 — Local tool and canonical MCP attribution

Status: complete on 2026-09-02; targeted and full pytest validation passed.

Depends on: existing tool-call records and P6.2 filters. Add inspection/count views by tool, status, duration, session, and turn using only authoritative hook-derived identity. For canonical MCP names, derive nullable server/tool fields from `mcp__server__tool`; preserve the original bounded tool name and source marker.

Acceptance: malformed or non-canonical names remain nullable; hosted/specialized tools remain visibly outside local hook coverage; no plugin inference is added.

#### S7.2 — Plugin ownership source investigation

Status: deferred by user on 2026-09-02; plugin ownership remains unsupported and no investigation or implementation is in the current release scope.

Depends on: P7.1 canonical MCP parsing and a documented passive local source, not merely a tool name. Inspect supported hook/transcript data and effective local metadata for an authoritative plugin identifier and version. App Server discovery methods are not a collector dependency; the current documentation marks plugin discovery/read methods as under development.

Acceptance: either a versioned passive source contract plus privacy/storage tests is approved for a future packet, or the item is explicitly closed as unsupported for this release. No plugin schema/UI implementation is authorized by the spike alone.

#### P7.3 — Saved filters and export presets

Depends on: P6.2 and P6.4, and on a privacy decision about local persisted configuration. Store only bounded query definitions and labels; never store prompt/response/tool payloads in presets.

Acceptance: create/update/delete/use flows are local, bounded, migration-safe, and do not persist secrets.

### Phase 8 — Release completion (existing P4.4)

Status: in progress on 2026-09-02. Operator documentation and release evidence report are complete; packaging and isolated-machine validation remain pending.

Depends on: Phase 5 gate, the selected Phase 6 scope, and any explicitly accepted Phase 7 items. Complete packaging, clean-environment installation, operator documentation, disable/status/uninstall flows, data deletion instructions, troubleshooting, limitations, and the final definition-of-done report.

Release blockers that must not be silently waived:

- Active one-minute CPU and sustained peak RSS measurements are still incomplete.
- Physical 1.25 GiB disk saturation and packet-level outbound observation require an isolated clean-machine run.
- Broader browser/screen-reader evidence is not established by the current audit.
- Plugin/MCP attribution remains unsupported unless S7.2 proves an authoritative source.

P4.4 acceptance: all selected release packets pass, remaining limitations are documented, and the release gate in the original Phase 4 section is re-run against the final integrated build.

### Cross-cutting scale requirement for Analytics

As local transcript volume grows, Analytics must be aggregation-first. The primary surface summarizes turn volume, lifecycle distribution, token/duration coverage, model/tool usage, and time trends rather than rendering every prompt. Individual prompt/response records remain available through bounded search, pagination, sampling, and explicit drill-down. Aggregations must be computed from SQLite with bounded responses; raw prompt text must not be loaded into the default dashboard payload.

### Dashboard expansion follow-up — completed 2026-09-02

The approved lightweight expansion adds a bounded turn timeline, first-class session list/detail navigation, session comparison and equivalent historical date-range comparison, context-efficiency aggregates, tool call/error/median/p95 duration metrics, and storage/coverage/last-event diagnostics in Setup. Git/repository correlation, deterministic insights, Event Explorer, tray/auto-start, rules, and notifications remain deferred.

## Reusable Luna handoff prompt

## New follow-up packet: P2.7 — Desktop transcript discovery and import

Status: complete on 2026-09-02; targeted and full pytest validation passed.

Evidence: Codex desktop writes JSONL session files under `%USERPROFILE%\\.codex\\sessions\\YYYY\\MM\\DD\\`. The inspected current desktop thread contains `session_meta`, `turn_context`, `event_msg`, `response_item`, `task_started`, `task_complete`, `token_count`, and `custom_tool_call` records, with `originator: codex_work_desktop`. Hook payloads do not reliably provide this file path, so hook-only collection cannot discover these sessions.

Depends on: P2.6 adapter contracts and the existing normalized schema.

Scope:

- Discover only the configured local Codex sessions root and read files read-only.
- Import proven session/turn metadata, user/assistant messages, lifecycle, model, and exact per-turn token snapshots when semantics are established.
- Reuse existing redaction, truncation, nullable/source-aware fields, maintenance events, and fail-open behavior.
- Deduplicate by stable session/turn/record identity and tolerate files still being written.
- Never persist raw tool responses, authorization material, hidden instructions, or unbounded transcript content.

Acceptance:

- A controlled desktop JSONL fixture creates or updates expected sessions and turns without hook input.
- Re-running import is idempotent and malformed/partial files do not block later files.
- Unsupported schema/version produces a bounded `unsupported_format` maintenance event.
- Existing hook-derived values are not overwritten by lower-quality transcript data.
- Dashboard shows imported desktop turns with explicit source/quality labels and unavailable values preserved.

Stop after importer tests, controlled fixture verification, and work-log update. Do not silently advance to P5 or P6.

## Reusable Luna handoff prompt

```text
Implement only work packet <PACKET_ID> from implementation-plan.md using gpt-5.6-luna. First read AGENTS.md, notes.md, reports/phase-1-analysis.md, reports/technical-constraints.md, and implementation-plan.md. Preserve the hook-first passive architecture and do not start the next packet. Run the packet's targeted tests, update backlog.md and backlog/log.md with evidence, then stop with changed files, test results, measured limitations, and any blocker.
```
