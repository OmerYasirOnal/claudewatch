from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api")

# #47: cap the amount of log we'll parse on every request so a multi-GB JSONL
# can't OOM the server. We read at most the trailing slice of this size.
MAX_LOG_TAIL_BYTES = 5 * 1024 * 1024

# How often /log-stream polls the file for new lines. Keep this low enough that
# the dashboard feels live, high enough that we're not stat'ing the file every
# frame for an idle session.
_LOG_STREAM_POLL_SECONDS = 0.5


def _state(request: Request):
    return request.app.state.s


def _redact_entries(entries: list[dict]) -> list[dict]:
    """Strip text + tool_use input payloads from JSONL entries (privacy mode).

    Shared between /log-tail and /log-stream so both honor the same redaction
    rules — the only delta is the trigger (one-shot vs SSE).
    """
    for e in entries:
        msg = e.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            redacted = []
            for block in msg["content"]:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                new_block: dict[str, Any] = {"type": btype}
                if btype == "tool_use":
                    new_block["name"] = block.get("name")
                redacted.append(new_block)
            msg["content"] = redacted
    return entries


@router.get("/sessions")
async def list_sessions(request: Request) -> list[dict[str, Any]]:
    s = _state(request)
    return [sess.model_dump(mode="json") for sess in s.sessions.values()]


@router.get("/sessions/{pid}")
async def get_session(pid: int, request: Request) -> dict[str, Any]:
    s = _state(request)
    sess = s.sessions.get(pid)
    if not sess:
        raise HTTPException(404, "session not found")
    return sess.model_dump(mode="json")


@router.get("/sessions/{pid}/files")
async def get_files(pid: int, request: Request, minutes: int = 10):
    s = _state(request)
    sess = s.sessions.get(pid)
    if not sess:
        raise HTTPException(404, "session not found")
    if not s.fs_watcher or not sess.cwd:
        return []
    changes = s.fs_watcher.get_recent(sess.cwd, minutes)
    return [c.model_dump(mode="json") for c in changes]


@router.get("/sessions/{pid}/log-tail")
async def get_log_tail(
    pid: int,
    request: Request,
    limit: int = Query(20, ge=1, le=500),
):
    s = _state(request)
    sess = s.sessions.get(pid)
    if not sess:
        raise HTTPException(404, "session not found")
    if not sess.conversation_log_path:
        return {"entries": [], "log_path": None}
    show_text = bool(s.config.get("show_log_text", False))
    path = Path(sess.conversation_log_path)
    if not path.is_file():
        return {"entries": [], "log_path": str(path)}

    def _read_tail() -> list[dict] | None:
        entries: list[dict] = []
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                if size > MAX_LOG_TAIL_BYTES:
                    f.seek(-MAX_LOG_TAIL_BYTES, 2)
                    f.readline()  # discard partial line
                raw = f.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        for line in raw.splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries[-limit:]

    entries = await asyncio.to_thread(_read_tail)
    if entries is None:
        return {"entries": [], "log_path": str(path)}

    if not show_text:
        _redact_entries(entries)
    return {"entries": entries, "log_path": str(path), "privacy_mode": not show_text}


@router.get("/sessions/{pid}/log-stream")
async def stream_log_tail(pid: int, request: Request):
    """SSE stream of new lines appended to the conversation log.

    Emits two event types:
      - ``snapshot`` — the last 20 entries already in the file, sent once at
        connect time so the client can render a starting frame.
      - ``append`` — list of new entries since the last poll. Fired only when
        the file actually grew (size delta > 0).
    """
    s = _state(request)
    sess = s.sessions.get(pid)
    if not sess:
        raise HTTPException(404, "session not found")
    if not sess.conversation_log_path:
        raise HTTPException(404, "no conversation log for session")
    show_text = bool(s.config.get("show_log_text", False))
    log_path = Path(sess.conversation_log_path)

    def _read_snapshot() -> tuple[list[dict], int] | None:
        """Read the tail of the log file for the initial snapshot.

        Mirrors the bounded MAX_LOG_TAIL_BYTES seek-from-end pattern used by
        /log-tail (#87) so a multi-GB conversation log can't OOM the daemon.
        Also returns the end-of-file position so the poll loop knows where
        to resume from.
        """
        try:
            size = log_path.stat().st_size
            with open(log_path, "rb") as f:
                if size > MAX_LOG_TAIL_BYTES:
                    f.seek(-MAX_LOG_TAIL_BYTES, 2)
                    f.readline()  # discard partial line
                raw = f.read()
                end_pos = f.tell()
        except OSError:
            return None
        text = raw.decode("utf-8", errors="replace")
        entries: list[dict] = []
        for line in text.splitlines()[-20:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries, end_pos

    def _read_new(start: int) -> tuple[bytes, int] | None:
        """Read bytes appended since the last poll, returning (chunk, new_pos)."""
        try:
            with open(log_path, "rb") as f:
                f.seek(start)
                chunk = f.read()
                new_pos = f.tell()
        except OSError:
            return None
        return chunk, new_pos

    async def gen():
        # --- initial snapshot --------------------------------------------------
        # Run the file read off the event loop and cap it at MAX_LOG_TAIL_BYTES
        # so a huge JSONL doesn't block the loop or OOM the process (#87).
        snap = await asyncio.to_thread(_read_snapshot)
        if snap is None:
            yield 'event: error\ndata: {"error":"cannot read log"}\n\n'
            return
        entries, pos = snap
        if not show_text:
            _redact_entries(entries)
        yield f"event: snapshot\ndata: {json.dumps({'entries': entries})}\n\n"

        # --- poll loop ---------------------------------------------------------
        shutdown_event = getattr(s, "shutdown_event", None)
        while True:
            if await request.is_disconnected():
                break
            if shutdown_event is not None and shutdown_event.is_set():
                break
            await asyncio.sleep(_LOG_STREAM_POLL_SECONDS)
            # Even the incremental poll touches disk — keep it off the loop
            # so a stalled fs doesn't freeze the whole daemon (#87).
            result = await asyncio.to_thread(_read_new, pos)
            if result is None:
                continue
            chunk, pos = result
            if not chunk:
                continue
            new_lines = chunk.decode("utf-8", errors="replace").splitlines()
            new_entries: list[dict] = []
            for line in new_lines:
                try:
                    new_entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            if not show_text:
                _redact_entries(new_entries)
            if new_entries:
                yield f"event: append\ndata: {json.dumps({'entries': new_entries})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
