# TraceDeck

TraceDeck is a local-first observability dashboard for Codex. It records supported local Codex lifecycle events and presents bounded history, sessions, analytics, tool activity, token coverage, and CSV exports.

## What it does

- Captures supported Codex lifecycle hooks without proxying or changing Codex behaviour.
- Imports supported local Codex Desktop session transcripts on refresh.
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

The measurable performance, memory, storage, and failure-mode budgets are documented in [reports/technical-constraints.md](reports/technical-constraints.md). Current release validation boundaries are documented in [reports/release-validation-2026-09-02.md](reports/release-validation-2026-09-02.md).

## Development

```powershell
py -m pytest
node --check src/tracedeck/web/static/app.js
```

The project deliberately uses Python, SQLite, FastAPI, Uvicorn, and plain HTML/CSS/JavaScript. No frontend build step is required.

## License

TraceDeck is released under the [MIT License](LICENSE).
