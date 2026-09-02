# Backlog

The ordered implementation contract is in `implementation-plan.md`. Do not begin Phase 2 without explicit user approval.

## Phase 1 — Investigation

- [x] Analyze the supplied product brief.
- [x] Verify current Codex hooks and App Server documentation.
- [x] Map observable fields and limitations.
- [x] Correct the architecture and proposed schema.
- [x] Define implementation packets and phase gates.

## Phase 2 — Collector and persistence

- [x] P2.1 Project scaffold and configuration.
- [x] P2.2 SQLite schema and migrations.
- [x] P2.3 Hook envelope and atomic recovery spool.
- [x] P2.4 Safe hook installer, uninstaller, and capture shim.
- [x] P2.5 Normalization and SQLite projection.
- [x] P2.6 Versioned transcript reconciliation.

## Phase 3 — Local dashboard

- [x] P3.1 Read-only query layer and local API.
- [x] P3.2 Home and turn-detail interface.
- [x] P3.3 Coverage, privacy, health, and capacity indicators.

## Phase 4 — Validation and release

- [x] P4.1 Privacy, security, and failure-mode validation.
- [x] P4.2 Performance, concurrency, and storage validation.
- [x] P4.3 Consented real-Codex validation and compatibility matrix.
- [ ] P4.4 Packaging, operator documentation, and release gate. Deferred until the dashboard design update is complete.

## Future dashboard and analysis enhancements

These are prospective product ideas. They are not approved implementation packets and must be scoped against the passive, local-first architecture before coding begins.

- [x] Add explicit loading, backend-unavailable, empty-data, stale, and partial states with actionable local recovery guidance; preserve last known data when possible.
- [x] Rework the dashboard information hierarchy so attention-needed states are primary and secondary lifecycle counts do not compete equally for attention.
- [x] Add a first-run/setup status surface for collector availability, capture status, hook installation status, and the next user action.
- [x] Replace the mobile horizontal-overflow turn table with a responsive compact row/card presentation that keeps essential fields visible at 360 px.
- [x] Add accessible text summaries for charts and verify status contrast and announcements across loading, error, empty, and partial states.
- [x] Harden turn-detail rendering for nullable duration/usage values and add focused UI coverage for unavailable and partial data.
- [x] Compare the coded dashboard against the supplied Figma frame and record information-architecture, surface, metric, and responsive evidence gaps in `reports/dashboard-design-audit.md`.
- [x] Define durable date/time presentation and retention behavior for sessions, turns, and tool calls; keep observer/source labels visible and distinguish event time from observer time.
- [x] Add bounded date-range filtering for the dashboard query contract with explicit timezone and boundary semantics.
- [x] Add bounded local CSV export for model, prompt, assistant response, tool-call, and token fields, preserving nullable values, prompt-unavailable reasons, provenance, and lifecycle status. Full responses are opt-in and capped at 100 MiB per export; export files are streamed and not persisted.
- [x] Add deterministic filter combinations for date range, model, lifecycle status, token ranges/known-token coverage, duration, and tool name; preserve nullable/data-source semantics.
- [x] Add local Analytics charts for token usage over time, model usage, heavy-token turns, and tool-call frequency; render empty, partial, and token-unavailable coverage explicitly and never plot unavailable values as zero.
- [x] Add local tool attribution, including nullable canonical MCP server/tool identifiers from `mcp__server__tool` hook names, with existing call counts, status, duration, and turn/session context.
- [ ] Investigate plugin ownership attribution. Store/display plugin identifiers only when an authoritative passive local source exposes them; otherwise keep plugin attribution unknown/unavailable. **Deferred by user on 2026-09-02; plugin ownership remains unsupported for the current release.** See `reports/phase-7-s7.2-attribution.md`.
- [x] Add bounded turn timelines, first-class session summaries, session and historical-range comparison, context-efficiency aggregates, tool duration/error percentiles, and richer Setup/Health diagnostics. Git/repository correlation remains deferred.
- [x] Add privacy-safe file observations from explicit tool inputs, including observed/written classification and event-time file sizes, with bounded CSV export. Desktop transcript imports also accept explicit structured `function_call` paths; opaque shell/exec command strings remain unsupported.
- [ ] Add saved filter views and repeatable export presets without persisting secrets or raw tool responses.
- [ ] Preserve a strict zero-versus-unknown distinction in every card, list, chart, filter, and CSV export: `0 tools` is valid only when tool coverage is known; otherwise show `tool data unavailable`.
- [x] Make History fast to scan with reasonable row height, dense-but-readable spacing, right-aligned key values, stable headings, and a sticky filter bar.
- [x] Add power-user keyboard flows: `/` focuses search, `f` opens filters, `Esc` clears/closes the active filter state, and `Enter` opens the focused turn detail.
- [x] Make date filtering contract explicit with relative/custom range inputs, browser-timezone clarity, inclusive/exclusive boundary documentation, and an API-visible active range.

## Next execution order after dashboard audit

The detailed packet definitions, dependencies, gates, and high-risk stop conditions are now in `implementation-plan.md` under **Current execution plan after validation and dashboard audit**. Execute one packet at a time in this order:

1. D0 scope and source-contract decisions. **Complete:** Analytics and bounded CSV are in release scope; local-time/observer-time semantics are selected; MCP server/tool attribution is selected; plugin ownership remains unsupported for MVP.
2. P5.1–P5.4 dashboard reliability, accessibility, responsive History, navigation, and first-run setup.
3. P6.1–P6.4 time/retention, History filters/pagination, Analytics, and bounded CSV export according to D0 scope.
4. P7.1 local tool attribution.
5. S7.2 plugin/MCP source investigation; implementation is not implied by the spike.
6. P7.3 saved filters/export presets if approved.
7. P4.4 packaging and final release gate.

Do not mark a prospective enhancement complete merely because a similar UI element exists. Each packet needs its own source contract, targeted tests, and work-log evidence.

### Enhancement constraints

- Reuse the existing nullable/source-aware schema and lifecycle vocabulary.
- Keep exports local and bounded; apply the same redaction, truncation, and privacy rules as the dashboard.
- Treat hook coverage as incomplete for hosted or specialized tools. Plugin/MCP visibility may require a versioned transcript or another documented passive source.
- Treat known zero and unavailable as different states across API, UI, charts, filters, and exports; missing data must never be coerced to zero.
- Verify list density, sticky-filter behavior, keyboard shortcuts, focus order, and date-range interaction at desktop and 360 px widths.
- Add each enhancement to `implementation-plan.md` as a separately approved packet only after its data source, storage impact, and acceptance tests are defined.

### Scale-oriented Analytics requirement

- [ ] As local transcript volume grows, make aggregate statistics and trends the primary dashboard experience. Keep individual prompts/responses behind bounded search, pagination, sampling, or explicit drill-down instead of rendering every prompt in the main surface.

- [x] Normalize retained user/final-assistant content into bounded messages linked to SHA-256 deduplicated compressed local blobs; keep turn metadata queryable and exclude raw tool responses.

### Newly identified source work

- [x] P2.7 Desktop transcript discovery and import: read local `.codex/sessions/**/*.jsonl` files, including `originator: codex_work_desktop`, without external APIs or raw tool-response persistence.
- [x] Import existing local Codex desktop session transcripts into the normalized TraceDeck schema with bounded, redacted, version-aware reconciliation and file/record deduplication.

Execution order update: D0 and P2.7 are complete; continue with P5.1–P5.4, P6.1–P6.4, P7.1, S7.2, P7.3, and P4.4.
