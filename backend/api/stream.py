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
    # Surface live subscriber count for /api/metrics. Kept on AppState.metrics
    # if present so legacy AppState instances (e.g. older tests) don't blow up.
    _metrics = getattr(s, "metrics", None)
    if _metrics is not None:
        _metrics.sse_subscribers += 1

    async def gen():
        try:
            # Initial snapshot
            yield _sse_event(
                "snapshot",
                {"sessions": [sess.model_dump(mode="json") for sess in s.sessions.values()]},
            )
            keepalive_interval = 15.0
            while True:
                if await request.is_disconnected():
                    break
                # Issue #27: race the queue against the app's shutdown_event
                # so daemon stop wakes us immediately instead of waiting up to
                # 15s for the next keepalive timeout.
                get_task = asyncio.create_task(queue.get())
                shutdown_task = asyncio.create_task(s.shutdown_event.wait())
                try:
                    done, pending = await asyncio.wait(
                        {get_task, shutdown_task},
                        timeout=keepalive_interval,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for t in (get_task, shutdown_task):
                        if not t.done():
                            t.cancel()
                if shutdown_task in done:
                    # Server is shutting down — exit the generator so the
                    # response can finish before the worker dies.
                    break
                if get_task in done:
                    msg = get_task.result()
                    yield _sse_event(msg.get("event", "message"), msg)
                else:
                    # Timed out without either event firing — emit a keepalive
                    # so proxies / curl pipes don't drop the connection.
                    yield ":keepalive\n\n"
        finally:
            s.sse_queues.discard(queue)
            if _metrics is not None and _metrics.sse_subscribers > 0:
                _metrics.sse_subscribers -= 1

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
