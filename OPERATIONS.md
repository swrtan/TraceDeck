# TraceDeck local operations

TraceDeck is a Windows 11, local-only Codex observer. It binds to `127.0.0.1` (default port `8765`) and stores bounded, redacted data in the configured local data directory.

## Start

From the project directory:

```powershell
py -m tracedeck
```

Open `http://127.0.0.1:8765/` in a supported local browser.

## Install or remove hooks

Installation is explicit and reversible:

```powershell
py -m tracedeck hooks install
py -m tracedeck hooks status
py -m tracedeck hooks uninstall
```

TraceDeck preserves unrelated Codex hooks and creates a `hooks.json.bak` before changing the file. If capture is disabled, set `TRACEDECK_ENABLED=0`; hooks then return immediately without writing database, spool, or network data.

## Local data and limits

The default data directory is `%LOCALAPPDATA%\TraceDeck`. Set `TRACEDECK_DATA_DIR` to change it. Persistent data defaults to 1 GiB; the total working-disk ceiling is 1.25 GiB. Prompt, response, tool argument, spool, WAL, and export limits are documented in `reports/technical-constraints.md`.

TraceDeck does not encrypt the SQLite database at rest. Compression and hashing are storage mechanisms, not encryption. Do not place credentials in prompts or tool arguments; redaction is best effort.

## Troubleshooting

- Use Setup / Status to inspect collector state, hook readiness, storage, spool, coverage, and the last observed event.
- Use Refresh after starting the service or installing hooks.
- A stale or partial state means local source data was incomplete; it is not a semantic task-success claim.
- Hosted or specialized tools may not appear because they do not pass through supported local hooks.

## Delete local data

Stop TraceDeck, confirm the exact configured `TRACEDECK_DATA_DIR`, then remove that directory using Windows Explorer or an explicitly targeted PowerShell `Remove-Item`. This deletes local TraceDeck data and cannot be undone by TraceDeck.

## Release boundary

The MVP does not provide cloud sync, remote access, raw tool-response retention, plugin ownership attribution, Git correlation, notifications, tray auto-start, or automatic backups.
