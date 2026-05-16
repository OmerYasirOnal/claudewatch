"""Safety / resilience tests for the server runtime.

Covers:
- #28: `_safe_float` must tolerate junk config values without killing the
  scheduler.
- #27: the SSE `gen` coroutine must exit promptly when `AppState.shutdown_event`
  fires, instead of waiting for its keepalive timeout.
- #43: `AppState.broadcast` on a full queue must drain + push reconnect
  instead of discarding silently.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from backend.config import DEFAULT_CONFIG
from backend.server import AppState, _safe_float

# --- #28: _safe_float -------------------------------------------------------


def test_safe_float_accepts_int():
    assert _safe_float(2, default=5.0) == 2.0


def test_safe_float_accepts_float():
    assert _safe_float(3.14, default=5.0) == 3.14


def test_safe_float_accepts_numeric_string():
    assert _safe_float("7.5", default=5.0) == 7.5


def test_safe_float_rejects_garbage_string(caplog):
    with caplog.at_level(logging.WARNING, logger="claudewatch"):
        assert _safe_float("abc", default=2.0) == 2.0
    assert any("Invalid config value" in r.message for r in caplog.records)


def test_safe_float_rejects_none(caplog):
    with caplog.at_level(logging.WARNING, logger="claudewatch"):
        assert _safe_float(None, default=2.0) == 2.0
    assert any("Invalid config value" in r.message for r in caplog.records)


def test_safe_float_rejects_below_min(caplog):
    """Values below min_val (e.g. 0.0 from misconfig) must fall back so the
    scheduler doesn't tight-loop."""
    with caplog.at_level(logging.WARNING, logger="claudewatch"):
        # 0.0 < default min_val of 0.1
        assert _safe_float(0.0, default=2.0) == 2.0


def test_safe_float_respects_custom_min():
    assert _safe_float(0.5, default=10.0, min_val=1.0) == 10.0
    assert _safe_float(1.5, default=10.0, min_val=1.0) == 1.5


def test_safe_float_rejects_bool_like_object():
    """A weird object that raises TypeError on float() should fall back."""

    class Weird:
        def __float__(self):
            raise TypeError("nope")

    assert _safe_float(Weird(), default=4.0) == 4.0


# --- #43: broadcast on QueueFull --------------------------------------------


async def test_broadcast_drains_and_signals_reconnect_when_full():
    s = AppState(config=dict(DEFAULT_CONFIG))
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    # Pre-fill the queue.
    q.put_nowait({"event": "old-1"})
    q.put_nowait({"event": "old-2"})
    s.sse_queues.add(q)

    await s.broadcast({"event": "session.updated", "payload": "x"})

    # Old events were drained; only a reconnect-required marker remains.
    msg = q.get_nowait()
    assert msg == {"event": "reconnect-required"}
    assert q.empty()
    # Queue is still tracked — we want the client to reconnect, not be evicted.
    assert q in s.sse_queues


async def test_broadcast_delivers_normally_when_queue_has_room():
    s = AppState(config=dict(DEFAULT_CONFIG))
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    s.sse_queues.add(q)

    await s.broadcast({"event": "session.started", "x": 1})

    msg = q.get_nowait()
    assert msg == {"event": "session.started", "x": 1}


# --- #27: SSE generator wakes on shutdown ----------------------------------


async def test_sse_gen_exits_promptly_on_shutdown():
    """End-to-end-ish: drive the gen() coroutine ourselves with a fake
    AppState. The generator should yield its initial snapshot, then break
    out of its loop within milliseconds of `shutdown_event.set()`."""
    from backend.api.stream import stream as stream_route

    s = AppState(config=dict(DEFAULT_CONFIG))

    class FakeApp:
        class state:
            pass

    fake_app = FakeApp()
    fake_app.state.s = s

    class FakeRequest:
        def __init__(self, app):
            self.app = app

        async def is_disconnected(self):
            return False

    # The route returns a StreamingResponse whose body_iterator is the gen.
    resp = await stream_route(FakeRequest(fake_app))
    body_iter = resp.body_iterator

    # Pull the initial snapshot — proves the generator is running.
    first = await body_iter.__anext__()
    assert "snapshot" in first

    # Schedule shutdown shortly, then await the next chunk. Without the fix
    # this would block for up to 15s on the keepalive timeout; with the fix
    # the generator exits immediately when the event fires.
    async def fire_shutdown():
        await asyncio.sleep(0.05)
        s.shutdown_event.set()

    fire_task = asyncio.create_task(fire_shutdown())
    try:
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(body_iter.__anext__(), timeout=2.0)
    finally:
        await fire_task

    # And the queue should be cleaned up.
    assert not s.sse_queues


async def test_sse_gen_yields_message_when_queue_event_arrives():
    """Sanity: when a normal event arrives before shutdown, the generator
    should yield it formatted as an SSE event, not break out of the loop."""
    from backend.api.stream import stream as stream_route

    s = AppState(config=dict(DEFAULT_CONFIG))

    class FakeApp:
        class state:
            pass

    fake_app = FakeApp()
    fake_app.state.s = s

    class FakeRequest:
        def __init__(self, app):
            self.app = app

        async def is_disconnected(self):
            return False

    resp = await stream_route(FakeRequest(fake_app))
    body_iter = resp.body_iterator

    # snapshot
    _ = await body_iter.__anext__()

    # Push an event onto the (single) queue this request created.
    assert len(s.sse_queues) == 1
    q = next(iter(s.sse_queues))
    await q.put({"event": "session.updated", "session": {"pid": 1}})

    chunk = await asyncio.wait_for(body_iter.__anext__(), timeout=1.0)
    assert "event: session.updated" in chunk
    assert '"pid": 1' in chunk

    # Cleanup: fire shutdown and drain.
    s.shutdown_event.set()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(body_iter.__anext__(), timeout=2.0)
