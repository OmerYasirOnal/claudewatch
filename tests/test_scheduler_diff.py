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


async def test_pid_reuse_emits_ended_and_started_in_same_tick():
    """#93: when a session ends and a new claude is spawned with the same
    PID before the next tick, the diff must surface session.ended for the
    old one AND session.started for the new one (identity is composite
    ``(pid, started_at)``)."""
    s = AppState(config=dict(DEFAULT_CONFIG))
    events = _collect_events(s)

    old = _mk_sess(pid=1)
    old.started_at = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    await _emit_diffs(s, [old])
    events.clear()

    new = _mk_sess(pid=1)
    new.started_at = datetime(2026, 5, 12, 11, 0, 0, tzinfo=timezone.utc)
    await _emit_diffs(s, [new])

    kinds = [e["event"] for e in events]
    assert "session.ended" in kinds
    assert "session.started" in kinds
    # The hash for the new session must be tracked (and not wiped by the
    # ended branch popping the pid-keyed entry).
    assert 1 in s.session_hashes


async def test_pid_reuse_clears_notified_high_cost_for_new_session():
    """#93: notified_high_cost_pids is pid-keyed; PID reuse must reset it
    so the new session gets its own cost notification when crossing the
    threshold."""
    s = AppState(config=dict(DEFAULT_CONFIG))
    _collect_events(s)

    old = _mk_sess(pid=1)
    old.started_at = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    s.notified_high_cost_pids.add(1)
    await _emit_diffs(s, [old])

    new = _mk_sess(pid=1)
    new.started_at = datetime(2026, 5, 12, 11, 0, 0, tzinfo=timezone.utc)
    await _emit_diffs(s, [new])

    assert 1 not in s.notified_high_cost_pids


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
    monkeypatched body. The session-end task awaits asyncio.to_thread (for
    the log preview read), so we also wait for any non-current tasks to
    complete before returning."""
    # First yield a few times so create_task() bodies get scheduled.
    for _ in range(3):
        await asyncio.sleep(0)
    # Then explicitly wait on every still-pending task (excluding ours).
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=2.0)


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
    _, kwargs = mock_notify.call_args
    # New format: title includes warning emoji + dollar cost + project name.
    assert kwargs.get("title", "").startswith("⚠️ Claude cost: $5.00")
    # Subtitle carries model · duration; cwd "/tmp" → project name "tmp".
    assert "tmp" in kwargs.get("title", "")
    assert "elapsed" in kwargs.get("subtitle", "")
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
    _, kwargs = mock_notify.call_args
    # New rich format: project name in title, model/cost/duration in subtitle,
    # message falls back to "<n> messages, <n> tokens" when no log preview.
    title = kwargs.get("title", "")
    assert title.startswith("✅ Claude finished:")
    assert "tmp" in title  # cwd is "/tmp" → basename "tmp"
    assert "messages" in kwargs.get("message", "")


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


async def test_session_end_notification_uses_log_preview(monkeypatch, tmp_path):
    """When the conversation log has an assistant text block, the message
    body must be that preview (not the count-based fallback)."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    s = AppState(config=_notify_cfg())
    _collect_events(s)

    log_file = tmp_path / "preview.jsonl"
    log_file.write_text(
        '{"type":"user","message":{"content":"hi"}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"All done, tests are green."}]}}\n'
    )
    sess = _mk_sess(pid=42)
    sess.conversation_log_path = str(log_file)
    await _emit_diffs(s, [sess])
    mock_notify.reset_mock()

    await _emit_diffs(s, [])
    await _drain_pending_tasks()
    assert mock_notify.await_count == 1
    _, kwargs = mock_notify.call_args
    assert kwargs.get("message") == "All done, tests are green."


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
