"""Admin endpoint tests.

Mirrors the patterns in ``tests/test_api.py``: use ``TestClient`` against a
``create_app()`` whose scheduler loop is patched to a no-op. We avoid spinning
up uvicorn and avoid hitting the real ``~/.claudewatch`` dir by monkeypatching
``LOGS_DIR`` to a tmp dir per-test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Build the FastAPI app with a tmp_path state DB and no real scheduler.

    Same fixture shape as ``tests/test_api.py::app``; duplicated rather than
    extracted to keep this file self-contained.
    """
    monkeypatch.setattr("backend.config.STATE_DB", tmp_path / "state.db")
    monkeypatch.setattr("backend.server.STATE_DB", tmp_path / "state.db")

    async def _no_scheduler(s):
        return None

    monkeypatch.setattr("backend.server._scheduler_loop", _no_scheduler)

    from backend.server import create_app

    fastapi_app = create_app()

    with TestClient(fastapi_app, base_url="http://127.0.0.1") as client:
        yield client, fastapi_app


def test_status_includes_basic_fields(app):
    client, _ = app
    r = client.get("/api/admin/status")
    assert r.status_code == 200, r.text
    d = r.json()
    # Hit the load-bearing keys; exhaustive shape is documented in the route.
    for key in (
        "version",
        "uptime_seconds",
        "started_at",
        "pid",
        "python_version",
        "active_sessions",
        "history_rows",
        "history_db_size_bytes",
        "log_file",
        "log_file_size_bytes",
        "scheduler",
        "iterm",
    ):
        assert key in d, f"missing key: {key}"
    assert d["version"] == "0.2.0"
    assert isinstance(d["pid"], int) and d["pid"] > 0
    assert isinstance(d["uptime_seconds"], int) and d["uptime_seconds"] >= 0
    assert d["active_sessions"] == 0
    assert d["python_version"].count(".") == 2
    for key in ("process_scan_interval_seconds", "iterm_refresh_interval_seconds"):
        assert key in d["scheduler"]
    assert "connected" in d["iterm"]


def test_logs_returns_last_n_lines(app, tmp_path, monkeypatch):
    client, _ = app
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_file = logs_dir / "server.log"
    log_file.write_text("\n".join(f"line-{i}" for i in range(50)) + "\n")
    monkeypatch.setattr("backend.api.admin.LOGS_DIR", logs_dir)

    r = client.get("/api/admin/logs?lines=10")
    assert r.status_code == 200
    d = r.json()
    assert d["path"] == str(log_file)
    assert d["size_bytes"] == log_file.stat().st_size
    assert d["truncated"] is False
    assert d["lines"] == [f"line-{i}" for i in range(40, 50)]


def test_logs_grep_filters(app, tmp_path, monkeypatch):
    client, _ = app
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_file = logs_dir / "server.log"
    log_file.write_text("alpha\nbeta foo\ngamma\nfoo bar\ndelta\n")
    monkeypatch.setattr("backend.api.admin.LOGS_DIR", logs_dir)

    r = client.get("/api/admin/logs?lines=100&grep=foo")
    assert r.status_code == 200
    d = r.json()
    assert d["lines"] == ["beta foo", "foo bar"]


def test_logs_caps_lines(app):
    client, _ = app
    r = client.get("/api/admin/logs?lines=999999")
    assert r.status_code == 422


def test_logs_handles_missing_file(app, tmp_path, monkeypatch):
    """No server.log on disk → empty response, not a crash."""
    client, _ = app
    logs_dir = tmp_path / "empty-logs"
    logs_dir.mkdir()
    monkeypatch.setattr("backend.api.admin.LOGS_DIR", logs_dir)

    r = client.get("/api/admin/logs?lines=10")
    assert r.status_code == 200
    d = r.json()
    assert d["lines"] == []
    assert d["size_bytes"] == 0
    assert d["truncated"] is False


def test_prune_invokes_state_prune(app):
    client, fastapi_app = app
    s = fastapi_app.state.s

    # State.prune is a coroutine; AsyncMock matches the await semantics.
    s.state.prune = AsyncMock()
    s.state.count_sessions = AsyncMock(side_effect=[7, 2])

    r = client.post("/api/admin/prune?hours=72")
    assert r.status_code == 200, r.text
    assert r.json() == {"rows_deleted": 5}
    s.state.prune.assert_awaited_once_with(hours=72)


def test_prune_clamps_hours(app):
    """``hours`` must be inside FastAPI's Query bounds (1..876000)."""
    client, _ = app
    assert client.post("/api/admin/prune?hours=0").status_code == 422
    assert client.post("/api/admin/prune?hours=-1").status_code == 422


def test_restart_requires_confirm(app, monkeypatch):
    client, _ = app
    killed: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr("backend.api.admin.os.kill", fake_kill)

    # Without confirm → 400 and no kill.
    r = client.post("/api/admin/restart")
    assert r.status_code == 400
    assert killed == []

    # With confirm → 202, body says restart_initiated, kill is scheduled.
    r = client.post("/api/admin/restart?confirm=true")
    assert r.status_code == 202
    assert r.json() == {"restart_initiated": True}

    # The kill happens on a background task that sleeps 100ms; give it room
    # to land before asserting.
    import time as _time

    deadline = _time.time() + 2.0
    while _time.time() < deadline and not killed:
        _time.sleep(0.05)
    assert len(killed) == 1
    import os as _os
    import signal as _signal

    assert killed[0][0] == _os.getpid()
    assert killed[0][1] == _signal.SIGTERM


def test_admin_endpoints_respect_read_only(app):
    client, fastapi_app = app
    fastapi_app.state.s.config["read_only"] = True
    # Read endpoints stay open; write endpoints close.
    assert client.get("/api/admin/status").status_code == 200
    assert client.get("/api/admin/logs?lines=1").status_code == 200
    assert client.post("/api/admin/prune?hours=24").status_code == 403
    assert client.post("/api/admin/restart?confirm=true").status_code == 403
