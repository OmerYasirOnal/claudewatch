from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.api import actions, config_api, health, history, sessions, stream
from backend.config import STATE_DB, load_config
from backend.detectors.filesystem_watch import FilesystemWatcher
from backend.detectors.iterm_applescript import (
    ItermTtyLocation,
    link_pids_to_iterm_applescript,
)
from backend.detectors.iterm_detector import (
    ItermConnectionManager,
    ItermLocation,
    link_pids_to_iterm,
)
from backend.detectors.linker import LinkerState, build_sessions
from backend.models import ClaudeSession
from backend.state import State

log = logging.getLogger("claudewatch")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _safe_float(value: Any, default: float, min_val: float = 0.1) -> float:
    """Coerce ``value`` to float, falling back to ``default`` on bad input.

    Used to defend the scheduler loops against malformed config values; bad
    input logs a warning and returns ``default`` instead of raising (which
    would otherwise kill the long-running task — see issue #28).
    """
    try:
        f = float(value)
        return f if f >= min_val else default
    except (TypeError, ValueError):
        log.warning("Invalid config value %r, falling back to %s", value, default)
        return default


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# How often state.prune() runs from inside the scheduler loop.
_PRUNE_INTERVAL_SECONDS = 3600.0

# Minimum gap between AppleScript fallback invocations. The AppleScript path
# can momentarily front the iTerm window on Sonoma+, so we rate-limit hard.
_APPLESCRIPT_MIN_INTERVAL_SECONDS = 30.0


