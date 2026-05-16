"""Tests for ItermConnectionManager — covers connection reuse, backoff after
failure, and cache return during the backoff window."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.detectors import iterm_detector
from backend.detectors.iterm_detector import ItermConnectionManager, ItermSessionInfo


class _FakeSession:
    def __init__(self, session_id: str, job_pid: int | None) -> None:
        self.session_id = session_id
        self._vars = {"jobPid": job_pid}

    async def async_get_variable(self, name: str):
        return self._vars.get(name)


class _FakeTab:
    def __init__(self, tab_id: int, sessions: list[_FakeSession]) -> None:
        self.tab_id = tab_id
        self.sessions = sessions

    async def async_get_variable(self, name: str):
        if name == "title":
            return "my-tab"
        return None


class _FakeWindow:
    def __init__(self, window_id: int, tabs: list[_FakeTab]) -> None:
        self.window_id = window_id
        self.tabs = tabs


class _FakeApp:
    def __init__(self, windows: list[_FakeWindow]) -> None:
        self.windows = windows


def _make_fake_app(job_pid: int = 4242) -> _FakeApp:
    return _FakeApp(windows=[_FakeWindow(1, [_FakeTab(2, [_FakeSession("sess-x", job_pid)])])])


@pytest.fixture(autouse=True)
def _force_iterm2_available(monkeypatch):
    monkeypatch.setattr(iterm_detector, "_ITERM2_AVAILABLE", True)


async def test_manager_returns_sessions_on_success():
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    fake_conn.async_close = AsyncMock()

    with (
        patch.object(
            iterm_detector.iterm2.Connection,
            "async_create",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=_make_fake_app(job_pid=4242)),
        ),
    ):
        out = await mgr.get_sessions()

    assert len(out) == 1
    assert isinstance(out[0], ItermSessionInfo)
    assert out[0].job_pid == 4242
    assert mgr.connected is True


async def test_manager_reuses_connection_across_calls():
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    fake_conn.async_close = AsyncMock()
    create_mock = AsyncMock(return_value=fake_conn)

    with (
        patch.object(iterm_detector.iterm2.Connection, "async_create", new=create_mock),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=_make_fake_app(job_pid=1)),
        ),
    ):
        await mgr.get_sessions()
        await mgr.get_sessions()
        await mgr.get_sessions()

    assert create_mock.await_count == 1  # connection reused, not recreated


async def test_manager_backoff_after_failure_returns_cache():
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    fake_conn.async_close = AsyncMock()
    create_mock = AsyncMock(return_value=fake_conn)

    # First call succeeds, populating the cache.
    with (
        patch.object(iterm_detector.iterm2.Connection, "async_create", new=create_mock),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=_make_fake_app(job_pid=99)),
        ),
    ):
        first = await mgr.get_sessions()
    assert len(first) == 1

    # Second call: async_get_app raises. The connection should be dropped, the
    # error timestamp set, and the cached result returned.
    with (
        patch.object(iterm_detector.iterm2.Connection, "async_create", new=create_mock),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        second = await mgr.get_sessions()
    assert mgr.connected is False
    assert len(second) == 1  # cached
    assert second[0].job_pid == 99

    # Third call while inside backoff window: must NOT attempt a new connection.
    create_mock_2 = AsyncMock(return_value=fake_conn)
    with patch.object(iterm_detector.iterm2.Connection, "async_create", new=create_mock_2):
        third = await mgr.get_sessions()
    assert create_mock_2.await_count == 0
    assert len(third) == 1


async def test_manager_backoff_returns_empty_when_no_cache():
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    fake_conn.async_close = AsyncMock()
    create_mock = AsyncMock(return_value=fake_conn)

    # First call fails before we ever cached anything.
    with (
        patch.object(iterm_detector.iterm2.Connection, "async_create", new=create_mock),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(side_effect=RuntimeError("nope")),
        ),
    ):
        out = await mgr.get_sessions()
    assert out == []

    # Inside backoff window: no reconnect attempt, no cached data.
    create_mock_2 = AsyncMock(return_value=fake_conn)
    with patch.object(iterm_detector.iterm2.Connection, "async_create", new=create_mock_2):
        out2 = await mgr.get_sessions()
    assert out2 == []
    assert create_mock_2.await_count == 0


async def test_manager_recovers_after_backoff_window():
    """After the backoff window passes, a new connection attempt should be made."""
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    fake_conn.async_close = AsyncMock()

    # First call fails.
    with (
        patch.object(
            iterm_detector.iterm2.Connection,
            "async_create",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        await mgr.get_sessions()

    # Move the error timestamp back so we're outside the backoff window.
    mgr._last_error_at = time.time() - 999

    # Now a fresh call should attempt a new connection and succeed.
    create_mock = AsyncMock(return_value=fake_conn)
    with (
        patch.object(iterm_detector.iterm2.Connection, "async_create", new=create_mock),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=_make_fake_app(job_pid=7)),
        ),
    ):
        out = await mgr.get_sessions()
    assert create_mock.await_count == 1
    assert len(out) == 1
    assert out[0].job_pid == 7


async def test_manager_close_drops_connection():
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    fake_conn.async_close = AsyncMock()

    with (
        patch.object(
            iterm_detector.iterm2.Connection,
            "async_create",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=_make_fake_app()),
        ),
    ):
        await mgr.get_sessions()
    assert mgr.connected is True

    await mgr.close()
    assert mgr.connected is False
    fake_conn.async_close.assert_awaited()
