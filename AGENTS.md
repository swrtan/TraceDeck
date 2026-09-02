# TraceDeck project instructions

TraceDeck is a local-first observability and usage dashboard for Codex. It must observe existing local Codex data only: never call an LLM, proxy Codex traffic, alter Codex behaviour, require API keys, or send telemetry externally.

## Technical constraints

- Use a deliberately small stack: Python, SQLite, FastAPI only if useful, and plain HTML/CSS/JavaScript.
- Bind any server only to `127.0.0.1`.
- Keep the database local and never persist API keys or HTTP authorization headers.
- Respect `TRACEDECK_ENABLED=0` to disable recording.
- Do not invent unavailable Codex metrics; store them as nullable and document limitations.
- Treat `reports/technical-constraints.md` as the measurable MVP engineering budget.
- A synchronous capture hook must add no more than 100 ms at p95 and 250 ms at p99; total TraceDeck-added latency per Codex turn must remain at or below 300 ms at p95.
- New activity must become visible in the dashboard within 1 second at p95 and 3 seconds at p99 after the corresponding local event becomes available.
- The TraceDeck service must stay below 150 MiB RSS at p95 and 250 MiB hard maximum. Idle CPU must average below 1% of one core; active ingestion must average below 5% of one core over one minute.
- Default persistent storage is capped at 1 GiB. Total working disk usage, including SQLite WAL, temporary spool, and logs, must stay below 1.25 GiB.
- Do not persist raw tool responses in the MVP. Enforce documented per-field size limits, redact secrets, and mark truncation explicitly.
- The collector must fail open: TraceDeck failures must never block, modify, or fail a Codex turn.
- Hooks must produce no model-visible content during normal operation. Events such as `Stop` that require JSON output must return only the neutral event-specific JSON described in `reports/phase-1-analysis.md`.
- Supported Codex lifecycle hooks are the primary passive integration. App Server is not an MVP collector unless OpenAI documents a way to subscribe to already-running host sessions without starting, proxying, or controlling them.
- Hook data is authoritative only for fields present in the documented hook schema. Transcript reconciliation is versioned and best-effort; unavailable fields remain null.
- A completed lifecycle is not a claim that the user's goal succeeded. Never label a turn semantically successful unless a supported source explicitly provides that meaning.

## Scope and process

Phase 1 documentation is complete. Read `reports/phase-1-analysis.md` and `implementation-plan.md`. Do not implement the collector or dashboard until the user explicitly approves Phase 2.

Keep changes focused, inspect only relevant files, avoid unnecessary dependencies, and run targeted verification.

Coding agents must execute one work packet from `implementation-plan.md` at a time, satisfy its tests and acceptance criteria, update the work log, and stop at the packet boundary. They must not silently advance to the next phase.

## Documentation practice

- Record confirmed technical decisions in `notes.md`.
- Keep prospective work in `backlog.md`.
- Add completed work to `backlog/log.md`.
- Put research findings and source material in `reports/`.
- Keep the executable work breakdown and agent handoff contract in `implementation-plan.md`.
