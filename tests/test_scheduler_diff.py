"""Tests for the SSE diff emitter (issue #5) and the in-loop periodic prune
(issue #4)."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from backend.config import DEFAULT_CONFIG
from backend.models import ClaudeSession, TokenUsage
from backend.server import AppState, _emit_diffs, _maybe_prune, _session_hash


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


# --- #45: hash must exclude monotonic / derived fields ---------------------


def test_session_hash_ignores_cpu_percent():
    """#45: cpu_percent fluctuates every tick; including it in the hash
    defeats the diff optimization entirely."""
    sess = _mk_sess(pid=1)
    base_hash = _session_hash(sess)
    sess.cpu_percent = 99.9
    assert _session_hash(sess) == base_hash


def test_session_hash_ignores_memory_mb():
    sess = _mk_sess(pid=1)
    base_hash = _session_hash(sess)
    sess.memory_mb = 9999.0
    assert _session_hash(sess) == base_hash


def test_session_hash_ignores_duration_seconds():
    sess = _mk_sess(pid=1)
    base_hash = _session_hash(sess)
    sess.duration_seconds = 999999
    assert _session_hash(sess) == base_hash


def test_session_hash_ignores_last_activity_at():
    sess = _mk_sess(pid=1)
    base_hash = _session_hash(sess)
    sess.last_activity_at = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert _session_hash(sess) == base_hash


def test_session_hash_ignores_current_task_elapsed_seconds():
    sess = _mk_sess(pid=1)
    base_hash = _session_hash(sess)
    sess.current_task_elapsed_seconds = 9999
    assert _session_hash(sess) == base_hash


def test_session_hash_changes_on_model_field():
    """Sanity check: meaningful changes still flip the hash."""
    sess = _mk_sess(pid=1)
    base_hash = _session_hash(sess)
    sess.model = "claude-opus-4-7"
    assert _session_hash(sess) != base_hash


def test_session_hash_changes_on_token_usage():
    sess = _mk_sess(pid=1, input_tokens=100)
    sess2 = _mk_sess(pid=1, input_tokens=200)
    assert _session_hash(sess) != _session_hash(sess2)


async def test_emit_diffs_skips_update_when_only_volatile_fields_change():
    """End-to-end: tick where only cpu/memory/duration change must NOT emit
    session.updated. This is the bug #45 was about."""
    s = AppState(config=dict(DEFAULT_CONFIG))
    events = _collect_events(s)

    sess1 = _mk_sess(pid=1)
    await _emit_diffs(s, [sess1])
    events.clear()

    sess2 = _mk_sess(pid=1)
    sess2.cpu_percent = 88.0
    sess2.memory_mb = 4096.0
    sess2.duration_seconds = sess1.duration_seconds + 60
    sess2.last_activity_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    sess2.current_task_elapsed_seconds = 42
    await _emit_diffs(s, [sess2])
    assert events == []


# --- notifications (this PR) -----------------------------------------------


def _mk_sess_with_cost(pid: int, cost: float | None) -> ClaudeSession:
    s = _mk_sess(pid=pid)
    s.usage = TokenUsage(input_tokens=100, cost_estimate_usd=cost)
    return s


def _notify_cfg(**overrides) -> dict:
    cfg = copy.deepcopy(dict(DEFAULT_CONFIG))
    cfg["notifications"] = {
        "enabled": True,
        "on_session_end": True,
        "on_high_cost": True,
        "cost_threshold_usd": 1.0,
    }
    cfg["notifications"].update(overrides)
    return cfg


async def _drain_pending_tasks():
    """Yield to the event loop so notify create_task()s actually run their
    monkeypatched body."""
    # Two passes: first lets create_task schedule; second runs the body.
    for _ in range(3):
        await asyncio.sleep(0)


async def test_high_cost_notification_fires_once(monkeypatch):
    """When cost crosses threshold we notify, then subsequent ticks must not
    re-notify (pid already tracked in notified_high_cost_pids)."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    s = AppState(config=_notify_cfg(cost_threshold_usd=1.0))
    _collect_events(s)

    await _emit_diffs(s, [_mk_sess_with_cost(pid=42, cost=5.0)])
    await _drain_pending_tasks()
    assert mock_notify.await_count == 1
    args, kwargs = mock_notify.call_args
    assert kwargs.get("title") == "Claude session: high cost"
    assert "42" in kwargs.get("message", "")
    assert 42 in s.notified_high_cost_pids

    # Second tick at same/higher cost: must NOT re-notify.
    await _emit_diffs(s, [_mk_sess_with_cost(pid=42, cost=5.5)])
    await _drain_pending_tasks()
    assert mock_notify.await_count == 1


async def test_high_cost_notification_does_not_fire_below_threshold(monkeypatch):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    s = AppState(config=_notify_cfg(cost_threshold_usd=5.0))
    _collect_events(s)

    await _emit_diffs(s, [_mk_sess_with_cost(pid=42, cost=1.0)])
    await _drain_pending_tasks()
    assert mock_notify.await_count == 0
    assert 42 not in s.notified_high_cost_pids


async def test_high_cost_notification_respects_enabled_flag(monkeypatch):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    s = AppState(config=_notify_cfg(enabled=False))
    s.config["notifications"]["enabled"] = False
    _collect_events(s)

    await _emit_diffs(s, [_mk_sess_with_cost(pid=42, cost=100.0)])
    await _drain_pending_tasks()
    assert mock_notify.await_count == 0


async def test_session_end_notification_fires(monkeypatch):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    s = AppState(config=_notify_cfg())
    _collect_events(s)

    await _emit_diffs(s, [_mk_sess(pid=42)])
    # Reset to ignore any other notifies (none expected from a bare session).
    mock_notify.reset_mock()

    await _emit_diffs(s, [])  # pid 42 disappears
    await _drain_pending_tasks()
    assert mock_notify.await_count == 1
    args, kwargs = mock_notify.call_args
    assert kwargs.get("title") == "Claude session ended"
    assert "42" in kwargs.get("message", "")


async def test_session_end_clears_notified_high_cost_pid(monkeypatch):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    s = AppState(config=_notify_cfg(cost_threshold_usd=1.0))
    _collect_events(s)

    await _emit_diffs(s, [_mk_sess_with_cost(pid=42, cost=5.0)])
    await _drain_pending_tasks()
    assert 42 in s.notified_high_cost_pids

    await _emit_diffs(s, [])
    await _drain_pending_tasks()
    assert 42 not in s.notified_high_cost_pids


async def test_session_end_notification_respects_flag(monkeypatch):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    s = AppState(config=_notify_cfg(on_session_end=False))
    _collect_events(s)

    await _emit_diffs(s, [_mk_sess(pid=42)])
    mock_notify.reset_mock()

    await _emit_diffs(s, [])
    await _drain_pending_tasks()
    assert mock_notify.await_count == 0