@dataclass
class AppState:
    config: dict[str, Any]
    sessions: dict[int, ClaudeSession] = field(default_factory=dict)
    sessions_started_at: dict[int, Any] = field(default_factory=dict)
    linker_state: LinkerState = field(default_factory=LinkerState)
    fs_watcher: FilesystemWatcher | None = None
    state: State | None = None
    sse_queues: set[asyncio.Queue] = field(default_factory=set)
    # iTerm state — populated by the dedicated iTerm refresh loop, consumed by
    # the main scheduler loop. Keeping them on AppState avoids re-querying iTerm
    # every tick of the (faster) main loop.
    iterm_loc_map: dict[int, ItermLocation] = field(default_factory=dict)
    iterm_tty_map: dict[int, ItermTtyLocation] = field(default_factory=dict)
    iterm_manager: ItermConnectionManager | None = None
    last_iterm_applescript_at: float = 0.0
    # Diff cache: previous broadcast hash per pid, so session.updated only fires
    # when the dump actually changes.
    session_hashes: dict[int, str] = field(default_factory=dict)
    last_prune_at: float = 0.0
    # Set on lifespan shutdown so SSE generators (and any other long-lived
    # awaiters) can wake immediately instead of waiting for their next timeout.
    # See issue #27.
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def broadcast(self, event: dict) -> None:
        for q in list(self.sse_queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Issue #43: a slow client used to get its queue silently
                # discarded, freezing the dashboard. Instead, drain the queue
                # and push a reconnect hint so the client can re-establish.
                while True:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                try:
                    q.put_nowait({"event": "reconnect-required"})
                except asyncio.QueueFull:
                    pass


# Fields that change on every tick (or are derived) and would otherwise defeat
# the session-diff hash, causing session.updated to fire constantly even when
# nothing meaningful changed (issue #45).
_DIFF_EXCLUDE = {
    "duration_seconds",
    "cpu_percent",
    "memory_mb",
    "last_activity_at",
    "current_task_elapsed_seconds",
}


def _session_hash(sess: ClaudeSession) -> str:
    return hashlib.sha256(sess.model_dump_json(exclude=_DIFF_EXCLUDE).encode()).hexdigest()


async def _emit_diffs(s: AppState, new_sessions: list[ClaudeSession]) -> None:
    """Emit started/updated/ended events with diff-aware semantics.

    `session.started` and `session.ended` always fire. `session.updated` only
    fires when the session's serialized form has changed since the last
    broadcast (tracked via SHA-256 hash in `s.session_hashes`).
    """
    prev = s.sessions
    new_map = {x.pid: x for x in new_sessions}

    for pid, sess in new_map.items():
        if pid not in prev:
            await s.broadcast({"event": "session.started", "session": sess.model_dump(mode="json")})
            s.sessions_started_at[pid] = sess.started_at
            s.session_hashes[pid] = _session_hash(sess)
        else:
            new_hash = _session_hash(sess)
            if s.session_hashes.get(pid) != new_hash:
                await s.broadcast({"event": "session.updated", "session": sess.model_dump(mode="json")})
                s.session_hashes[pid] = new_hash
        if s.state:
            await s.state.upsert_active(sess)

    for pid in list(prev.keys()):
        if pid not in new_map:
            await s.broadcast({"event": "session.ended", "pid": pid})
            s.session_hashes.pop(pid, None)
            if s.state:
                started = s.sessions_started_at.pop(pid, prev[pid].started_at)
                await s.state.mark_ended(pid, started)

    s.sessions = new_map


async def _maybe_prune(s: AppState) -> None:
    """Periodic in-loop prune. Called from the main scheduler loop, no extra timer."""
    now = time.time()
    if s.state is None:
        return
    if s.last_prune_at == 0.0:
        # First call: set the clock without pruning (prune already ran at startup).
        s.last_prune_at = now
        return
    if (now - s.last_prune_at) >= _PRUNE_INTERVAL_SECONDS:
        try:
            await s.state.prune()
        finally:
            s.last_prune_at = now


async def _scheduler_loop(s: AppState) -> None:
    interval = _safe_float(s.config.get("process_scan_interval_seconds", 2), default=2.0)
    while True:
        try:
            new_sessions = await build_sessions(
                s.config,
                s.linker_state,
                s.fs_watcher,
                iterm_loc_map=s.iterm_loc_map,
                iterm_tty_map=s.iterm_tty_map,
            )
            await _emit_diffs(s, new_sessions)

            if s.fs_watcher:
                cwds = {x.cwd for x in new_sessions if x.cwd}
                await s.fs_watcher.sync_active_cwds(cwds)

            await _maybe_prune(s)
        except Exception as e:  # noqa: BLE001
            log.exception("scheduler iteration failed: %s", e)
        await asyncio.sleep(interval)


async def _iterm_refresh_loop(s: AppState) -> None:
    """Refresh iTerm location maps on a slower, dedicated cadence.

    The Python API call is the expensive/risky one (it opens a WebSocket to
    iTerm); doing it on the same 2s cadence as the process scan was the
    underlying cause of issue #2 (focus stealing). We run it every
    iterm_refresh_interval_seconds (default 5s), reuse a single connection,
    and only fall back to AppleScript when we have unlinked claude PIDs AND
    enough time has passed since the last fallback.
    """
    interval = _safe_float(s.config.get("iterm_refresh_interval_seconds", 5), default=5.0)
    while True:
        try:
            await _iterm_refresh_once(s)
        except Exception as e:  # noqa: BLE001
            log.exception("iterm refresh iteration failed: %s", e)
        await asyncio.sleep(interval)


async def _iterm_refresh_once(s: AppState) -> None:
    if s.iterm_manager is None:
        return
    pids = list(s.sessions.keys())
    # Always query the Python API via the persistent manager.
    sess_info = await s.iterm_manager.get_sessions()
    s.iterm_loc_map = link_pids_to_iterm(pids, sess_info) if pids else {}

    # AppleScript fallback — only if there are claude PIDs that the Python API
    # did NOT manage to link, AND we're outside the cooldown window.
    unlinked = [pid for pid in pids if pid not in s.iterm_loc_map]
    now = time.time()
    if unlinked and (now - s.last_iterm_applescript_at) >= _APPLESCRIPT_MIN_INTERVAL_SECONDS:
        s.iterm_tty_map = await asyncio.to_thread(link_pids_to_iterm_applescript, unlinked)
        s.last_iterm_applescript_at = now
    elif not pids:
        # No live sessions — drop the cached tty map so we don't show stale ones.
        s.iterm_tty_map = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    state = State(STATE_DB)
    await state.connect()
    await state.init_db()
    await state.prune()
    fs_watcher = FilesystemWatcher(
        retention_minutes=int(cfg.get("file_change_retention_minutes", 10)),
        ignore_patterns=cfg.get("ignore_patterns", []),
    )
    s = AppState(
        config=cfg,
        state=state,
        fs_watcher=fs_watcher,
        iterm_manager=ItermConnectionManager(),
        last_prune_at=time.time(),
    )
    app.state.s = s
    scheduler_task = asyncio.create_task(_scheduler_loop(s))
    iterm_task = asyncio.create_task(_iterm_refresh_loop(s))
    log.info("ClaudeWatch backend started on http://127.0.0.1:%d", int(cfg.get("port", 7788)))
    try:
        yield
    finally:
        # Wake any SSE generators (or other awaiters) blocked on the queue so
        # they can exit cleanly before we tear down the scheduler. See #27.
        s.shutdown_event.set()
        for t in (scheduler_task, iterm_task):
            t.cancel()
        for t in (scheduler_task, iterm_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        if s.iterm_manager is not None:
            await s.iterm_manager.close()
        await fs_watcher.stop_all()
        await state.close()


def create_app() -> FastAPI:
    app = FastAPI(title="ClaudeWatch", version="0.2.0", lifespan=lifespan)
    # Issue #39: defeat DNS-rebinding attacks by rejecting requests whose
    # Host header is anything other than a loopback address. The daemon
    # only ever binds to 127.0.0.1, so this is purely defence-in-depth.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.include_router(sessions.router)
    app.include_router(actions.router)
    app.include_router(stream.router)
    app.include_router(health.router)
    app.include_router(history.router)
    app.include_router(config_api.router)

    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def index():
        path = FRONTEND_DIR / "index.html"
        if path.is_file():
            return FileResponse(str(path))
        return {"message": "ClaudeWatch backend running. Frontend not yet built."}

    return app


app = create_app()
