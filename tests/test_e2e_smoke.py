"""End-to-end integration smoke tests for the FastAPI app.

Multi-step flows exercised via ``TestClient``: no real daemon, no real iTerm,
no real network. External I/O (subprocess.run for git, iterm_manager,
notification dispatch) is either mocked or pointed at a hermetic tmp_path
git repo.

These complement the focused per-router tests in ``test_api.py`` /
``test_files_api.py`` / ``test_insights.py`` — they're about ensuring the
pieces compose, not about edge-case correctness of any single endpoint.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models import ClaudeSession, TokenUsage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mk_session(pid: int, cwd: str, *, tokens: int = 1000, cost: float = 0.5) -> ClaudeSession:
    now = datetime.now(timezone.utc)
    return ClaudeSession(
        pid=pid,
        cwd=cwd,
        started_at=now,
        last_activity_at=now,
        model="claude-opus-4-7",
        message_count=4,
        usage=TokenUsage(
            input_tokens=tokens // 2,
            output_tokens=tokens // 2,
            cost_estimate_usd=cost,
        ),
    )


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Full FastAPI app with the scheduler loop neutered.

    Mirrors the fixture in test_api.py — we don't want the real lifespan to
    spin up the process scanner or the iTerm refresh loop during a test."""
    monkeypatch.setattr("backend.config.STATE_DB", tmp_path / "state.db")
    monkeypatch.setattr("backend.server.STATE_DB", tmp_path / "state.db")
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    async def _no_scheduler(s):
        return None

    async def _no_iterm(s):
        return None

    monkeypatch.setattr("backend.server._scheduler_loop", _no_scheduler)
    monkeypatch.setattr("backend.server._iterm_refresh_loop", _no_iterm)

    from backend.server import create_app

    fastapi_app = create_app()
    with TestClient(fastapi_app, base_url="http://127.0.0.1") as client:
        yield client, fastapi_app


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def test_session_lifecycle_via_api(app):
    """Add → list → stats → remove → list. Verify ``session.ended`` lands on
    a subscribed SSE queue after the removal is broadcast."""
    client, fastapi_app = app
    s = fastapi_app.state.s

    # Initially empty.
    assert client.get("/api/sessions").json() == []
    stats = client.get("/api/stats").json()
    assert stats["active"] == 0
    assert stats["active_tokens"] == 0

    # Inject one synthetic session — we don't have a real "create" path that
    # doesn't spawn iTerm, so push directly into the in-memory map.
    sess = _mk_session(pid=4242, cwd="/Users/me/Projects/demo", tokens=2000, cost=1.25)
    s.sessions[sess.pid] = sess

    listed = client.get("/api/sessions").json()
    assert len(listed) == 1
    assert listed[0]["pid"] == 4242

    stats = client.get("/api/stats").json()
    assert stats["active"] == 1
    assert stats["active_tokens"] == 2000
    assert stats["active_cost"] == pytest.approx(1.25)

    # Hook up an SSE queue so we can observe the broadcast.
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    s.sse_queues.add(queue)

    async def _broadcast_ended():
        # Simulate what _emit_diffs does at the tail end of a tick where the
        # pid has disappeared. We don't run the full scheduler — we test the
        # bookkeeping side of it.
        s.sessions.pop(sess.pid, None)
        await s.broadcast({"event": "session.ended", "pid": sess.pid})

    asyncio.run(_broadcast_ended())

    # Drain the queue and assert the ended event landed.
    events: list[dict] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(e.get("event") == "session.ended" and e.get("pid") == 4242 for e in events)

    # And the public surface is back to empty.
    assert client.get("/api/sessions").json() == []
    assert client.get("/api/stats").json()["active"] == 0


# ---------------------------------------------------------------------------
# Settings roundtrip
# ---------------------------------------------------------------------------


