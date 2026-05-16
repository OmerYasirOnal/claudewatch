"""Tests for the SSE diff emitter (issue #5) and the in-loop periodic prune
(issue #4)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from backend.config import DEFAULT_CONFIG
from backend.models import ClaudeSession, TokenUsage
from backend.server import AppState, _emit_diffs, _maybe_prune


def _mk_sess(pid: int, input_tokens: int = 0) -> ClaudeSession:
    return ClaudeSession(
        pid=pid,
        cwd="/tmp",
        started_at=datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc),
        duration_seconds=10,
        cpu_percent=0.0,
        memory_mb=100.0,
        status="idle",
        location_type="headless",
        last_activity_at=datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc),
        usage=TokenUsage(input_tokens=input_tokens),
    )


def _collect_events(s: AppState) -> list[dict]:
    out: list[dict] = []

    async def _capture(event):
        out.append(event)

    s.broadcast = _capture  # type: ignore[assignment]
    return out


async def test_session_started_fires_on_new_session():
    s = AppState(config=dict(DEFAULT_CONFIG))
    events = _collect_events(s)

    await _emit_diffs(s, [_mk_sess(pid=1)])

    assert len(events) == 1
    assert events[0]["event"] == "session.started"


async def test_session_updated_does_not_fire_when_unchanged():
    s = AppState(config=dict(DEFAULT_CONFIG))
    events = _collect_events(s)
    sess = _mk_sess(pid=1)

    await _emit_diffs(s, [sess])
    events.clear()

    # Same session, no changes.
    await _emit_diffs(s, [_mk_sess(pid=1)])
    assert events == []


async def test_session_updated_fires_when_token_count_changes():
    s = AppState(config=dict(DEFAULT_CONFIG))
    events = _collect_events(s)

    await _emit_diffs(s, [_mk_sess(pid=1, input_tokens=100)])
    events.clear()

    await _emit_diffs(s, [_mk_sess(pid=1, input_tokens=200)])
    assert len(events) == 1
    assert events[0]["event"] == "session.updated"
    assert events[0]["session"]["usage"]["input_tokens"] == 200


async def test_session_ended_fires_when_pid_disappears():
    s = AppState(config=dict(DEFAULT_CONFIG))
    events = _collect_events(s)

    await _emit_diffs(s, [_mk_sess(pid=1)])
    events.clear()

    await _emit_diffs(s, [])
    assert len(events) == 1
    assert events[0] == {"event": "session.ended", "pid": 1}
    assert 1 not in s.session_hashes


async def test_session_hash_evicted_on_end_so_re_start_emits_started():
    s = AppState(config=dict(DEFAULT_CONFIG))
    events = _collect_events(s)
    await _emit_diffs(s, [_mk_sess(pid=1)])
    await _emit_diffs(s, [])  # ends
    events.clear()

    # Same pid comes back: must be session.started, not session.updated.
    await _emit_diffs(s, [_mk_sess(pid=1)])
    assert len(events) == 1
    assert events[0]["event"] == "session.started"


async def test_maybe_prune_first_call_only_sets_clock(monkeypatch):
    """The very first _maybe_prune call after lifespan startup should not re-prune
    (lifespan already pruned at startup) — it should just initialize the timer."""
    s = AppState(config=dict(DEFAULT_CONFIG))
    s.state = type("FakeState", (), {"prune": AsyncMock()})()
    s.last_prune_at = 0.0

    now_holder = {"t": 1_000_000.0}
    monkeypatch.setattr("backend.server.time.time", lambda: now_holder["t"])

    await _maybe_prune(s)
    s.state.prune.assert_not_awaited()
    assert s.last_prune_at == 1_000_000.0


async def test_maybe_prune_skips_within_window(monkeypatch):
    s = AppState(config=dict(DEFAULT_CONFIG))
    s.state = type("FakeState", (), {"prune": AsyncMock()})()
    s.last_prune_at = 1_000_000.0

    monkeypatch.setattr("backend.server.time.time", lambda: 1_000_000.0 + 500)

    await _maybe_prune(s)
    s.state.prune.assert_not_awaited()


async def test_maybe_prune_runs_after_one_hour(monkeypatch):
    s = AppState(config=dict(DEFAULT_CONFIG))
    s.state = type("FakeState", (), {"prune": AsyncMock()})()
    s.last_prune_at = 1_000_000.0

    monkeypatch.setattr("backend.server.time.time", lambda: 1_000_000.0 + 3601)

    await _maybe_prune(s)
    s.state.prune.assert_awaited_once()
    assert s.last_prune_at == 1_000_000.0 + 3601


async def test_maybe_prune_no_state_is_noop():
    s = AppState(config=dict(DEFAULT_CONFIG), state=None)
    s.last_prune_at = 0.0
    # Should not raise.
    await _maybe_prune(s)
