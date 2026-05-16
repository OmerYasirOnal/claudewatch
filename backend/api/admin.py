"""Admin / control-plane endpoints for the daemon.

These routes power the dashboard's status panel (and the upcoming tray
"control panel"): they let the UI introspect daemon state (status, log tail,
DB stats) and trigger a small set of administrative actions (prune, restart)
without anyone having to SSH in.

Conventions matched from the rest of ``backend/api/``:
- ``_state(request)`` to get ``AppState``.
- ``_check_read_only(request)`` (imported from ``actions``) to gate every
  write endpoint behind ``config.read_only``.
- Blocking I/O wrapped in ``asyncio.to_thread`` so the event loop never
  stalls on a slow ``stat`` / log read.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.api.actions import _check_read_only
from backend.config import LOGS_DIR

router = APIRouter(prefix="/api/admin")
log = logging.getLogger(__name__)

# Hard cap on how much of the log file we'll read. We seek from the end and
# slurp at most this many bytes; lines() then splits + tails. Matches the
# protective pattern used in ``api/sessions.py``'s log-tail (issue #47).
_MAX_LOG_READ_BYTES = 1 * 1024 * 1024

# How often the scheduler's prune timer fires (kept in sync with
# ``server._PRUNE_INTERVAL_SECONDS``). Lifted here so we don't import from
# the server module (which would create a circular import).
_PRUNE_INTERVAL_SECONDS = 3600.0

# Delay between flushing a /restart response and SIGTERM'ing self. 100ms is
# enough for uvicorn to finish writing the body but short enough that the
# user's "click → daemon down" feedback is instant.
_RESTART_DELAY_SECONDS = 0.1


def _state(request: Request):
    return request.app.state.s


def _log_path() -> Path:
    return LOGS_DIR / "server.log"


def _file_size(path: Path) -> int:
    """``stat().st_size`` with a 0 fallback when the file is absent."""
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _read_log_tail_blocking(path: Path, lines: int, grep: str | None) -> tuple[list[str], bool]:
    """Return (lines, truncated). Reads at most _MAX_LOG_READ_BYTES from the tail.

    ``truncated`` is True when the requested ``lines`` exceeds what we could
    pull from the bounded read window — i.e. the caller is seeing the most
    recent chunk but not the full history they asked for.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], False
    try:
        with open(path, "rb") as f:
            if size > _MAX_LOG_READ_BYTES:
                f.seek(-_MAX_LOG_READ_BYTES, 2)
                f.readline()  # discard partial line
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return [], False

    all_lines = raw.splitlines()
    if grep:
        # Substring match (NOT regex) — keeps this O(n*m) and free of any
        # catastrophic-backtracking surprises a malicious caller could send.
        all_lines = [ln for ln in all_lines if grep in ln]
    tail = all_lines[-lines:] if lines > 0 else all_lines
    truncated = size > _MAX_LOG_READ_BYTES
    return tail, truncated


