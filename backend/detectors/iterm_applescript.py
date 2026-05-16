"""Fallback iTerm enumerator using plain AppleScript (no Python API needed).

Returns (window_id, tab_index, tty, unique_session_id, session_name) tuples.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

APPLESCRIPT_DIR = Path(__file__).resolve().parent.parent / "applescript"
LIST_SCRIPT = APPLESCRIPT_DIR / "list_iterm_sessions.applescript"


@dataclass
class ItermSessionTty:
    window_id: str
    tab_index: int
    tty: str
    unique_id: str
    name: str


@dataclass
class ItermTtyLocation:
    window_id: str
    tab_index: int
    tty: str
    unique_id: str
    name: str


def list_iterm_sessions_via_applescript(timeout: float = 3.0) -> list[ItermSessionTty]:
    """Return iTerm sessions enumerated via plain AppleScript. Returns [] on error."""
    try:
        r = subprocess.run(
            ["osascript", str(LIST_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.debug("osascript list failed: %s", e)
        return []
    if r.returncode != 0:
        log.debug("osascript returned %d: %s", r.returncode, r.stderr.strip())
        return []
    out: list[ItermSessionTty] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        window_id = parts[0].strip()
        if not window_id:
            continue
        try:
            tab_index = int(parts[1])
        except ValueError:
            continue
        out.append(
            ItermSessionTty(
                window_id=window_id,
                tab_index=tab_index,
                tty=parts[2].strip(),
                unique_id=parts[3].strip(),
                name=parts[4].strip(),
            )
        )
    return out


def _pid_tty(pid: int) -> str | None:
    try:
        return psutil.Process(pid).terminal()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _ancestor_ttys(pid: int, max_depth: int = 12) -> list[str]:
    out: list[str] = []
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return out
    cur = proc
    for _ in range(max_depth):
        try:
            t = cur.terminal()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        if t and t not in out:
            out.append(t)
        try:
            parent = cur.parent()
            if parent is None:
                break
            cur = parent
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
    return out


def link_pids_to_iterm_applescript(
    claude_pids: Iterable[int],
    sessions: list[ItermSessionTty] | None = None,
) -> dict[int, ItermTtyLocation]:
    if sessions is None:
        sessions = list_iterm_sessions_via_applescript()
    if not sessions:
        return {}
    by_tty: dict[str, ItermSessionTty] = {s.tty: s for s in sessions if s.tty and s.tty != "?"}
    out: dict[int, ItermTtyLocation] = {}
    for pid in claude_pids:
        for tty in _ancestor_ttys(pid):
            if tty in by_tty:
                s = by_tty[tty]
                out[pid] = ItermTtyLocation(
                    window_id=s.window_id,
                    tab_index=s.tab_index,
                    tty=s.tty,
                    unique_id=s.unique_id,
                    name=s.name,
                )
                break
    return out
