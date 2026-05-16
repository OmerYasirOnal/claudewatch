from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import psutil

try:
    import iterm2  # type: ignore

    _ITERM2_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ITERM2_AVAILABLE = False

log = logging.getLogger(__name__)

# After a failed iTerm Python-API call, don't retry the connection for this many
# seconds. Prevents tight reconnect loops that can churn iTerm's WebSocket and
# (on Sonoma+) re-activate the app in the window order.
_RECONNECT_BACKOFF_SECONDS = 10.0

# Cached results from the last successful query are considered fresh for this
# long. During a backoff window we return the cache instead of an empty list.
_CACHE_TTL_SECONDS = 30.0


@dataclass
class ItermSessionInfo:
    # iTerm 3.5+ returns UUID-like strings for window/tab IDs (e.g.
    # "pty-518AFBF3-77FC-...") — never coerce to int (#22).
    window_id: str
    tab_id: str
    session_id: str
    tab_title: str
    job_pid: int | None


@dataclass
class ItermLocation:
    window_id: str
    tab_id: str
    session_id: str
    tab_title: str


class ItermConnectionManager:
    """Singleton-style manager for a long-lived iTerm2 Python API connection.

    The previous implementation opened a fresh WebSocket every 2 seconds, which
    on macOS Sonoma+ can cause iTerm to briefly steal window focus. This class
    holds one connection across many `get_sessions()` calls and only tears it
    down (with backoff) on error.
    """

    def __init__(self) -> None:
        self._conn: Any | None = None
        self._last_error_at: float = 0.0
        self._cached_sessions: list[ItermSessionInfo] = []
        self._cached_at: float = 0.0
        # #34: Split locks. _conn_lock guards connection lifecycle (create/drop)
        # only; reads & API calls don't need to serialize. Lets the user-driven
        # focus_session run concurrently with the scheduler-driven get_sessions
        # — both share self._conn but only block each other for the ~1ms it
        # takes to create the connection the first time.
        self._conn_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    async def _ensure_connection(self, timeout: float) -> bool:
        """Open self._conn lazily under _conn_lock. Returns True if usable."""
        if self._conn is not None:
            return True
        async with self._conn_lock:
            if self._conn is not None:  # double-checked under lock
                return True
            try:
                self._conn = await asyncio.wait_for(iterm2.Connection.async_create(), timeout=timeout)
                return True
            except (TimeoutError, Exception) as e:  # noqa: BLE001
                log.debug("iTerm connection failed: %s", e)
                self._last_error_at = time.time()
                return False

    async def get_sessions(self, timeout: float = 3.0) -> list[ItermSessionInfo]:
        if not _ITERM2_AVAILABLE:
            return []
        # Backoff: if we recently failed and the connection is down, return the
        # cached result (or [] if no cache). Don't try to reconnect.
        now = time.time()
        if self._conn is None and (now - self._last_error_at) < _RECONNECT_BACKOFF_SECONDS:
            return self._cached_sessions_if_fresh()
        if not await self._ensure_connection(timeout):
            return self._cached_sessions_if_fresh()
        try:
            sessions = await asyncio.wait_for(self._enumerate(), timeout=timeout)
        except (TimeoutError, Exception) as e:  # noqa: BLE001
            log.debug("iTerm enumeration failed, dropping connection: %s", e)
            await self._drop_connection()
            self._last_error_at = time.time()
            return self._cached_sessions_if_fresh()
        self._cached_sessions = sessions
        self._cached_at = time.time()
        return sessions

    def _cached_sessions_if_fresh(self) -> list[ItermSessionInfo]:
        if not self._cached_sessions:
            return []
        if (time.time() - self._cached_at) > _CACHE_TTL_SECONDS:
            return []
        return list(self._cached_sessions)

    async def _enumerate(self) -> list[ItermSessionInfo]:
        out: list[ItermSessionInfo] = []
        assert self._conn is not None
        app = await iterm2.async_get_app(self._conn)
        if app is None:
            return out
        for window in app.windows:
            # Keep IDs as raw strings — iTerm 3.5+ uses UUID-like values like
            # "pty-518AFBF3-77FC-464B-9DB6-5513BC6F53C3" for windows; coercing
            # to int silently zeroed every window_id and killed the Python-API
            # focus path (#22).
            window_id = str(window.window_id)
            for tab in window.tabs:
                tab_id = str(tab.tab_id)
                tab_title = ""
                try:
                    tab_title = await tab.async_get_variable("title") or ""
                except Exception:
                    pass
                for session in tab.sessions:
                    job_pid: int | None = None
                    try:
                        v = await session.async_get_variable("jobPid")
                        if v is not None:
                            job_pid = int(v)
                    except Exception:
                        pass
                    out.append(
                        ItermSessionInfo(
                            window_id=window_id,
                            tab_id=tab_id,
                            session_id=str(session.session_id),
                            tab_title=str(tab_title),
                            job_pid=job_pid,
                        )
                    )
        return out

    async def _drop_connection(self) -> None:
        # #23: iterm2.Connection has no `async_close` in iterm2 >= 2.10. Just
        # drop the reference and let GC close the underlying WebSocket.
        self._conn = None

    async def focus_session(self, session_id: str, timeout: float = 3.0) -> bool:
        """Focus the iTerm session whose `session_id` matches.

        Uses the persistent Python API connection (reused across calls, with the
        same backoff semantics as `get_sessions`). Returns True iff the session
        was found AND all three activations (window, tab, session) completed
        without raising. Returns False otherwise so callers can fall back to
        the AppleScript path (#24).
        """
        if not _ITERM2_AVAILABLE:
            return False
        now = time.time()
        if self._conn is None and (now - self._last_error_at) < _RECONNECT_BACKOFF_SECONDS:
            return False
        if not await self._ensure_connection(timeout):
            return False
        try:
            return await asyncio.wait_for(self._activate(session_id), timeout=timeout)
        except (TimeoutError, Exception) as e:  # noqa: BLE001
            log.debug("iTerm focus_session failed, dropping connection: %s", e)
            await self._drop_connection()
            self._last_error_at = time.time()
            return False

    async def _activate(self, session_id: str) -> bool:
        assert self._conn is not None
        app = await iterm2.async_get_app(self._conn)
        if app is None:
            return False
        for window in app.windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    if str(session.session_id) == session_id:
                        await window.async_activate()
                        await tab.async_select()
                        await session.async_activate()
                        return True
        return False

    async def close(self) -> None:
        async with self._conn_lock:
            await self._drop_connection()
            self._cached_sessions = []
            self._cached_at = 0.0