def test_full_settings_save_roundtrip(app):
    """GET → POST → GET. Persisted to config.toml and reflected on
    ``app.state.s.config``."""
    client, fastapi_app = app

    initial = client.get("/api/config").json()
    # Default plan is "api"; we'll flip it to "max" and also toggle read_only.
    assert initial["plan"] == "api"

    r = client.post("/api/config", json={"plan": "max", "read_only": True})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["plan"] == "max"
    assert body["read_only"] is True

    # GET reflects the change.
    fetched = client.get("/api/config").json()
    assert fetched["plan"] == "max"
    assert fetched["read_only"] is True

    # In-memory s.config also reflects.
    assert fastapi_app.state.s.config["plan"] == "max"
    assert fastapi_app.state.s.config["read_only"] is True


# ---------------------------------------------------------------------------
# Files diff full flow against a real temp git repo
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
        },
    )


def test_files_diff_full_flow(tmp_path, monkeypatch):
    """End-to-end: real git repo, one tracked file modified + one untracked.

    Bypasses the full app fixture so we can point ``Path.home()`` at the same
    tmp_path the repo lives in — the diff endpoint's safety check requires
    the cwd to live under ``$HOME``."""
    from backend.api import files as files_api
    from backend.server import AppState

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("backend.api.files.Path.home", lambda: fake_home)

    repo = fake_home / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    tracked = repo / "main.py"
    tracked.write_text("a = 1\nb = 2\nc = 3\n")
    _git(repo, "add", "main.py")
    _git(repo, "commit", "-q", "-m", "init")
    tracked.write_text("a = 1\nb = 999\nc = 3\n")

    fastapi_app = FastAPI()
    fastapi_app.include_router(files_api.router)
    state = AppState(config={})
    state.sessions = {1: _mk_session(pid=1, cwd=str(repo))}
    fastapi_app.state.s = state

    with TestClient(fastapi_app, base_url="http://127.0.0.1") as client:
        # Modified-but-tracked: real diff is returned.
        r = client.get("/api/files/diff", params={"cwd": str(repo), "path": "main.py"})
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["is_git"] is True
        assert body["tracked"] is True
        assert "999" in body["diff"]
        assert body["untracked_preview"] is None

        # Untracked file: preview path engaged.
        untracked = repo / "new_module.py"
        untracked.write_text("# brand new file\n")
        r2 = client.get("/api/files/diff", params={"cwd": str(repo), "path": "new_module.py"})
        assert r2.status_code == 200, r2.json()
        body2 = r2.json()
        assert body2["is_git"] is True
        assert body2["tracked"] is False
        assert body2["untracked_preview"] == "# brand new file\n"


# ---------------------------------------------------------------------------
# Insights /api/projects aggregation
# ---------------------------------------------------------------------------


def test_insights_projects_endpoint_aggregates(app):
    """Three live sessions across two cwds — cost+tokens roll up correctly
    and are sorted by total cost desc."""
    client, fastapi_app = app
    s = fastapi_app.state.s

    cwd_a = "/Users/me/Projects/alpha"
    cwd_b = "/Users/me/Projects/beta"
    s.sessions = {
        1: _mk_session(pid=1, cwd=cwd_a, tokens=1000, cost=0.30),
        2: _mk_session(pid=2, cwd=cwd_a, tokens=2000, cost=0.70),
        3: _mk_session(pid=3, cwd=cwd_b, tokens=500, cost=0.10),
    }

    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    # Sorted desc by total_cost_24h: alpha (1.0) first, beta (0.1) second.
    assert data[0]["cwd"] == cwd_a
    assert data[0]["active_sessions"] == 2
    assert data[0]["total_tokens_24h"] == 3000
    assert data[0]["total_cost_24h"] == pytest.approx(1.0)
    assert data[1]["cwd"] == cwd_b
    assert data[1]["total_tokens_24h"] == 500
    assert data[1]["total_cost_24h"] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# Log-stream SSE snapshot + append
# ---------------------------------------------------------------------------


