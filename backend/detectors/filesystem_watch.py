from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watchfiles import Change, awatch

from backend.models import FileChange

log = logging.getLogger(__name__)

_CHANGE_MAP = {
    Change.added: "created",
    Change.modified: "modified",
    Change.deleted: "deleted",
}


class FilesystemWatcher:
    def __init__(self, retention_minutes: int = 10, ignore_patterns: list[str] | None = None) -> None:
        self.retention_minutes = retention_minutes
        self.ignore_patterns = ignore_patterns or []
        self.changes: dict[str, deque[FileChange]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop_events: dict[str, asyncio.Event] = {}

    def _should_ignore(self, rel_path: str) -> bool:
        for pat in self.ignore_patterns:
            if pat.endswith("/"):
                if rel_path.startswith(pat) or ("/" + pat) in ("/" + rel_path):
                    return True
            else:
                if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(Path(rel_path).name, pat):
                    return True
        return False

    def get_recent(self, cwd: str, minutes: int | None = None) -> list[FileChange]:
        m = minutes if minutes is not None else self.retention_minutes
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=m)
        return [c for c in self.changes.get(cwd, deque()) if c.ts >= cutoff]

    async def sync_active_cwds(self, cwds: set[str]) -> None:
        cwds = {c for c in cwds if c and Path(c).is_dir()}
        for cwd in list(self._tasks.keys()):
            if cwd not in cwds:
                await self._stop(cwd)
        for cwd in cwds:
            if cwd not in self._tasks:
                self._start(cwd)

    def _start(self, cwd: str) -> None:
        stop_event = asyncio.Event()
        self._stop_events[cwd] = stop_event
        self.changes.setdefault(cwd, deque(maxlen=2000))
        self._tasks[cwd] = asyncio.create_task(self._watch(cwd, stop_event))

    async def _stop(self, cwd: str) -> None:
        ev = self._stop_events.pop(cwd, None)
        if ev:
            ev.set()
        task = self._tasks.pop(cwd, None)
        if task:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()

    async def stop_all(self) -> None:
        for cwd in list(self._tasks.keys()):
            await self._stop(cwd)

    async def _watch(self, cwd: str, stop_event: asyncio.Event) -> None:
        base = Path(cwd).resolve()
        try:
            async for changes in awatch(str(base), stop_event=stop_event, recursive=True):
                now = datetime.now(timezone.utc)
                dq = self.changes.setdefault(cwd, deque(maxlen=2000))
                cutoff = now - timedelta(minutes=self.retention_minutes)
                while dq and dq[0].ts < cutoff:
                    dq.popleft()
                for change_type, raw_path in changes:
                    try:
                        rel = str(Path(raw_path).resolve().relative_to(base))
                    except ValueError:
                        rel = raw_path
                    if self._should_ignore(rel):
                        continue
                    kind = _CHANGE_MAP.get(change_type, "modified")
                    dq.append(FileChange(path=rel, kind=kind, ts=now))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("watch error for %s: %s", cwd, e)
