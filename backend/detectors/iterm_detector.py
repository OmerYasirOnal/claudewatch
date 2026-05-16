from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass

import psutil

try:
    import iterm2  # type: ignore

    _ITERM2_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ITERM2_AVAILABLE = False

log = logging.getLogger(__name__)


@dataclass
class ItermSessionInfo:
    window_id: int
    tab_id: int
    session_id: str
    tab_title: str
    job_pid: int | None


@dataclass
class ItermLocation:
    window_id: int
    tab_id: int
    session_id: str
    tab_title: str


async def list_iterm_sessions(timeout: float = 3.0) -> list[ItermSessionInfo]:
    """Connect to iTerm2 Python API and enumerate sessions. Returns [] on any error."""
    if not _ITERM2_AVAILABLE:
        return []
    try:
        return await asyncio.wait_for(_list_iterm_sessions_inner(), timeout=timeout)
    except (TimeoutError, Exception) as e:  # noqa: BLE001
        log.debug("iTerm enumeration failed: %s", e)
        return []


async def _list_iterm_sessions_inner() -> list[ItermSessionInfo]:
    out: list[ItermSessionInfo] = []
    connection = await iterm2.Connection.async_create()
    try:
        app = await iterm2.async_get_app(connection)
        if app is None:
            return out
        for window in app.windows:
            try:
                window_id = int(window.window_id) if str(window.window_id).isdigit() else 0
            except Exception:
                window_id = 0
            for tab in window.tabs:
                try:
                    tab_id_raw = tab.tab_id
                    tab_id = int(tab_id_raw) if str(tab_id_raw).isdigit() else 0
                except Exception:
                    tab_id = 0
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
    finally:
        try:
            await connection.async_close()
        except Exception:
            pass
    return out


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


async def link_pids_to_iterm(
    claude_pids: Iterable[int],
    sessions: list[ItermSessionInfo] | None = None,
) -> dict[int, ItermLocation]:
    if sessions is None:
        sessions = await list_iterm_sessions()
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