def test_log_stream_emits_snapshot_then_appends(app, tmp_path):
    """Open the SSE generator in-process, step it through:
    snapshot frame → file grows → append frame.

    httpx's sync streaming inside TestClient doesn't cooperate with an
    async generator that polls forever (see the comment in test_api.py's
    ``test_log_stream_initial_snapshot_event``), so we exercise the
    generator directly via asyncio.run — same pattern as the existing
    snapshot test."""
    from backend.api.sessions import stream_log_tail

    _, fastapi_app = app
    s = fastapi_app.state.s
    s.config["show_log_text"] = True

    log = tmp_path / "stream.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": f"line {i}"}]},
                }
            )
            for i in range(3)
        )
        + "\n"
    )
    sess = _mk_session(pid=9001, cwd=str(tmp_path))
    sess.conversation_log_path = str(log)
    s.sessions[sess.pid] = sess

    disconnected = {"v": False}

    fake_req = MagicMock()
    fake_req.app = fastapi_app

    async def _is_disconnected():
        return disconnected["v"]

    fake_req.is_disconnected = _is_disconnected

    async def _delayed_append(path, delay):
        """Append a 4th JSONL entry *after* the poll loop has recorded its
        starting position (which happens after the snapshot yields, but
        before the first sleep returns)."""
        await asyncio.sleep(delay)
        with open(path, "a") as f:
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "line 3 appended"}]},
                    }
                )
                + "\n"
            )

    async def _run():
        resp = await stream_log_tail(sess.pid, fake_req)
        agen = resp.body_iterator
        try:
            # Frame 1: snapshot.
            first = await agen.__anext__()
            # Schedule the append slightly into the future so it lands AFTER
            # the generator's "pos = f.tell()" call records the end-of-file
            # offset (which only runs when __anext__() is resumed below).
            task = asyncio.create_task(_delayed_append(log, 0.1))
            # Frame 2: append. The poll loop sleeps 0.5s between ticks;
            # 5s is ample headroom for one tick + the 0.1s delay above.
            second = await asyncio.wait_for(agen.__anext__(), timeout=5.0)
            await task
            return first, second
        finally:
            disconnected["v"] = True
            await agen.aclose()

    first, second = asyncio.run(_run())
    if isinstance(first, bytes):
        first = first.decode("utf-8")
    if isinstance(second, bytes):
        second = second.decode("utf-8")

    assert "event: snapshot" in first
    snapshot_payload = json.loads(first.split("data: ", 1)[1].strip())
    assert len(snapshot_payload["entries"]) == 3

    assert "event: append" in second
    append_payload = json.loads(second.split("data: ", 1)[1].strip())
    assert len(append_payload["entries"]) == 1
    assert append_payload["entries"][0]["message"]["content"][0]["text"] == "line 3 appended"


# ---------------------------------------------------------------------------
# send-text remote_control gating
# ---------------------------------------------------------------------------


def test_send_text_validates_remote_control_config(app):
    """Disabled → 403. Enabled → 200 with newline appended when submit=True."""
    client, fastapi_app = app
    s = fastapi_app.state.s

    sess = _mk_session(pid=7777, cwd="/Users/me/Projects/rc")
    sess.iterm_session_id = "iterm-sess-xyz"
    s.sessions[sess.pid] = sess

    # Default remote_control.enabled is False (DEFAULT_CONFIG). Confirm 403.
    s.config.setdefault("remote_control", {})["enabled"] = False
    r = client.post(f"/api/sessions/{sess.pid}/send-text", json={"text": "hello"})
    assert r.status_code == 403

    # Flip enabled + mock the iterm_manager. send-text must succeed and the
    # forwarded payload must end with "\n" when submit=True.
    s.config["remote_control"]["enabled"] = True
    fake_mgr = AsyncMock()
    fake_mgr.send_text = AsyncMock(return_value=True)
    s.iterm_manager = fake_mgr

    r2 = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "hello", "submit": True},
    )
    assert r2.status_code == 200, r2.json()
    body = r2.json()
    assert body["success"] is True
    # 5 chars + 1 newline.
    assert body["bytes_sent"] == 6
    fake_mgr.send_text.assert_awaited_once_with("iterm-sess-xyz", "hello\n")
