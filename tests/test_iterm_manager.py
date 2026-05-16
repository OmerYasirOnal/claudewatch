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
        self.async_activate = AsyncMock()
        self.async_send_text = AsyncMock()

    async def async_get_variable(self, name: str):
        return self._vars.get(name)


class _FakeTab:
    def __init__(self, tab_id: str, sessions: list[_FakeSession]) -> None:
        self.tab_id = tab_id
        self.sessions = sessions
        self.async_select = AsyncMock()

    async def async_get_variable(self, name: str):
        if name == "title":
            return "my-tab"
        return None


class _FakeWindow:
    def __init__(self, window_id: str, tabs: list[_FakeTab]) -> None:
        self.window_id = window_id
        self.tabs = tabs
        self.async_activate = AsyncMock()


class _FakeApp:
    def __init__(self, windows: list[_FakeWindow]) -> None:
        self.windows = windows


def _make_fake_app(job_pid: int = 4242) -> _FakeApp:
    # Use UUID-like string IDs as real iTerm 3.5+ does (#22).
    return _FakeApp(
        windows=[
            _FakeWindow(
                "pty-518AFBF3-77FC-464B-9DB6-5513BC6F53C3",
                [_FakeTab("3", [_FakeSession("sess-x", job_pid)])],
            )
        ]
    )


@pytest.fixture(autouse=True)
def _force_iterm2_available(monkeypatch):
    monkeypatch.setattr(iterm_detector, "_ITERM2_AVAILABLE", True)


async def test_manager_returns_sessions_on_success():
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()

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
    # #22: IDs preserved as strings, including non-numeric UUID-like window IDs.
    assert out[0].window_id == "pty-518AFBF3-77FC-464B-9DB6-5513BC6F53C3"
    assert out[0].tab_id == "3"
    assert mgr.connected is True


async def test_manager_reuses_connection_across_calls():
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
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

    # #23: close() must not call async_close on the iterm2 Connection (it doesn't
    # exist on iterm2 >= 2.10). We rely on GC for the underlying socket.
    await mgr.close()
    assert mgr.connected is False


async def test_focus_session_finds_and_activates():
    """#24: focus_session walks the app tree and activates window, tab, session."""
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    app = _make_fake_app(job_pid=1)
    window = app.windows[0]
    tab = window.tabs[0]
    session = tab.sessions[0]

    with (
        patch.object(
            iterm_detector.iterm2.Connection,
            "async_create",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=app),
        ),
    ):
        result = await mgr.focus_session(session.session_id)

    assert result is True
    window.async_activate.assert_awaited_once()
    tab.async_select.assert_awaited_once()
    session.async_activate.assert_awaited_once()


async def test_focus_session_returns_false_when_not_found():
    """#24: unknown session id → no activations, returns False."""
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    app = _make_fake_app(job_pid=1)
    window = app.windows[0]
    tab = window.tabs[0]
    session = tab.sessions[0]

    with (
        patch.object(
            iterm_detector.iterm2.Connection,
            "async_create",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=app),
        ),
    ):
        result = await mgr.focus_session("sess-does-not-exist")

    assert result is False
    window.async_activate.assert_not_awaited()
    tab.async_select.assert_not_awaited()
    session.async_activate.assert_not_awaited()


async def test_send_text_finds_session_and_sends():
    """send_text walks the app tree and calls async_send_text exactly once."""
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    app = _make_fake_app(job_pid=1)
    session = app.windows[0].tabs[0].sessions[0]

    with (
        patch.object(
            iterm_detector.iterm2.Connection,
            "async_create",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=app),
        ),
    ):
        result = await mgr.send_text(session.session_id, "hello")

    assert result is True
    session.async_send_text.assert_awaited_once_with("hello")


async def test_send_text_returns_false_when_not_found():
    """An unknown session_id must not send to any other session."""
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    app = _make_fake_app(job_pid=1)
    session = app.windows[0].tabs[0].sessions[0]

    with (
        patch.object(
            iterm_detector.iterm2.Connection,
            "async_create",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=app),
        ),
    ):
        result = await mgr.send_text("sess-does-not-exist", "hello")

    assert result is False
    session.async_send_text.assert_not_awaited()


async def test_send_text_drops_connection_on_error():
    """Like focus_session: an exception drops the connection + sets backoff."""
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()
    with (
        patch.object(
            iterm_detector.iterm2.Connection,
            "async_create",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=_make_fake_app(job_pid=1)),
        ),
    ):
        await mgr.get_sessions()
    assert mgr.connected is True

    before = mgr._last_error_at
    with patch.object(
        iterm_detector.iterm2,
        "async_get_app",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await mgr.send_text("anything", "x")
    assert result is False
    assert mgr.connected is False
    assert mgr._last_error_at > before


async def test_focus_session_drops_connection_on_error():
    """#24: on error, the connection is dropped and backoff timestamp is set."""
    mgr = ItermConnectionManager()
    fake_conn = MagicMock()

    # First populate the connection by a successful get_sessions call.
    with (
        patch.object(
            iterm_detector.iterm2.Connection,
            "async_create",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch.object(
            iterm_detector.iterm2,
            "async_get_app",
            new=AsyncMock(return_value=_make_fake_app(job_pid=1)),
        ),
    ):
        await mgr.get_sessions()
    assert mgr.connected is True

    before = mgr._last_error_at
    with patch.object(
        iterm_detector.iterm2,
        "async_get_app",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await mgr.focus_session("anything")

    assert result is False
    assert mgr.connected is False
    assert mgr._last_error_at > before
