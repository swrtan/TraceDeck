# TraceDeck

TraceDeck is a local-first observability dashboard for Codex. It records supported local Codex lifecycle events and presents bounded history, sessions, analytics, tool activity, token coverage, and CSV exports.

## Understand the system visually

Start with the interactive architecture explainer. It walks through the complete path from Codex lifecycle hooks to the local spool, normalization, SQLite, API, and dashboard, with explanations of the privacy and reliability decisions.

**[Open the interactive TraceDeck system explainer](https://swrtan.github.io/TraceDeck/)**

The explainer is also available as [`tracedeck_interactive_explainer.html`](tracedeck_interactive_explainer.html) in this repository. GitHub Pages publishes it automatically from the repository.

The public Pages surface is the reviewer-friendly product and architecture tour. The actual dashboard remains local by design because it reads local Codex hook/transcript data and runs its FastAPI service on `127.0.0.1`.

### Data path in one line

```text
Codex hook → hook shim → atomic local spool → redact/truncate/normalize → SQLite → FastAPI → dashboard/export
```

TraceDeck does not persist raw hook envelopes or raw tool responses. Each stored value keeps its source and quality where available; unknown values stay unknown instead of becoming zero. Long fields are bounded, and truncation remains visible in metadata. The collector is fail-open, so a TraceDeck failure must not block a Codex turn.

### Export and AI analysis boundary

TraceDeck can produce bounded local CSV exports for selected turn, session, tool, duration, token, source, and quality fields. Export files are not automatically sent anywhere and are not written back into the database. A user may inspect an export, remove sensitive rows, and manually provide selected data to an AI tool for analysis. TraceDeck itself does not call an LLM, account-usage API, or external runtime service.

## What it does

- Captures supported Codex lifecycle hooks without proxying or changing Codex behaviour.
- Automatically reconciles supported local Codex CLI, Desktop, and structured subagent transcripts without controlling Codex.
- Keeps prompt, response, token, duration, lifecycle, and source/quality fields explicit.
- Records only explicit structured file paths when the local source exposes them; file contents are never stored.
- Provides bounded local CSV exports for turns, tools, and observed files.
- Runs only on `127.0.0.1`; it has no cloud sync, API key requirement, telemetry, or external runtime call.

## Requirements

- Windows 11 is the primary supported platform.
- Python 3.11, 3.12, or 3.13.
- Codex CLI/Desktop with supported lifecycle data when capture is enabled.

## Install

From the repository directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
py -m tracedeck hooks install
```

Hook installation is explicit and reversible. Review the Codex trust prompt if it appears.

## Run

```powershell
py -m tracedeck
```

Open `http://127.0.0.1:8765/` locally. Use Setup / Status to check capture, hooks, storage, and coverage.

To disable recording without changing the installation:

```powershell
$env:TRACEDECK_ENABLED = "0"
py -m tracedeck
```

See [OPERATIONS.md](OPERATIONS.md) for status, uninstall, storage, privacy, troubleshooting, and data deletion instructions.

## Privacy and limits

TraceDeck stores bounded, redacted local data in `%LOCALAPPDATA%\TraceDeck` by default. It does not encrypt the local SQLite database. Redaction is best effort, so do not put credentials in prompts or tool arguments.

Tool coverage is source-dependent. CLI hook events can provide structured file paths; opaque Desktop `exec` command text is intentionally not parsed to guess touched files. Unknown metrics remain unavailable rather than being invented.

Token totals are locally observed usage, not account billing or weekly quota. TraceDeck keeps the latest cumulative usage for each supported agent turn, merges root and structured subagent contributions once, and lists unsupported local files in Setup / Status. It never calls an account usage API.

The measurable performance, memory, storage, and failure-mode budgets are documented in [reports/technical-constraints.md](reports/technical-constraints.md). Current release validation boundaries are documented in [reports/release-validation-2026-09-02.md](reports/release-validation-2026-09-02.md).

## Development

```powershell
py -m pytest
node --check src/tracedeck/web/static/app.js
```

The project deliberately uses Python, SQLite, FastAPI, Uvicorn, and plain HTML/CSS/JavaScript. No frontend build step is required.

## License

TraceDeck is released under the [MIT License](LICENSE).
