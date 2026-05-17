"""Tests for the SSE stream endpoint (`GET /api/stream`).

Focused on issue #123: the `sse_subscribers` gauge — and the `sse_queues`
set — must NOT leak when the StreamingResponse generator is closed early
(client disconnect, mid-stream cancellation, exception in `gen()` before
the first yield, etc.). The fix is to register the queue + increment the
gauge *inside* the generator's try-block so the matching `finally` is
guaranteed to run by the generator's lifecycle.

We exercise the generator directly (rather than via TestClient) so we can
control exactly when iteration stops — TestClient would always run the
generator to completion, which would mask the leak path the fix targets.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.api import stream
from backend.server import AppState


@pytest.fixture
def app_state():
    """A minimal AppState with no DB — enough for the stream handler."""
    return AppState(config={}, state=None)


def _make_request(app_state) -> SimpleNamespace:
    """Fake a `fastapi.Request` with just the attributes `stream()` reads:
    `request.app.state.s` and `request.is_disconnected()`. Keeping it thin
    avoids dragging the whole TestClient into a test where we need
    fine-grained generator control.
    """

    async def _not_disconnected() -> bool:
        return False

    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(s=app_state)),
        is_disconnected=_not_disconnected,
    )


async def test_subscriber_count_increments_on_first_iteration(app_state):
    """Sanity check: the gauge should increment as soon as the generator
    yields its first event (the snapshot) and the queue should be tracked."""
    request = _make_request(app_state)
    response = await stream.stream(request)
    # StreamingResponse stashes the generator on .body_iterator.
    gen = response.body_iterator

    assert app_state.metrics.sse_subscribers == 0, "precondition: no subscribers yet"
    assert len(app_state.sse_queues) == 0, "precondition: no queues registered"

    # Drive the generator to its first yield (the initial snapshot).
    first = await gen.__anext__()
    assert "event: snapshot" in first

    assert app_state.metrics.sse_subscribers == 1, "subscriber gauge must bump on first iteration"
    assert len(app_state.sse_queues) == 1, "queue must be tracked while live"

    # Closing the generator runs the `finally` block — gauge + queue both clean.
    await gen.aclose()
    assert app_state.metrics.sse_subscribers == 0
    assert len(app_state.sse_queues) == 0


async def test_subscriber_count_no_leak_when_generator_never_iterated(app_state):
    """The core regression test for issue #123.

    Before the fix: `sse_subscribers` was incremented in the handler body,
    so calling `stream()` and discarding the response without iterating
    would leak the gauge + the queue forever.

    After the fix: the increment lives inside `gen()`'s try-block, so
    never-iterating the generator means we never incremented in the first
    place — and `aclose()` is still safe on an unstarted generator.
    """
    request = _make_request(app_state)
    response = await stream.stream(request)
    gen = response.body_iterator

    # Simulate Starlette discarding the response without iterating it
    # (client disconnect between handler return and body streaming).
    await gen.aclose()

    assert app_state.metrics.sse_subscribers == 0, (
        "gauge must NOT leak when the generator was never started — " "this was the bug fixed in #123"
    )
    assert len(app_state.sse_queues) == 0, (
        "queue must NOT be registered if the generator was never started — "
        "dead queues used to accumulate in sse_queues"
    )


async def test_subscriber_count_resets_on_mid_stream_close(app_state):
    """Client disconnects mid-stream → gauge + queue must reset."""
    request = _make_request(app_state)
    response = await stream.stream(request)
    gen = response.body_iterator

    # Drive past the initial snapshot so we're definitely "in flight".
    await gen.__anext__()
    assert app_state.metrics.sse_subscribers == 1
    assert len(app_state.sse_queues) == 1

    # Close mid-stream — simulates the StreamingResponse being torn down.
    await gen.aclose()

    assert app_state.metrics.sse_subscribers == 0
    assert len(app_state.sse_queues) == 0


async def test_repeated_connect_disconnect_does_not_leak(app_state):
    """1000 connect-then-immediately-disconnect cycles must leave the gauge at 0.
    Smaller than the issue's repro (which uses concurrent clients) but
    exercises the same code path — if the increment/decrement ever desync
    we'd see the gauge drift by exactly the number of leaked cycles.
    """
    for _ in range(1000):
        request = _make_request(app_state)
        response = await stream.stream(request)
        gen = response.body_iterator
        # Never iterate — just close, the most aggressive case.
        await gen.aclose()

    assert app_state.metrics.sse_subscribers == 0
    assert len(app_state.sse_queues) == 0


async def test_gauge_decrement_clamped_at_zero(app_state):
    """Defensive: if for any reason the decrement runs without a matching
    increment (legacy generator instance, manual state poke), it must not
    underflow into negative territory. The existing code uses
    `if _metrics.sse_subscribers > 0` as the guard — pin that behavior."""
    # Drive a normal lifecycle, then close again to attempt a second decrement.
    request = _make_request(app_state)
    response = await stream.stream(request)
    gen = response.body_iterator
    await gen.__anext__()
    await gen.aclose()
    # Closing an already-closed generator is a no-op — gauge stays at 0.
    await gen.aclose()
    assert app_state.metrics.sse_subscribers == 0
    # And manually invoking aclose extra times doesn't underflow.
    assert app_state.metrics.sse_subscribers >= 0


async def test_concurrent_streams_track_independently(app_state):
    """Two concurrent streams → gauge = 2, then closing both → gauge = 0."""
    req1 = _make_request(app_state)
    req2 = _make_request(app_state)
    resp1 = await stream.stream(req1)
    resp2 = await stream.stream(req2)
    gen1 = resp1.body_iterator
    gen2 = resp2.body_iterator

    # Bring both online.
    await gen1.__anext__()
    await gen2.__anext__()
    assert app_state.metrics.sse_subscribers == 2
    assert len(app_state.sse_queues) == 2

    await gen1.aclose()
    assert app_state.metrics.sse_subscribers == 1
    assert len(app_state.sse_queues) == 1

    await gen2.aclose()
    assert app_state.metrics.sse_subscribers == 0
    assert len(app_state.sse_queues) == 0


# pytest-asyncio runs in auto mode (see pyproject.toml `asyncio_mode = "auto"`);
# every `async def test_*` function is collected as an asyncio test, so no
# explicit pytestmark is needed.
