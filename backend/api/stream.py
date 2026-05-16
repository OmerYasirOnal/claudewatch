from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api")


@router.get("/stream")
async def stream(request: Request):
    s = request.app.state.s
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    s.sse_queues.add(queue)

    async def gen():
        try:
            # Initial snapshot
            yield _sse_event(
                "snapshot",
                {"sessions": [sess.model_dump(mode="json") for sess in s.sessions.values()]},
            )
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield _sse_event(msg.get("event", "message"), msg)
                except TimeoutError:
                    yield ":keepalive\n\n"
        finally:
            s.sse_queues.discard(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(name: str, data) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"