@router.get("/status")
async def get_status(request: Request) -> dict:
    """Daemon-side state: version, uptime, DB + log file stats, scheduler timing."""
    s = _state(request)
    now = datetime.now(timezone.utc)
    started_at: datetime = getattr(s, "started_at", now)
    uptime = max(0, int((now - started_at).total_seconds()))

    # History DB stats — count rows + measure file size on disk.
    history_rows = 0
    if s.state is not None:
        try:
            history_rows = await s.state.count_sessions()
        except Exception as e:  # noqa: BLE001
            log.warning("count_sessions failed: %s", e)
    history_db_size = await asyncio.to_thread(_file_size, s.state.db_path) if s.state else 0

    # Log file path + size — cheap stat call, but still thread-pooled for
    # consistency with the other I/O on this route.
    log_path = _log_path()
    log_size = await asyncio.to_thread(_file_size, log_path)

    # Scheduler timing. last_prune_at is stored as a ``time.time()`` float;
    # convert it back into wall-clock ISO for the UI. next_prune_in_seconds
    # is the gap until the next periodic prune fires inside the scheduler.
    cfg = s.config or {}
    last_prune_iso: str | None = None
    next_prune_in: int | None = None
    if s.last_prune_at:
        last_prune_iso = (
            datetime.fromtimestamp(s.last_prune_at, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        )
        elapsed = time.time() - s.last_prune_at
        next_prune_in = max(0, int(_PRUNE_INTERVAL_SECONDS - elapsed))

    iterm_connected = False
    iterm_last_error_iso: str | None = None
    if s.iterm_manager is not None:
        iterm_connected = bool(getattr(s.iterm_manager, "connected", False))
        last_err = getattr(s.iterm_manager, "_last_error_at", 0.0) or 0.0
        if last_err:
            iterm_last_error_iso = (
                datetime.fromtimestamp(last_err, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            )

    return {
        "version": request.app.version,
        "uptime_seconds": uptime,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "pid": os.getpid(),
        "python_version": (f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
        "active_sessions": len(s.sessions),
        "history_rows": history_rows,
        "history_db_size_bytes": history_db_size,
        "log_file": str(log_path),
        "log_file_size_bytes": log_size,
        "scheduler": {
            "process_scan_interval_seconds": cfg.get("process_scan_interval_seconds", 2),
            "iterm_refresh_interval_seconds": cfg.get("iterm_refresh_interval_seconds", 5),
            "last_prune_at": last_prune_iso,
            "next_prune_in_seconds": next_prune_in,
        },
        "iterm": {
            "connected": iterm_connected,
            "last_error_at": iterm_last_error_iso,
        },
    }


@router.get("/logs")
async def get_logs(
    request: Request,
    lines: int = Query(100, ge=1, le=1000),
    grep: str | None = Query(None, max_length=200),
) -> dict:
    """Tail of ``server.log``. Bounded to the last ~1 MB of the file."""
    path = _log_path()
    tail, truncated = await asyncio.to_thread(_read_log_tail_blocking, path, lines, grep)
    size = await asyncio.to_thread(_file_size, path)
    return {
        "path": str(path),
        "size_bytes": size,
        "lines": tail,
        "truncated": truncated,
    }


@router.post("/prune")
async def post_prune(
    request: Request,
    hours: int = Query(48, ge=1, le=24 * 365 * 100),
) -> dict:
    """Trigger an immediate ``state.prune(hours=...)`` and report rows removed."""
    _check_read_only(request)
    s = _state(request)
    if s.state is None:
        raise HTTPException(503, "history state unavailable")
    pre = await s.state.count_sessions()
    await s.state.prune(hours=hours)
    post = await s.state.count_sessions()
    return {"rows_deleted": max(0, pre - post)}


async def _delayed_sigterm() -> None:
    """Background task: sleep briefly, then SIGTERM self.

    The delay gives uvicorn enough time to flush the response body to the
    client before the process dies; otherwise the dashboard sees a torn
    connection and surfaces "request failed" instead of "restart_initiated".
    """
    await asyncio.sleep(_RESTART_DELAY_SECONDS)
    os.kill(os.getpid(), signal.SIGTERM)


@router.post("/restart")
async def post_restart(
    request: Request,
    confirm: bool = Query(False),
) -> JSONResponse:
    """SIGTERM the current process after the response flushes.

    Useful when running under launchd (``KeepAlive=true`` → auto-restart) or
    via the bundled .app's PythonRunner (sees the exit and re-spawns).

    Requires ``?confirm=true`` so a misclick or stray ``curl`` can't take the
    daemon down by accident.
    """
    _check_read_only(request)
    if not confirm:
        raise HTTPException(400, "restart requires ?confirm=true")
    asyncio.create_task(_delayed_sigterm())
    return JSONResponse({"restart_initiated": True}, status_code=202)
