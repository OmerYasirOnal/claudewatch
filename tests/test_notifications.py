"""Tests for backend.notifications — macOS notification helper."""

from __future__ import annotations

from unittest.mock import MagicMock

import backend.notifications as notifications
from backend.notifications import _safe_as, notify


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
