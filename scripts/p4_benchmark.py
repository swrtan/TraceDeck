"""Run the P4.2 acceptance-scale measurements without touching user data."""

from __future__ import annotations

import io
import json
import os
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from tracedeck.config import Config
from tracedeck.db import connect, database_size_bytes
from tracedeck.hook_entry import capture
from tracedeck.web.app import create_app


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def timed(call, count: int) -> dict[str, float]:
    samples = []
    for _ in range(count):
        started = time.perf_counter()
        call()
        samples.append((time.perf_counter() - started) * 1000)
    return {"p50_ms": percentile(samples, .50), "p95_ms": percentile(samples, .95),
            "p99_ms": percentile(samples, .99), "max_ms": max(samples),
            "mean_ms": statistics.fmean(samples)}


def build_dataset(path: Path) -> None:
    db = connect(path)
    timestamp = "2026-09-01T00:00:00+00:00"
    db.execute("BEGIN")
    db.executemany(
        "INSERT INTO sessions(codex_session_id, started_at, created_at, updated_at, lifecycle_status) VALUES (?, ?, ?, ?, 'completed')",
        [(f"session-{index}", timestamp, timestamp, timestamp) for index in range(10_000)],
    )
    session_ids = [row[0] for row in db.execute("SELECT id FROM sessions ORDER BY id")]
    db.executemany(
        "INSERT INTO turns(session_id, codex_turn_id, user_prompt, lifecycle_status, started_at, created_at, updated_at) VALUES (?, ?, ?, 'completed', ?, ?, ?)",
        [(session_id, f"turn-{index}", "benchmark prompt", timestamp, timestamp, timestamp)
         for index, session_id in enumerate(session_ids)],
    )
    turn_ids = [row[0] for row in db.execute("SELECT id FROM turns ORDER BY id")]
    db.executemany(
        "INSERT INTO tool_calls(turn_id, call_id, tool_name, arguments_json, lifecycle_status, pre_observed, post_observed, created_at, updated_at) VALUES (?, ?, 'Bash', '{}', 'completed', 1, 1, ?, ?)",
        [(turn_id, f"call-{tool_index}", timestamp, timestamp)
         for turn_id in turn_ids for tool_index in range(10)],
    )
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="tracedeck-p4-") as directory:
        root = Path(directory)
        database_path = root / "trace.db"
        started = time.perf_counter()
        build_dataset(database_path)
        dataset_seconds = time.perf_counter() - started

        app = create_app(Config(data_dir=root))
        headers = {"host": "127.0.0.1"}
        with TestClient(app) as client:
            api = {
                "summary": timed(lambda: client.get("/api/summary", headers=headers).raise_for_status(), 1000),
                "recent_turns": timed(lambda: client.get("/api/turns?page_size=50", headers=headers).raise_for_status(), 1000),
                "turn_detail": timed(lambda: client.get("/api/turns/1", headers=headers).raise_for_status(), 1000),
            }

            def concurrent_worker(_: int) -> int:
                worker_app = create_app(Config(data_dir=root))
                with TestClient(worker_app) as worker_client:
                    return sum(worker_client.get("/api/summary", headers=headers).status_code == 200 for _ in range(20))

            concurrent_started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=5) as executor:
                completed_requests = sum(executor.map(concurrent_worker, range(5)))
            concurrent_elapsed = (time.perf_counter() - concurrent_started) * 1000

        hook_root = root / "hook-data"
        os.environ["TRACEDECK_DATA_DIR"] = str(hook_root)
        hook = timed(lambda: capture("UserPromptSubmit", io.BytesIO(b'{"session_id":"bench","turn_id":"t","prompt":"hello"}')), 1000)

        db = connect(database_path)
        size = database_size_bytes(db)
        wal = database_path.with_name(database_path.name + "-wal")
        db.close()
        all_files = [file for file in root.rglob("*") if file.is_file()]
        spool_files = [file for file in all_files if "hook-data" in file.parts]
        print(json.dumps({
            "dataset": {"turns": 10_000, "tool_calls": 100_000, "build_seconds": dataset_seconds},
            "api": api,
            "concurrency": {"workers": 5, "requests": completed_requests, "elapsed_ms": concurrent_elapsed},
            "hook_capture": hook,
            "storage": {"database_allocated_bytes": size, "wal_bytes": wal.stat().st_size if wal.exists() else 0,
                         "total_working_bytes": sum(file.stat().st_size for file in all_files),
                         "hook_spool_files": len(spool_files), "hook_spool_bytes": sum(file.stat().st_size for file in spool_files)},
        }, indent=2))


if __name__ == "__main__":
    main()