def _ancestors(pid: int, max_depth: int = 12) -> list[int]:
    chain: list[int] = []
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return chain
    cur = proc
    for _ in range(max_depth):
        try:
            cur = cur.parent()
            if cur is None:
                break
            chain.append(cur.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
    return chain


def link_pids_to_iterm(
    claude_pids: Iterable[int],
    sessions: list[ItermSessionInfo],
) -> dict[int, ItermLocation]:
    """Pure function: map claude PIDs to iTerm locations given pre-enumerated sessions.

    Unlike previous versions, this no longer talks to iTerm itself — pass in
    sessions from `ItermConnectionManager.get_sessions()`.
    """
    if not sessions:
        return {}
    jobpid_to_session: dict[int, ItermSessionInfo] = {s.job_pid: s for s in sessions if s.job_pid is not None}
    out: dict[int, ItermLocation] = {}
    for pid in claude_pids:
        if pid in jobpid_to_session:
            s = jobpid_to_session[pid]
            out[pid] = ItermLocation(s.window_id, s.tab_id, s.session_id, s.tab_title)
            continue
        # Walk up: maybe claude is a child of a shell which is the iterm jobPid.
        for ancestor_pid in _ancestors(pid):
            if ancestor_pid in jobpid_to_session:
                s = jobpid_to_session[ancestor_pid]
                out[pid] = ItermLocation(s.window_id, s.tab_id, s.session_id, s.tab_title)
                break
    return out
