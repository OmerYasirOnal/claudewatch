from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api")


def _state(request: Request):
    return request.app.state.s


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
async def get_log_tail(pid: int, request: Request, limit: int = 20):
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
    entries: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return {"entries": [], "log_path": str(path)}
    entries = entries[-limit:]

    if not show_text:
        for e in entries:
            msg = e.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), list):
                redacted = []
                for block in msg["content"]:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    new_block = {"type": btype}
                    if btype == "tool_use":
                        new_block["name"] = block.get("name")
                    redacted.append(new_block)
                msg["content"] = redacted
    return {"entries": entries, "log_path": str(path), "privacy_mode": not show_text}
