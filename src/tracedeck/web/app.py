"""Read-only local API for TraceDeck."""

from __future__ import annotations

import asyncio
import csv
import io
import threading
from statistics import median
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config, load_config
from ..attribution import canonical_mcp_attribution
from ..db import connect, database_size_bytes, read_prompt, read_turn_message
from ..integrations.codex_desktop import import_desktop_sessions
from ..hooks import EVENTS, status as hook_status
from ..normalize import project_spool
from ..prompt_policy import prompt_for_storage
from ..spool import AtomicSpool


COLLECTOR_INTERVAL_SECONDS = 0.25
STALE_AFTER_SECONDS = 10
TIME_CONTRACT = {
    "storage_timezone": "UTC",
    "display_timezone": "browser-local",
    "precision": "milliseconds",
    "date_range_start": "inclusive",
    "date_range_end": "inclusive-day; API normalizes to an exclusive next-day boundary",
    "unknown_timestamp": "preserved as unavailable",
}
EXPORT_DEFAULT_ROWS = 500
EXPORT_MAX_ROWS = 5_000
EXPORT_RESPONSE_EXCERPT_BYTES = 32 * 1024
EXPORT_MAX_BYTES = 100 * 1024 * 1024


def _csv_safe(value: Any) -> Any:
    """Keep spreadsheet programs from evaluating exported text as formulas."""

    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def _quality(source: str | None, value: Any) -> dict[str, Any]:
    return {"value": value, "source": source, "quality": "known" if value is not None else "unavailable"}


def _prompt_status(turn: dict[str, Any], prompt: str | None) -> dict[str, str | None]:
    if prompt:
        return {"state": "known", "reason": None}
    if turn.get("codex_turn_id") == "auto-compact-1":
        return {"state": "unavailable", "reason": "Auto-compact event; no user prompt"}
    if turn.get("model") == "codex-auto-review":
        return {"state": "unavailable", "reason": "Auto-review turn; no direct user prompt"}
    if turn.get("prompt_id") is not None:
        return {"state": "unavailable", "reason": "Attachment/context only; no real user prompt"}
    return {"state": "unavailable", "reason": "Source did not expose a user prompt"}


def _is_stale(lifecycle_status: str | None, updated_at: str | None) -> bool:
    if lifecycle_status != "in_progress" or not updated_at:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return False
    return age > STALE_AFTER_SECONDS


def _age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds())
    except ValueError:
        return None


def _retention_status(db, max_storage_mb: int) -> dict[str, Any]:
    row = db.execute(
        "SELECT occurred_at, details_json FROM maintenance_events WHERE kind='prune' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {
        "cap_bytes": max_storage_mb * 1024 * 1024,
        "policy": "prune oldest completed sessions at the high-water mark; active sessions are protected",
        "unknown_timestamps": "never used to select a session for pruning",
        "last_prune": {"occurred_at": row[0], "details": row[1]} if row else None,
    }


def _date_boundary(value: str, name: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name} must use YYYY-MM-DD") from exc


def _collector_state(*, integrity: str, enabled: bool, hooks_ready: bool, last_drain_at: str | None,
                    total_turns: int, partial_turns: int, stale_after: int = STALE_AFTER_SECONDS) -> dict[str, Any]:
    if integrity != "ok":
        return {"kind": "degraded", "title": "Local database needs attention", "message": "TraceDeck cannot verify the local database safely.", "next_action": "Stop TraceDeck and follow the local recovery guidance."}
    if not enabled:
        return {"kind": "capture_disabled", "title": "Capture is disabled", "message": "New events are paused. Existing local data remains available.", "next_action": "Set TRACEDECK_ENABLED=1 when you want to resume capture."}
    if not hooks_ready:
        return {"kind": "setup_required", "title": "Capture setup required", "message": "TraceDeck is running, but the required Codex lifecycle hooks are not all installed.", "next_action": "Run python -m tracedeck hooks install, then review the Codex trust step."}
    drain_age = _age_seconds(last_drain_at)
    if drain_age is None or drain_age > stale_after:
        return {"kind": "stale", "title": "Collector is stale", "message": "TraceDeck has not completed a local spool check recently.", "next_action": "Retry the refresh or restart TraceDeck locally."}
    if total_turns == 0:
        return {"kind": "empty", "title": "No recorded activity yet", "message": "Hooks are ready, but no Codex turns have reached TraceDeck.", "next_action": "Run a Codex turn, then refresh this page."}
    if partial_turns:
        return {"kind": "partial", "title": "Partial observation", "message": "Some local records are incomplete; unavailable values are preserved as unavailable.", "next_action": "Inspect the affected turns and review the coverage notice."}
    return {"kind": "healthy", "title": "Collector connected", "message": "Local activity is available and the collector is current.", "next_action": "No action required."}
