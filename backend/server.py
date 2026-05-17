"""FastAPI app + the two async scheduler loops that drive ClaudeWatch.

The lifespan brings up a single ``AppState`` (in-memory snapshot, SQLite
connection, filesystem watcher, iTerm connection manager, SSE fan-out
queues) and starts two background tasks:

* ``_scheduler_loop`` — main detector pass; runs every
  ``process_scan_interval_seconds`` (default 2s).
* ``_iterm_refresh_loop`` — refreshes the cached iTerm session maps on a
  slower cadence so the main loop never has to touch iTerm itself.

See ``docs/architecture.md`` for the full pipeline and why the loops are
split.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.api import actions, admin, config_api, files, health, history, insights, sessions, stream
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
from backend.notifications import notify
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


# Honor an env var override so the bundled .app can point at its own copy of
# frontend/ (next to site-packages/, not above it).
_env_frontend = os.environ.get("CLAUDEWATCH_FRONTEND_DIR", "").strip()
FRONTEND_DIR = Path(_env_frontend) if _env_frontend else Path(__file__).resolve().parent.parent / "frontend"

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
    # PIDs we've already fired a "high cost" notification for, so we don't
    # spam every tick after the threshold has been crossed.
    notified_high_cost_pids: set[int] = field(default_factory=set)
    last_prune_at: float = 0.0
    # Set on lifespan shutdown so SSE generators (and any other long-lived
    # awaiters) can wake immediately instead of waiting for their next timeout.
    # See issue #27.
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Wall-clock instant at which the AppState was constructed. Surfaced by
    # /api/admin/status for uptime + process-age reporting. Using a
    # default_factory means lifespan can construct AppState without needing
    # to pass it explicitly; tests that instantiate AppState directly also
    # get a sensible value.
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Issue #88: per-PID token bucket for /send-text. The deque stores monotonic
    # send timestamps in the current window; entries older than the window are
    # pruned on each request. Lives on AppState so the bucket persists across
    # requests but is reset between daemon restarts.
    send_text_rate: dict[int, deque[float]] = field(default_factory=dict)

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


# Max bytes read off the end of the conversation log when sniffing the last
# assistant text — bounds the work this does so notifications can't be slowed
# by a multi-GB JSONL.
_LAST_ASSISTANT_TAIL_BYTES = 256 * 1024
# Max characters of assistant content surfaced in the notification body.
_NOTIFICATION_PREVIEW_CHARS = 120


def _project_name(cwd: str | None) -> str:
    """Friendly project label for notifications — basename of cwd, or fallback."""
    if not cwd:
        return "(unknown)"
    try:
        name = Path(cwd).name
    except (TypeError, ValueError):
        return "(unknown)"
    return name or "(unknown)"


def _format_duration(seconds: int | float | None) -> str:
    """Render a duration as '5m 23s' / '1h 02m' / '12s'."""
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    if total < 60:
        return f"{total}s"
    if total < 3600:
        m, s = divmod(total, 60)
        return f"{m}m {s:02d}s"
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m"


def _last_assistant_text_blocking(path: Path) -> str | None:
    """Read the tail of ``path`` and return the last assistant message's first
    text block (truncated). Blocking — wrap in ``asyncio.to_thread`` to call.
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > _LAST_ASSISTANT_TAIL_BYTES:
                f.seek(-_LAST_ASSISTANT_TAIL_BYTES, 2)
                f.readline()  # discard partial line
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    # Walk lines bottom-up to find the most recent assistant entry with text.
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                cleaned = text.strip().replace("\r\n", "\n").replace("\n", " · ")
                if len(cleaned) > _NOTIFICATION_PREVIEW_CHARS:
                    cleaned = cleaned[: _NOTIFICATION_PREVIEW_CHARS - 1].rstrip() + "…"
                return cleaned
    return None


async def _last_assistant_text(path: Path | None) -> str | None:
    if path is None:
        return None
    return await asyncio.to_thread(_last_assistant_text_blocking, path)


def _format_session_end(
    sess: ClaudeSession,
    preview: str | None = None,
) -> tuple[str, str, str]:
    """Return (title, subtitle, message) strings for a session-end notification."""
    project = _project_name(sess.cwd)
    title = f"✅ Claude finished: {project}"

    model = sess.model or "(unknown model)"
    duration = _format_duration(sess.duration_seconds)
    cost = (sess.usage.cost_estimate_usd if sess.usage else None) or 0.0
    subtitle = f"{model}  ·  ${cost:.2f}  ·  {duration}"

    if preview:
        message = preview
    else:
        total_tokens = sess.usage.total_tokens if sess.usage else 0
        message = f"{sess.message_count} messages, {total_tokens} tokens"
    return title, subtitle, message


