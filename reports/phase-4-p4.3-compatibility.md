# Phase 4.3 compatibility validation

Status: controlled real-Codex smoke test complete; broader compatibility matrix pending  
Date: 2026-09-01

## Completed evidence

- The installed `py -m tracedeck hook-event ... --owned` entrypoint was exercised in subprocesses for SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, and SessionEnd.
- A sanitized compatibility matrix covered a normal turn, failed shell command, `apply_patch`, MCP-like local tool, long-running unified-exec-like tool, missing assistant message, duplicate PostToolUse delivery, and session end.
- The hook subprocesses returned the neutral Stop JSON and produced one bounded spool file per event.
- Deterministic projection validation confirmed failed tools, partial/in-progress tools, missing assistant response, and completed lifecycle state without raw tool responses.
- Full suite passes 38/38.

## Fixes found by the matrix

- SQLite upsert helpers now resolve the domain row ID by its conflict key instead of relying on stale `lastrowid` after `ON CONFLICT DO UPDATE`.
- Tool events no longer reset an already completed turn to `unknown` when a late or duplicate tool event arrives.

## Pending real-session validation

A user-approved ephemeral/read-only `codex exec` smoke test was run with Codex CLI 0.152.0 and model `gpt-5.6-luna`. The first run confirmed that the hook definition was skipped until trusted; the second run used the explicit `--dangerously-bypass-hook-trust` validation switch for the already-installed TraceDeck hook. SessionStart, UserPromptSubmit, and Stop ran successfully. TraceDeck recorded one new completed turn with the prompt and final response present, model `gpt-5.6-luna`, observer duration 2,015 ms, no tool calls, and nullable usage shown as unavailable. No project files were changed.

Additional approved read-only runs covered a failed shell command (`cmd /c exit 1`) and a 2-second shell command. Both produced PreToolUse/PostToolUse and Stop hook output; the dashboard showed the tool records and the long-running command duration. The test exposed that cross-process event arrival can reorder Prompt/Stop events, so the normalizer was hardened to preserve terminal lifecycle states when a late prompt arrives.

The remaining approved checks were then run in safe scopes: `apply_patch` created only a temporary validation file; the enabled `node_repl` MCP server evaluated `1 + 1` once; multiple separate ephemeral CLI invocations exercised restart-like fresh sessions; and a 20-second read-only command was interrupted with Ctrl+C. The interruption produced the expected incomplete/in-progress observation. A final failed-shell run after restarting the TraceDeck service produced a completed turn with one tool record; the real shell `exit_code` response is now mapped to `failed` rather than `unknown`.

The real Codex transcript-mismatch case remains covered by the existing fail-closed transcript tests. No external MCP service, project file, or persistent user file was modified by these validation runs. The initial untrusted run also confirmed that Codex skips changed non-managed hooks until they are reviewed/trusted.

## 0.152.x transcript usage observation

A consented local Codex 0.152.0 session was inspected read-only. Its JSONL records use the outer `type`/`payload` envelope, with `turn_context.payload.turn_id` identifying the active turn and `event_msg` records carrying `payload.type=token_count`. In this session, token usage was not present; comparison with the same local event family confirmed the exact usage shape under `payload.info.last_token_usage` with `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`, and `total_tokens`.

The version-gated adapter accepts only that per-turn snapshot and writes it to the nullable `turns.*_tokens` columns with `usage_source=transcript:codex-0.152.x`. It deliberately ignores `total_token_usage`, because deriving per-turn deltas would assume accounting semantics not guaranteed by the transcript contract. Reconciliation runs after `Stop` and `SessionEnd` when a hook-provided transcript path is available; the adapter detects `cli_version` from transcript metadata when the hook payload omits `codex_version`. No raw transcript content is persisted.

## Post-fix live validation

On 2026-09-01, Codex CLI was updated to 0.152.0 and a read-only smoke turn was run through the installed TraceDeck hooks. The turn completed normally and TraceDeck stored exact usage: `total_tokens=18077`, `usage_source=transcript:codex-0.152.x`. The dashboard summary reported known token coverage of 1/11 turns. The regression fix was required because the hook payload omitted `codex_version` even though the supplied transcript contained `session_meta.payload.cli_version=0.152.0`.