def create_app(config: Config | None = None) -> FastAPI:
    runtime_config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = connect(runtime_config.data_dir / "trace.db", runtime_config.max_storage_mb)
        app.state.db = database
        app.state.desktop_import = {"files": 0, "turns": 0, "unsupported": 0, "skipped": 0}
        if runtime_config.enabled and runtime_config.codex_sessions_dir is not None:
            app.state.desktop_import = import_desktop_sessions(runtime_config.codex_sessions_dir, database)
        spool = AtomicSpool(runtime_config.data_dir / "spool")
        app.state.last_drain = project_spool(spool, database)
        app.state.last_drain_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        app.state.collector_started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        app.state.collector_stop = asyncio.Event()
        app.state.collector_lock = threading.RLock()

        async def drain_loop():
            while not app.state.collector_stop.is_set():
                try:
                    with app.state.collector_lock:
                        app.state.last_drain = project_spool(spool, database)
                        app.state.last_drain_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                except Exception:
                    # The hook path is fail-open; a transient collector failure
                    # must leave pending files available for the next attempt.
                    pass
                try:
                    await asyncio.wait_for(app.state.collector_stop.wait(), COLLECTOR_INTERVAL_SECONDS)
                except asyncio.TimeoutError:
                    continue

        app.state.collector_task = asyncio.create_task(drain_loop())
        try:
            yield
        finally:
            app.state.collector_stop.set()
            await app.state.collector_task
            database.close()

    app = FastAPI(title="TraceDeck API", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        host = request.headers.get("host", "").split(":", 1)[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "invalid host"}, status_code=400)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health(request: Request):
        db = request.app.state.db
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
        spool = AtomicSpool(runtime_config.data_dir / "spool")
        incidents = {row[0]: row[1] for row in db.execute("SELECT kind, COUNT(*) FROM maintenance_events GROUP BY kind")}
        incidents["spool_incidents"] = len(list(spool.incidents.glob("*.json")))
        try:
            hooks = hook_status()
            installed = hooks.get("installed", {})
            missing_hooks = [event for event in EVENTS if not installed.get(event, False)]
            hook_info = {"available": True, "installed": not missing_hooks, "installed_count": len(EVENTS) - len(missing_hooks), "required_count": len(EVENTS), "missing": missing_hooks}
        except Exception:
            hook_info = {"available": False, "installed": False, "installed_count": 0, "required_count": len(EVENTS), "missing": list(EVENTS)}
        total_turns = db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        partial_turns = db.execute("SELECT COUNT(*) FROM turns WHERE lifecycle_status IN ('partial', 'unknown')").fetchone()[0]
        known_tokens = db.execute("SELECT COUNT(*) FROM turns WHERE total_tokens IS NOT NULL").fetchone()[0]
        known_duration = db.execute("SELECT COUNT(*) FROM turns WHERE duration_ms IS NOT NULL").fetchone()[0]
        last_event = db.execute("SELECT MAX(updated_at) FROM turns").fetchone()[0]
        collector_state = _collector_state(integrity=integrity, enabled=runtime_config.enabled, hooks_ready=hook_info["installed"],
                                            last_drain_at=request.app.state.last_drain_at, total_turns=total_turns,
                                            partial_turns=partial_turns)
        with request.app.state.collector_lock:
            current_database_bytes = database_size_bytes(db)
        return {"status": "ok" if integrity == "ok" else "degraded", "database": integrity,
                "time_contract": TIME_CONTRACT, "retention": _retention_status(db, runtime_config.max_storage_mb),
                "state": collector_state, "hook_installation": hook_info,
                "capture_enabled": runtime_config.enabled, "data_directory_configured": True,
                "collector_started_at": request.app.state.collector_started_at,
                "last_drain_at": request.app.state.last_drain_at,
                "last_drain": request.app.state.last_drain,
                "desktop_import": request.app.state.desktop_import,
                "storage": {"database_bytes": current_database_bytes, "database_cap_bytes": runtime_config.max_storage_mb * 1024 * 1024, "wal_limit_bytes": 64 * 1024 * 1024},
                "last_event_at": last_event,
                "telemetry": {"turns": total_turns, "known_tokens": known_tokens, "known_duration": known_duration,
                               "known_token_percent": round(known_tokens / total_turns * 100, 1) if total_turns else None,
                               "known_duration_percent": round(known_duration / total_turns * 100, 1) if total_turns else None},
                "spool_usage_bytes": spool.usage_bytes(), "spool_limit_bytes": spool.max_bytes,
                "incidents": incidents}

    @app.get("/api/summary")
    def summary(request: Request, start_date: str = Query("", max_length=20), end_date: str = Query("", max_length=20)):
        db = request.app.state.db
        normalized_start = _date_boundary(start_date, "start_date") if start_date else ""
        normalized_end = _date_boundary(end_date, "end_date") if end_date else ""
        if normalized_start and normalized_end and normalized_start > normalized_end:
            raise HTTPException(status_code=422, detail="start_date must not be after end_date")
        clauses = ["1=1"]
        params: list[Any] = []
        # Summary is a record-level view: when Codex did not expose a source
        # start time, use TraceDeck's local observation/ingestion time so the
        # record remains visible in the selected period. History rows and
        # analytics still preserve the stricter known-started_at semantics.
        summary_time = "COALESCE(started_at, created_at)"
        if normalized_start:
            clauses.append(f"{summary_time} >= ?"); params.append(normalized_start)
        if normalized_end:
            clauses.append(f"{summary_time} < ?"); params.append((date.fromisoformat(normalized_end) + timedelta(days=1)).isoformat())
        where = " AND ".join(clauses)
        counts = {row[0]: row[1] for row in db.execute(f"SELECT lifecycle_status, COUNT(*) FROM turns WHERE {where} GROUP BY lifecycle_status", params)}
        stale = sum(
            _is_stale(row[0], row[1])
            for row in db.execute(f"SELECT lifecycle_status, updated_at FROM turns WHERE {where} AND lifecycle_status='in_progress'", params)
        )
        total = sum(counts.values())
        known_tokens = db.execute(f"SELECT COUNT(*) FROM turns WHERE {where} AND total_tokens IS NOT NULL", params).fetchone()[0]
        total_tokens = db.execute(f"SELECT SUM(total_tokens) FROM turns WHERE {where} AND total_tokens IS NOT NULL", params).fetchone()[0]
        known_duration = db.execute(f"SELECT COUNT(*) FROM turns WHERE {where} AND duration_ms IS NOT NULL", params).fetchone()[0]
        durations = [row[0] for row in db.execute(f"SELECT duration_ms FROM turns WHERE {where} AND duration_ms IS NOT NULL", params)]
        median_duration_ms = median(durations) if durations else None
        return {"turns": {"total": total, "by_lifecycle": counts, "stale_in_progress": stale},
                "token_coverage": {"known": known_tokens, "total": total, "sum": total_tokens},
                "duration_coverage": {"known": known_duration, "total": total, "median_ms": median_duration_ms},
                "active_filters": {"start_date": normalized_start or None, "end_date": normalized_end or None, "timezone": "browser-local", "unknown_timestamps_match": False, "summary_fallback": "created_at when started_at is unavailable"}}

    @app.post("/api/refresh")
    def refresh_local(request: Request):
        """Run the bounded local import and spool drain on explicit user refresh."""

        db = request.app.state.db
        with request.app.state.collector_lock:
            if runtime_config.enabled and runtime_config.codex_sessions_dir is not None:
                request.app.state.desktop_import = import_desktop_sessions(runtime_config.codex_sessions_dir, db)
            spool = AtomicSpool(runtime_config.data_dir / "spool")
            request.app.state.last_drain = project_spool(spool, db)
            request.app.state.last_drain_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        return {"desktop_import": request.app.state.desktop_import, "last_drain": request.app.state.last_drain}

    @app.get("/api/filter-options")
    def filter_options(request: Request):
        db = request.app.state.db
        models = [row[0] for row in db.execute("SELECT DISTINCT model FROM turns WHERE model IS NOT NULL AND model <> '' ORDER BY model")]
        tools = [row[0] for row in db.execute("SELECT DISTINCT tool_name FROM tool_calls WHERE tool_name IS NOT NULL AND tool_name <> '' ORDER BY tool_name")]
        return {"models": models, "tools": tools}

    @app.get("/api/sessions")
    def sessions(request: Request, limit: int = Query(50, ge=1, le=200)):
        db = request.app.state.db
        rows = db.execute("""SELECT s.id, s.codex_session_id, s.model_last, s.lifecycle_status, s.started_at, s.ended_at,
            COALESCE((SELECT m.preview FROM turns st JOIN turn_messages stm ON stm.turn_id=st.id JOIN messages m ON m.id=stm.message_id WHERE st.session_id=s.id AND m.role='user' ORDER BY COALESCE(st.started_at, st.created_at), stm.position LIMIT 1), 'Untitled session') session_title,
            COUNT(DISTINCT t.id) turns, SUM(CASE WHEN t.total_tokens IS NOT NULL THEN t.total_tokens ELSE 0 END) known_tokens,
            COUNT(DISTINCT tc.id) tool_calls, SUM(CASE WHEN tc.lifecycle_status='failed' THEN 1 ELSE 0 END) failed_tools
            FROM sessions s LEFT JOIN turns t ON t.session_id=s.id LEFT JOIN tool_calls tc ON tc.turn_id=t.id
            GROUP BY s.id ORDER BY COALESCE(s.started_at, s.created_at) DESC, s.id DESC LIMIT ?""", (limit,)).fetchall()
        return {"items": [dict(row) for row in rows], "limit": limit}

    @app.get("/api/sessions/{session_id}")
    def session_detail(session_id: int, request: Request):
        db = request.app.state.db
        session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        turns = db.execute("SELECT id, codex_turn_id, model, lifecycle_status, started_at, duration_ms, total_tokens, (SELECT m.preview FROM turn_messages tm JOIN messages m ON m.id=tm.message_id WHERE tm.turn_id=t.id AND m.role='user' ORDER BY tm.position LIMIT 1) prompt_preview, (SELECT COUNT(*) FROM tool_calls tc WHERE tc.turn_id=t.id) tool_count FROM turns t WHERE session_id=? ORDER BY COALESCE(started_at, created_at), id", (session_id,)).fetchall()
        return {"session": dict(session), "turns": [dict(row) for row in turns]}

    @app.get("/api/compare")
    def compare(request: Request, session_a: int | None = Query(None, ge=1), session_b: int | None = Query(None, ge=1), start_a: str = Query("", max_length=20), end_a: str = Query("", max_length=20), start_b: str = Query("", max_length=20), end_b: str = Query("", max_length=20)):
        db = request.app.state.db

        def aggregate(session_id: int | None, start: str, end: str):
            clauses = ["1=1"]; params: list[Any] = []
            if session_id is not None: clauses.append("t.session_id=?"); params.append(session_id)
            normalized_start = _date_boundary(start, "start_a") if start else ""
            normalized_end = _date_boundary(end, "end_a") if end else ""
            if normalized_start and normalized_end and normalized_start > normalized_end: raise HTTPException(status_code=422, detail="comparison date range is reversed")
            if normalized_start: clauses.append("t.started_at IS NOT NULL AND t.started_at>=?"); params.append(normalized_start)
            if normalized_end: clauses.append("t.started_at IS NOT NULL AND t.started_at<?"); params.append((date.fromisoformat(normalized_end) + timedelta(days=1)).isoformat())
            where = " AND ".join(clauses)
            row = db.execute(f"SELECT COUNT(*) turns, SUM(total_tokens) known_tokens, SUM(duration_ms) duration_sum, AVG(duration_ms) avg_duration, COUNT(total_tokens) known_token_turns, (SELECT COUNT(*) FROM tool_calls tc WHERE tc.turn_id IN (SELECT id FROM turns WHERE {where})) tool_calls, (SELECT COUNT(*) FROM tool_calls tc WHERE tc.lifecycle_status='failed' AND tc.turn_id IN (SELECT id FROM turns WHERE {where})) failed_tools FROM turns t WHERE {where}", [*params, *params, *params]).fetchone()
            return dict(row)

        return {"a": aggregate(session_a, start_a, end_a), "b": aggregate(session_b, start_b, end_b), "contract": {"unknown_metrics": "preserved as unavailable", "date_timezone": "browser-local"}}

    @app.get("/api/export/turns.csv")
    def export_turns(request: Request, include_full_response: bool = Query(False), max_rows: int = Query(EXPORT_DEFAULT_ROWS, ge=1, le=EXPORT_MAX_ROWS), q: str = Query("", max_length=200), model: str = Query("", max_length=120), lifecycle: str = Query("", max_length=40), tool: str = Query("", max_length=120), token_state: str = Query("", pattern="^(|known|unknown)$"), duration_state: str = Query("", pattern="^(|known|unknown)$")):
        db = request.app.state.db
        clauses = ["1=1"]; params: list[Any] = []
        if q.strip():
            needle = f"%{q.strip()}%"; clauses.append("(CAST(t.id AS TEXT) LIKE ? OR t.codex_turn_id LIKE ? OR t.model LIKE ? OR EXISTS (SELECT 1 FROM turn_messages tm_search JOIN messages m_search ON m_search.id=tm_search.message_id WHERE tm_search.turn_id=t.id AND m_search.preview LIKE ?))"); params.extend([needle] * 4)
        if model: clauses.append("t.model = ?"); params.append(model)
        if lifecycle: clauses.append("t.lifecycle_status = ?"); params.append(lifecycle)
        if tool: clauses.append("EXISTS (SELECT 1 FROM tool_calls tc_filter WHERE tc_filter.turn_id=t.id AND tc_filter.tool_name=?)"); params.append(tool)
        if token_state == "known": clauses.append("t.total_tokens IS NOT NULL")
        if token_state == "unknown": clauses.append("t.total_tokens IS NULL")
        if duration_state == "known": clauses.append("t.duration_ms IS NOT NULL")
        if duration_state == "unknown": clauses.append("t.duration_ms IS NULL")
        rows = db.execute(f"SELECT t.*, (SELECT m.preview FROM turn_messages tm JOIN messages m ON m.id=tm.message_id WHERE tm.turn_id=t.id AND m.role='user' ORDER BY tm.position LIMIT 1) AS prompt_preview FROM turns t WHERE {' AND '.join(clauses)} ORDER BY COALESCE(t.started_at, t.created_at) DESC, t.id DESC LIMIT ?", [*params, max_rows]).fetchall()

        def stream():
            output = io.StringIO(); writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow(["turn_id", "started_at", "model", "status", "prompt", "prompt_quality", "response", "response_quality", "response_truncated", "response_original_bytes", "export_cutoff", "input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens", "duration_ms", "tool_count", "observed_file_count", "written_file_count", "record_source", "prompt_unavailable_reason"])
            sent = 0
            for row in rows:
                turn = dict(row); prompt = read_prompt(db, turn.get("prompt_id")); response = read_turn_message(db, turn["id"], "assistant")
                prompt_status = _prompt_status(turn, prompt); response_truncated = False; export_cutoff = False
                response_original_bytes = len(response.encode("utf-8")) if response else None
                if response and not include_full_response and len(response.encode("utf-8")) > EXPORT_RESPONSE_EXCERPT_BYTES:
                    response = response.encode("utf-8")[:EXPORT_RESPONSE_EXCERPT_BYTES].decode("utf-8", errors="ignore"); response_truncated = True
                if response and include_full_response:
                    remaining = EXPORT_MAX_BYTES - sent; encoded = response.encode("utf-8")
                    if len(encoded) > remaining:
                        response = encoded[:max(0, remaining)].decode("utf-8", errors="ignore"); response_truncated = True
                        export_cutoff = True
                tool_count = db.execute("SELECT COUNT(*) FROM tool_calls WHERE turn_id=?", (turn["id"],)).fetchone()[0]
                observed_files = db.execute("SELECT COUNT(*) FROM file_observations WHERE turn_id=? AND access_kind='observed'", (turn["id"],)).fetchone()[0]
                written_files = db.execute("SELECT COUNT(*) FROM file_observations WHERE turn_id=? AND access_kind='written'", (turn["id"],)).fetchone()[0]
                writer.writerow([_csv_safe(value) for value in [turn.get("codex_turn_id"), turn.get("started_at"), turn.get("model"), turn.get("lifecycle_status"), prompt, "known" if prompt else "unavailable", response, "known" if response else "unavailable", response_truncated, response_original_bytes, export_cutoff, turn.get("input_tokens"), turn.get("output_tokens"), turn.get("reasoning_output_tokens"), turn.get("total_tokens"), turn.get("duration_ms"), tool_count, observed_files, written_files, turn.get("source"), prompt_status["reason"]]])
                chunk = output.getvalue(); output.seek(0); output.truncate(0); sent += len(chunk.encode("utf-8")); yield chunk
                if include_full_response and sent >= EXPORT_MAX_BYTES:
                    break

        return StreamingResponse(stream(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="tracedeck-turns-{max_rows}.csv"', "X-TraceDeck-Export-Limit": str(EXPORT_MAX_BYTES)})

    @app.get("/api/export/tools.csv")
    def export_tools(request: Request, max_rows: int = Query(EXPORT_MAX_ROWS, ge=1, le=EXPORT_MAX_ROWS)):
        db = request.app.state.db

        def stream():
            output = io.StringIO(); writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow(["turn_id", "tool_call_id", "tool_name", "mcp_server", "mcp_tool", "status", "duration_ms", "partial", "redacted", "record_source"])
            rows = db.execute("SELECT tc.*, t.codex_turn_id, t.source FROM tool_calls tc JOIN turns t ON t.id=tc.turn_id ORDER BY COALESCE(tc.started_at, tc.created_at) DESC, tc.id DESC LIMIT ?", (max_rows,)).fetchall()
            for row in rows:
                tool = dict(row); attribution = canonical_mcp_attribution(tool.get("tool_name"))
                writer.writerow([_csv_safe(value) for value in [tool.get("codex_turn_id"), tool.get("call_id"), tool.get("tool_name"), attribution.get("mcp_server"), attribution.get("mcp_tool"), tool.get("lifecycle_status"), tool.get("duration_ms"), not (tool.get("pre_observed") and tool.get("post_observed")), bool(tool.get("arguments_redacted")), tool.get("source")]])
                yield output.getvalue(); output.seek(0); output.truncate(0)
        return StreamingResponse(stream(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="tracedeck-tools.csv"'})

    @app.get("/api/export/files.csv")
    def export_files(request: Request, max_rows: int = Query(EXPORT_MAX_ROWS, ge=1, le=EXPORT_MAX_ROWS)):
        db = request.app.state.db
        def stream():
            output = io.StringIO(); writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow(["turn_id", "tool_call_id", "path", "access_kind", "size_bytes", "observed_at", "source", "confidence"])
            rows = db.execute("SELECT fo.*, t.codex_turn_id, tc.call_id FROM file_observations fo JOIN turns t ON t.id=fo.turn_id LEFT JOIN tool_calls tc ON tc.id=fo.tool_call_id ORDER BY fo.observed_at DESC, fo.id DESC LIMIT ?", (max_rows,)).fetchall()
            for row in rows:
                writer.writerow([_csv_safe(value) for value in [row["codex_turn_id"], row["call_id"], row["path"], row["access_kind"], row["size_bytes"], row["observed_at"], row["source"], row["confidence"]]])
                yield output.getvalue(); output.seek(0); output.truncate(0)
        return StreamingResponse(stream(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="tracedeck-files.csv"'})

    @app.get("/api/turns")
    def recent_turns(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        q: str = Query("", max_length=200),
        model: str = Query("", max_length=120),
        lifecycle: str = Query("", max_length=40),
        tool: str = Query("", max_length=120),
        token_state: str = Query("", pattern="^(|known|unknown)$"),
        duration_state: str = Query("", pattern="^(|known|unknown)$"),
        start_date: str = Query("", max_length=20),
        end_date: str = Query("", max_length=20),
        min_tokens: int | None = Query(None, ge=0, le=2_147_483_647),
        max_tokens: int | None = Query(None, ge=0, le=2_147_483_647),
        min_duration_ms: int | None = Query(None, ge=0, le=2_147_483_647),
        max_duration_ms: int | None = Query(None, ge=0, le=2_147_483_647),
    ):
        db = request.app.state.db
        clauses = ["1=1"]
        params: list[Any] = []
        normalized_start = _date_boundary(start_date, "start_date") if start_date else ""
        normalized_end = _date_boundary(end_date, "end_date") if end_date else ""
        if normalized_start and normalized_end and normalized_start > normalized_end:
            raise HTTPException(status_code=422, detail="start_date must not be after end_date")
        if min_tokens is not None and max_tokens is not None and min_tokens > max_tokens:
            raise HTTPException(status_code=422, detail="min_tokens must not exceed max_tokens")
        if min_duration_ms is not None and max_duration_ms is not None and min_duration_ms > max_duration_ms:
            raise HTTPException(status_code=422, detail="min_duration_ms must not exceed max_duration_ms")
        if q.strip():
            needle = f"%{q.strip()}%"
            # Full prompt bodies stay compressed; search only the bounded,
            # non-sensitive preview kept in the normalized message index.
            clauses.append("(CAST(t.id AS TEXT) LIKE ? OR t.codex_turn_id LIKE ? OR t.model LIKE ? OR EXISTS (SELECT 1 FROM turn_messages tm_search JOIN messages m_search ON m_search.id=tm_search.message_id WHERE tm_search.turn_id=t.id AND m_search.preview LIKE ?))")
            params.extend([needle] * 4)
        if model:
            clauses.append("t.model = ?"); params.append(model)
        if lifecycle:
            clauses.append("t.lifecycle_status = ?"); params.append(lifecycle)
        if tool:
            clauses.append("EXISTS (SELECT 1 FROM tool_calls tc_filter WHERE tc_filter.turn_id=t.id AND tc_filter.tool_name=?)"); params.append(tool)
        if token_state == "known": clauses.append("t.total_tokens IS NOT NULL")
        if token_state == "unknown": clauses.append("t.total_tokens IS NULL")
        if duration_state == "known": clauses.append("t.duration_ms IS NOT NULL")
        if duration_state == "unknown": clauses.append("t.duration_ms IS NULL")
        if min_tokens is not None: clauses.append("t.total_tokens IS NOT NULL AND t.total_tokens >= ?"); params.append(min_tokens)
        if max_tokens is not None: clauses.append("t.total_tokens IS NOT NULL AND t.total_tokens <= ?"); params.append(max_tokens)
        if min_duration_ms is not None: clauses.append("t.duration_ms IS NOT NULL AND t.duration_ms >= ?"); params.append(min_duration_ms)
        if max_duration_ms is not None: clauses.append("t.duration_ms IS NOT NULL AND t.duration_ms <= ?"); params.append(max_duration_ms)
        if normalized_start: clauses.append("t.started_at IS NOT NULL AND t.started_at >= ?"); params.append(normalized_start)
        if normalized_end: clauses.append("t.started_at IS NOT NULL AND t.started_at < ?"); params.append((date.fromisoformat(normalized_end) + timedelta(days=1)).isoformat())
        where = " AND ".join(clauses)
        total = db.execute(f"SELECT COUNT(*) FROM turns t WHERE {where}", params).fetchone()[0]
        offset = (page - 1) * page_size
        rows = db.execute(f"SELECT t.id, t.session_id, t.codex_turn_id, t.model, t.source, t.started_at, t.ended_at, t.updated_at, t.duration_ms, t.duration_source, t.lifecycle_status, t.total_tokens, t.usage_source, t.prompt_id, (SELECT m.preview FROM turn_messages tm JOIN messages m ON m.id=tm.message_id WHERE tm.turn_id=t.id AND m.role='user' ORDER BY tm.position LIMIT 1) AS prompt_preview, (SELECT COUNT(*) FROM tool_calls tc WHERE tc.turn_id=t.id) AS tool_count FROM turns t LEFT JOIN prompt_store p ON p.id=t.prompt_id WHERE {where} ORDER BY COALESCE(t.started_at, t.created_at) DESC, t.id DESC LIMIT ? OFFSET ?", [*params, page_size, offset]).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["prompt_preview"] = prompt_for_storage(item.get("prompt_preview"))
            item["prompt_status"] = _prompt_status(item, item["prompt_preview"])
            item["stale"] = _is_stale(item["lifecycle_status"], item["updated_at"])
        return {"page": page, "page_size": page_size, "total": total, "has_next": offset + len(items) < total, "has_previous": page > 1, "time_contract": TIME_CONTRACT, "active_filters": {"start_date": normalized_start or None, "end_date": normalized_end or None, "timezone": "browser-local", "unknown_timestamps_match": False}, "items": items}

    @app.get("/api/analytics")
    def analytics(request: Request, start_date: str = Query("", max_length=20), end_date: str = Query("", max_length=20)):
        db = request.app.state.db
        normalized_start = _date_boundary(start_date, "start_date") if start_date else ""
        normalized_end = _date_boundary(end_date, "end_date") if end_date else ""
        if normalized_start and normalized_end and normalized_start > normalized_end:
            raise HTTPException(status_code=422, detail="start_date must not be after end_date")
        clauses = ["1=1"]
        params: list[Any] = []
        if normalized_start:
            clauses.append("t.started_at IS NOT NULL AND t.started_at >= ?"); params.append(normalized_start)
        if normalized_end:
            clauses.append("t.started_at IS NOT NULL AND t.started_at < ?"); params.append((date.fromisoformat(normalized_end) + timedelta(days=1)).isoformat())
        where = " AND ".join(clauses)
        total = db.execute(f"SELECT COUNT(*) FROM turns t WHERE {where}", params).fetchone()[0]
        known_count = db.execute(f"SELECT COUNT(*) FROM turns t WHERE {where} AND t.total_tokens IS NOT NULL", params).fetchone()[0]
        known = db.execute(f"SELECT COALESCE(t.model, 'Unavailable') model, COUNT(*) turns, SUM(t.total_tokens) tokens, COUNT(t.total_tokens) known_token_turns FROM turns t WHERE {where} GROUP BY t.model ORDER BY known_token_turns DESC, model", params).fetchall()
        heavy = db.execute(f"SELECT t.id, t.codex_turn_id, t.model, t.duration_ms, t.total_tokens, COALESCE((SELECT m.preview FROM turn_messages tm JOIN messages m ON m.id=tm.message_id WHERE tm.turn_id=t.id AND m.role='user' ORDER BY tm.position LIMIT 1), 'Prompt unavailable') AS prompt_preview, (SELECT COUNT(*) FROM tool_calls tc_turn WHERE tc_turn.turn_id=t.id) AS tool_count FROM turns t WHERE {where} AND t.total_tokens IS NOT NULL ORDER BY t.total_tokens DESC, t.id DESC LIMIT 5", params).fetchall()
        tools = db.execute(f"SELECT tc.tool_name, COUNT(*) calls, SUM(CASE WHEN tc.lifecycle_status='failed' THEN 1 ELSE 0 END) errors, AVG(tc.duration_ms) average_duration_ms FROM tool_calls tc JOIN turns t ON t.id=tc.turn_id WHERE {where} GROUP BY tc.tool_name ORDER BY calls DESC, tc.tool_name LIMIT 8", params).fetchall()
        daily = db.execute(f"SELECT substr(t.started_at, 1, 10) day, SUM(t.total_tokens) tokens, COUNT(*) turns FROM turns t WHERE {where} AND t.total_tokens IS NOT NULL AND t.started_at IS NOT NULL GROUP BY day ORDER BY day DESC LIMIT 30", params).fetchall()
        state = "no_data" if total == 0 else "token_unavailable" if known_count == 0 else "partial" if known_count < total else "ready"
        tool_items = []
        for row in tools:
            item = dict(row)
            durations = [value[0] for value in db.execute(f"SELECT tc.duration_ms FROM tool_calls tc JOIN turns t ON t.id=tc.turn_id WHERE {where} AND tc.tool_name=? AND tc.duration_ms IS NOT NULL ORDER BY tc.duration_ms", [*params, item["tool_name"]]).fetchall()]
            item["median_duration_ms"] = median(durations) if durations else None
            item["p95_duration_ms"] = durations[min(len(durations) - 1, max(0, int(len(durations) * 0.95) - 1))] if durations else None
            item.update(canonical_mcp_attribution(item.get("tool_name")))
            tool_items.append(item)
        context = dict(db.execute(f"SELECT AVG(input_tokens) average_input_tokens, AVG(cached_input_tokens) average_cached_input_tokens, AVG(output_tokens) average_output_tokens, COUNT(input_tokens) known_input_turns, COUNT(cached_input_tokens) known_cached_turns, COUNT(output_tokens) known_output_turns FROM turns t WHERE {where}", params).fetchone())
        context["cache_ratio"] = (context["average_cached_input_tokens"] / context["average_input_tokens"]) if context["average_input_tokens"] else None
        return {"models": [dict(row) for row in known], "heavy_turns": [dict(row) for row in heavy], "tools": tool_items, "daily": [dict(row) for row in reversed(daily)], "context_efficiency": context, "state": state, "active_filters": {"start_date": normalized_start or None, "end_date": normalized_end or None, "timezone": "browser-local", "unknown_timestamps_match": False}, "coverage": {"known_token_turns": known_count, "total_turns": total}}

    @app.get("/api/turns/{turn_id}")
    def turn_detail(turn_id: int, request: Request):
        db = request.app.state.db
        row = db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="turn not found")
        turn = dict(row)
        turn["user_prompt"] = read_prompt(db, turn.get("prompt_id"))
        turn["prompt_status"] = _prompt_status(turn, turn["user_prompt"])
        turn["assistant_response"] = read_turn_message(db, turn_id, "assistant")
        turn["stale"] = _is_stale(turn["lifecycle_status"], turn.get("updated_at"))
        turn["time_contract"] = TIME_CONTRACT
        turn["time_meaning"] = "source event time" if turn.get("source") == "transcript:desktop" else "observer time"
        turn["duration"] = _quality(turn.pop("duration_source"), turn.pop("duration_ms"))
        turn["usage"] = {field: _quality(turn.get("usage_source"), turn.get(field)) for field in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")}
        turn["redaction"] = {"prompt": bool(turn.pop("prompt_redacted")), "response": bool(turn.pop("response_redacted"))}
        turn["truncation"] = {"prompt": bool(turn.pop("prompt_truncated")), "response": bool(turn.pop("response_truncated"))}
        tools = [dict(item) for item in db.execute("SELECT * FROM tool_calls WHERE turn_id=? ORDER BY COALESCE(started_at, created_at), id", (turn_id,)).fetchall()]
        timeline = []
        if turn.get("started_at"):
            timeline.append({"kind": "prompt", "label": "Prompt submitted", "at": turn["started_at"]})
        for tool in tools:
            tool.update(canonical_mcp_attribution(tool.get("tool_name")))
            tool["duration"] = _quality(tool.pop("duration_source"), tool.pop("duration_ms"))
            tool["partial"] = not (tool["pre_observed"] and tool["post_observed"])
            tool["redacted"] = bool(tool.pop("arguments_redacted"))
            tool["truncated"] = bool(tool.pop("arguments_truncated"))
            timeline.append({"kind": "tool", "label": tool.get("tool_name") or "Tool unavailable", "at": tool.get("started_at") or tool.get("created_at"), "ended_at": tool.get("ended_at"), "duration": tool.get("duration")})
        turn["tool_calls"] = tools
        turn["file_observations"] = [dict(item) for item in db.execute("SELECT path, access_kind, size_bytes, observed_at, source, confidence FROM file_observations WHERE turn_id=? ORDER BY observed_at, id", (turn_id,)).fetchall()]
        if turn.get("ended_at"):
            timeline.append({"kind": "response", "label": "Final response", "at": turn["ended_at"]})
        turn["timeline"] = sorted(timeline, key=lambda item: item.get("at") or "")
        return turn

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="dashboard")
    return app