def _format_high_cost(sess: ClaudeSession) -> tuple[str, str, str]:
    """Return (title, subtitle, message) strings for a high-cost notification."""
    project = _project_name(sess.cwd)
    cost = (sess.usage.cost_estimate_usd if sess.usage else None) or 0.0
    title = f"⚠️ Claude cost: ${cost:.2f} on {project}"

    model = sess.model or "(unknown model)"
    duration = _format_duration(sess.duration_seconds)
    subtitle = f"{model}  ·  {duration} elapsed"

    if sess.current_task_subject:
        message = sess.current_task_subject
    else:
        total_tokens = sess.usage.total_tokens if sess.usage else 0
        message = f"{total_tokens} tokens used"
    return title, subtitle, message


async def _notify_session_end(sess: ClaudeSession, log_path: Path | None) -> None:
    """Background task: build and fire the rich session-end notification.

    Kept on a separate coroutine so the (potentially slow) log read can run
    off the scheduler's hot path without blocking the next diff tick.
    """
    preview = await _last_assistant_text(log_path)
    title, subtitle, message = _format_session_end(sess, preview)
    await notify(
        title=title,
        message=message,
        subtitle=subtitle,
        group=f"claudewatch-end-{sess.pid}",
    )


async def _emit_diffs(s: AppState, new_sessions: list[ClaudeSession]) -> None:
    """Emit started/updated/ended events with diff-aware semantics.

    `session.started` and `session.ended` always fire. `session.updated` only
    fires when the session's serialized form has changed since the last
    broadcast (tracked via SHA-256 hash in `s.session_hashes`).

    #93: identity is keyed on ``(pid, started_at)`` rather than ``pid`` alone.
    The OS recycles PIDs aggressively; if a claude process exits and another
    claude is spawned and reassigned the same PID before the next 2s tick,
    a pid-only diff would treat it as a quiet "update" — leaking
    ``notified_high_cost_pids`` and leaving the old SQLite row open forever.
    The public state (``s.sessions``) stays pid-keyed for callers; only the
    identity-tracking inside this function uses the composite key.
    """
    prev = s.sessions
    new_map = {x.pid: x for x in new_sessions}
    prev_keyed = {(p.pid, p.started_at.isoformat()): p for p in prev.values()}
    new_keyed = {(x.pid, x.started_at.isoformat()): x for x in new_sessions}
    notif_cfg = s.config.get("notifications", {}) or {}
    notif_enabled = bool(notif_cfg.get("enabled"))

    # Process "ended" first so a pid-reuse case (old session ended, new
    # session spawned with the same pid in the same tick) clears the per-pid
    # bookkeeping before the started branch re-populates it for the new
    # session. Otherwise the pop in the ended branch would wipe out the
    # freshly-recorded started_at / hash for the new session.
    for key, ended_sess in prev_keyed.items():
        if key in new_keyed:
            continue
        pid = ended_sess.pid
        await s.broadcast({"event": "session.ended", "pid": pid})
        s.session_hashes.pop(pid, None)
        if s.state:
            started = s.sessions_started_at.pop(pid, ended_sess.started_at)
            await s.state.mark_ended(pid, started)
        if notif_enabled and notif_cfg.get("on_session_end"):
            # Sniff the conversation log for a short preview of what the
            # assistant last said. Falls back to message/token counts when
            # the log isn't readable or contains no text blocks.
            log_path = Path(ended_sess.conversation_log_path) if ended_sess.conversation_log_path else None
            asyncio.create_task(
                _notify_session_end(ended_sess, log_path),
            )
        s.notified_high_cost_pids.discard(pid)

    for key, sess in new_keyed.items():
        pid = sess.pid
        if key not in prev_keyed:
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

        # High-cost notification — fire once per pid when cost crosses the
        # configured threshold. Don't await; osascript can block.
        if (
            notif_enabled
            and notif_cfg.get("on_high_cost")
            and sess.usage
            and sess.usage.cost_estimate_usd is not None
            and pid not in s.notified_high_cost_pids
        ):
            try:
                threshold = float(notif_cfg.get("cost_threshold_usd", 5.0))
            except (TypeError, ValueError):
                threshold = 5.0
            cost = sess.usage.cost_estimate_usd
            if cost >= threshold:
                title, subtitle, message = _format_high_cost(sess)
                asyncio.create_task(
                    notify(
                        title=title,
                        message=message,
                        subtitle=subtitle,
                        group=f"claudewatch-cost-{pid}",
                    )
                )
                s.notified_high_cost_pids.add(pid)

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
    """Main detector pass — runs forever, every ``process_scan_interval_seconds``.

    Builds a fresh ``ClaudeSession`` list via ``build_sessions``, diffs it
    against ``s.sessions``, broadcasts started/updated/ended events, syncs
    the filesystem watcher, and triggers periodic SQLite pruning.
    """
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
    app.include_router(insights.router)
    app.include_router(files.router)
    app.include_router(admin.router)

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
