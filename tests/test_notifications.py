"""Tests for backend.notifications — macOS notification helper."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import backend.notifications as notifications
from backend.models import ClaudeSession, TokenUsage
from backend.notifications import _safe_as, notify
from backend.server import (
    _format_duration,
    _format_high_cost,
    _format_session_end,
    _project_name,
)


def test_safe_as_escapes_quotes():
    assert _safe_as('he said "hi"') == 'he said \\"hi\\"'


def test_safe_as_escapes_backslashes():
    # Backslash must be escaped first so we don't double-escape the quote
    # escapes we add afterwards.
    assert _safe_as("c:\\foo") == "c:\\\\foo"


def test_safe_as_handles_empty_string():
    assert _safe_as("") == ""


async def test_notify_runs_osascript(monkeypatch):
    """notify() should shell out to osascript with a script that contains the title."""
    calls: list[tuple] = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(notifications.subprocess, "run", fake_run)

    await notify("My Title", "Body message", subtitle="extra")
    assert len(calls) == 1
    cmd = calls[0][0][0]
    assert cmd[0] == "osascript"
    assert cmd[1] == "-e"
    script = cmd[2]
    assert "My Title" in script
    assert "Body message" in script
    assert "extra" in script


async def test_notify_omits_subtitle_clause_when_empty(monkeypatch):
    calls: list[tuple] = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(notifications.subprocess, "run", fake_run)

    await notify("T", "M")
    script = calls[0][0][0][2]
    assert "subtitle" not in script


async def test_notify_swallows_exceptions(monkeypatch):
    """A failing osascript must never propagate — notify is best-effort."""

    def boom(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(notifications.subprocess, "run", boom)

    # Must not raise.
    await notify("T", "M")


async def test_notify_escapes_quotes_in_inputs(monkeypatch):
    calls: list[tuple] = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(notifications.subprocess, "run", fake_run)

    await notify('Title with "quote"', 'Body with "quote"', subtitle='Sub "x"')
    script = calls[0][0][0][2]
    # The literal embedded form must be the escaped one — no bare "quote".
    assert '\\"quote\\"' in script


async def test_notify_accepts_group_kwarg(monkeypatch):
    """group= is currently ignored (no native osascript equivalent) but must
    be accepted so callers can pass it without raising."""
    calls: list[tuple] = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(notifications.subprocess, "run", fake_run)

    await notify("t", "m", subtitle="s", group="my-group")
    assert len(calls) == 1


# --- formatter helpers (server.py) ----------------------------------------


def _mk_session(**overrides) -> ClaudeSession:
    base = {
        "pid": 42,
        "cwd": "/Users/me/Projects/cool-thing",
        "started_at": datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
        "duration_seconds": 323,  # 5m 23s
        "cpu_percent": 0.0,
        "memory_mb": 0.0,
        "status": "idle",
        "location_type": "headless",
        "last_activity_at": datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
        "model": "claude-opus-4-7",
        "usage": TokenUsage(input_tokens=100, output_tokens=50, cost_estimate_usd=12.34),
        "message_count": 7,
    }
    base.update(overrides)
    return ClaudeSession(**base)


def test_project_name_strips_basename():
    assert _project_name("/Users/me/Projects/foo") == "foo"


def test_project_name_handles_trailing_slash():
    assert _project_name("/Users/me/Projects/foo/") == "foo"


def test_project_name_falls_back_when_missing():
    assert _project_name(None) == "(unknown)"
    assert _project_name("") == "(unknown)"


def test_format_duration_seconds_only():
    assert _format_duration(45) == "45s"


def test_format_duration_minutes_and_seconds():
    assert _format_duration(323) == "5m 23s"


def test_format_duration_hours():
    assert _format_duration(3725) == "1h 02m"


def test_format_session_end_includes_project_name():
    sess = _mk_session()
    title, subtitle, message = _format_session_end(sess)
    assert "cool-thing" in title
    assert title.startswith("✅ Claude finished:")


def test_format_session_end_subtitle_has_model_cost_duration():
    sess = _mk_session()
    _, subtitle, _ = _format_session_end(sess)
    assert "claude-opus-4-7" in subtitle
    assert "$12.34" in subtitle
    assert "5m 23s" in subtitle


def test_format_session_end_uses_preview_when_present():
    sess = _mk_session()
    _, _, message = _format_session_end(sess, preview="Done! The tests are all green.")
    assert message == "Done! The tests are all green."


def test_format_session_end_falls_back_to_counts_without_preview():
    sess = _mk_session()
    _, _, message = _format_session_end(sess, preview=None)
    assert "messages" in message
    assert "tokens" in message


def test_format_session_end_handles_missing_cost_and_model():
    sess = _mk_session(model=None, usage=None)
    title, subtitle, message = _format_session_end(sess)
    # Falls back to $0.00 / "(unknown model)" but still renders cleanly.
    assert "$0.00" in subtitle
    assert "unknown model" in subtitle


def test_format_high_cost_includes_current_task():
    sess = _mk_session(current_task_subject="Refactor scheduler loop")
    _, _, message = _format_high_cost(sess)
    assert message == "Refactor scheduler loop"


def test_format_high_cost_title_has_emoji_and_cost():
    sess = _mk_session()
    title, _, _ = _format_high_cost(sess)
    assert title.startswith("⚠️ Claude cost: $12.34")
    assert "cool-thing" in title


def test_format_high_cost_subtitle_has_elapsed():
    sess = _mk_session()
    _, subtitle, _ = _format_high_cost(sess)
    assert "elapsed" in subtitle
    assert "5m 23s" in subtitle


def test_format_high_cost_falls_back_to_token_count():
    sess = _mk_session(current_task_subject=None)
    _, _, message = _format_high_cost(sess)
    assert "tokens used" in message
