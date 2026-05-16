"""API endpoint tests using FastAPI TestClient.

These tests don't spin up uvicorn; they instantiate the app and call routes
directly via httpx. Lifespan runs the scheduler loop, so we suppress that by
overriding state setup.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.models import ClaudeSession, TokenUsage, ToolCallStats


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Build the FastAPI app with a tmp_path state DB and no real scheduler."""
    monkeypatch.setattr("backend.config.STATE_DB", tmp_path / "state.db")
    monkeypatch.setattr("backend.server.STATE_DB", tmp_path / "state.db")

    # Disable the scheduler by patching the loop to no-op.
    async def _no_scheduler(s):
        return None

    monkeypatch.setattr("backend.server._scheduler_loop", _no_scheduler)

    from backend.server import create_app

    app = create_app()

    # TrustedHostMiddleware (issue #39) rejects the default TestClient host
    # "testserver"; pin the base_url to 127.0.0.1 so requests carry an allowed
    # Host header.
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app


@pytest.fixture
def populated_app(app, tmp_path):
    client, fastapi_app = app
    now = datetime.now(timezone.utc)
    sess = ClaudeSession(
        pid=12345,
        cwd="/Users/me/Projects/x",
        started_at=now,
        duration_seconds=120,
        cpu_percent=4.2,
        memory_mb=512.0,
        status="working",
        location_type="iterm",
        iterm_window_id="42",
        iterm_tab_index=1,
        iterm_tty="/dev/ttys001",
        last_activity_at=now,
        model="claude-opus-4-7",
        usage=TokenUsage(
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=2000,
            cost_estimate_usd=0.063,
        ),
        tool_calls=ToolCallStats(total=3, breakdown={"Edit": 2, "Bash": 1}, last_used="Edit"),
        permission_mode="dangerously-skip",
        message_count=4,
    )
    fastapi_app.state.s.sessions = {sess.pid: sess}
    return client, fastapi_app, sess


def test_sessions_endpoint_lists_active(populated_app):
    client, _, sess = populated_app
    r = client.get("/api/sessions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["pid"] == 12345
    assert data[0]["model"] == "claude-opus-4-7"
    assert data[0]["usage"]["total_tokens"] == 3500


def test_sessions_get_single_404_when_missing(populated_app):
    client, _, _ = populated_app
    assert client.get("/api/sessions/99999").status_code == 404


def test_sessions_get_single_returns_full_object(populated_app):
    client, _, _ = populated_app
    r = client.get("/api/sessions/12345")
    assert r.status_code == 200
    d = r.json()
    assert d["iterm_window_id"] == "42"
    assert d["tool_calls"]["total"] == 3
    assert d["tool_calls"]["breakdown"] == {"Edit": 2, "Bash": 1}
    assert d["permission_mode"] == "dangerously-skip"


def test_health_endpoint_shape(populated_app):
    client, _, _ = populated_app
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    for key in ("iterm_api", "automation", "tmux_available", "log_dir_found", "issues"):
        assert key in d


def test_stats_aggregates_active_tokens_and_cost(populated_app):
    client, _, _ = populated_app
    r = client.get("/api/stats")
    assert r.status_code == 200
    d = r.json()
    assert d["active"] == 1
    assert d["active_tokens"] == 3500
    assert d["active_cost"] == pytest.approx(0.063)


def test_config_get_and_post_roundtrip(populated_app, tmp_path, monkeypatch):
    client, fastapi_app, _ = populated_app
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    r = client.post("/api/config", json={"port": 7799})
    assert r.status_code == 200
    assert r.json()["port"] == 7799

    r2 = client.get("/api/config")
    assert r2.status_code == 200
    assert r2.json()["port"] == 7799


def test_post_config_rejects_unknown_keys(populated_app, tmp_path, monkeypatch):
    """#41: extra='forbid' rejects keys outside the whitelist (e.g. an attacker
    trying to plant random config that future code might honor)."""
    client, _, _ = populated_app
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    r = client.post("/api/config", json={"sneaky_field": "x"})
    assert r.status_code == 422


def test_post_config_clamps_ranges_low_port(populated_app, tmp_path, monkeypatch):
    """#41: port < 1024 (privileged range) is rejected so callers can't
    redirect us onto e.g. port 80."""
    client, _, _ = populated_app
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    r = client.post("/api/config", json={"port": 80})
    assert r.status_code == 422


def test_post_config_clamps_ranges_high_port(populated_app, tmp_path, monkeypatch):
    client, _, _ = populated_app
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    r = client.post("/api/config", json={"port": 70000})
    assert r.status_code == 422


def test_post_config_accepts_known_keys(populated_app, tmp_path, monkeypatch):
    """Sanity: the whitelist still accepts every legitimate field."""
    client, _, _ = populated_app
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    body = {
        "port": 7799,
        "read_only": True,
        "privacy_mode": False,
        "show_log_text": True,
        "file_change_retention_minutes": 30,
        "process_scan_interval_seconds": 1.5,
        "iterm_refresh_interval_seconds": 4.0,
        "ignore_patterns": ["*.bak"],
    }
    r = client.post("/api/config", json=body)
    assert r.status_code == 200
    out = r.json()
    for k, v in body.items():
        assert out[k] == v


def test_post_pricing_rejects_string_rate(populated_app, tmp_path, monkeypatch):
    """#30 / #41: a pricing rate of "abc" must be 422, not silently accepted."""
    client, _, _ = populated_app
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    r = client.post(
        "/api/pricing",
        json={
            "claude-opus-4-7": {
                "input": "abc",
                "output": 75.0,
                "cache_read": 1.5,
                "cache_write": 18.75,
            }
        },
    )
    assert r.status_code == 422


def test_post_pricing_rejects_negative_rate(populated_app, tmp_path, monkeypatch):
    """#41: negative pricing values are nonsense — must 422."""
    client, _, _ = populated_app
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    r = client.post(
        "/api/pricing",
        json={
            "claude-opus-4-7": {
                "input": -1.0,
                "output": 75.0,
                "cache_read": 1.5,
                "cache_write": 18.75,
            }
        },
    )
    assert r.status_code == 422


def test_post_pricing_accepts_valid_payload(populated_app, tmp_path, monkeypatch):
    client, _, _ = populated_app
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    payload = {
        "claude-opus-4-7": {
            "input": 15.0,
            "output": 75.0,
            "cache_read": 1.5,
            "cache_write": 18.75,
        }
    }
    r = client.post("/api/pricing", json=payload)
    assert r.status_code == 200
    assert r.json()["claude-opus-4-7"]["input"] == 15.0


def test_history_returns_empty_initially(populated_app):
    client, _, _ = populated_app
    r = client.get("/api/history")
    assert r.status_code == 200
    assert r.json() == []


def test_focus_rejects_headless(populated_app):
    client, fastapi_app, _ = populated_app
    sess = list(fastapi_app.state.s.sessions.values())[0]
    sess.location_type = "headless"
    sess.iterm_tty = None
    sess.iterm_window_id = None
    r = client.post(f"/api/sessions/{sess.pid}/focus")
    assert r.status_code == 400
    assert "headless" in r.json()["detail"].lower()


def test_focus_rejects_404_for_unknown_pid(populated_app):
    client, _, _ = populated_app
    assert client.post("/api/sessions/99999/focus").status_code == 404


def test_focus_rejects_tmux_without_attached_client(populated_app, monkeypatch):
    """Issue #12: tmux session with no attached client and no iTerm linkage
    should return 409 instead of silently succeeding."""
    client, fastapi_app, _ = populated_app
    sess = list(fastapi_app.state.s.sessions.values())[0]
    sess.location_type = "tmux"
    sess.iterm_tty = None
    sess.iterm_window_id = None
    sess.iterm_tab_id = None
    sess.tmux_session = "work"
    sess.tmux_window = "0"
    sess.tmux_pane = "0"

    import subprocess as _sub

    def fake_run(cmd, *args, **kwargs):
        # tmux list-clients returns empty stdout = no client attached
        if cmd[:2] == ["tmux", "list-clients"]:
            return _sub.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected subprocess call: {cmd}")

    monkeypatch.setattr("backend.api.actions.subprocess.run", fake_run)

    r = client.post(f"/api/sessions/{sess.pid}/focus")
    assert r.status_code == 409
    assert "detached tmux" in r.json()["detail"].lower()


def test_halt_404_for_unknown_pid(populated_app):
    client, _, _ = populated_app
    assert client.post("/api/sessions/99999/halt").status_code == 404


def test_read_only_mode_blocks_actions(populated_app):
    client, fastapi_app, _ = populated_app
    fastapi_app.state.s.config["read_only"] = True
    assert client.post("/api/sessions/12345/halt").status_code == 403
    assert client.post("/api/sessions/12345/focus").status_code == 403
    assert client.post("/api/sessions/new", json={"cwd": str(Path.home())}).status_code == 403


def test_new_session_rejects_bad_cwd(populated_app):
    client, _, _ = populated_app
    r = client.post("/api/sessions/new", json={"cwd": "/nope/not/a/dir/xyz"})
    assert r.status_code == 400


def test_new_session_rejects_unsafe_flag(populated_app):
    client, _, _ = populated_app
    r = client.post(
        "/api/sessions/new",
        json={"cwd": str(Path.home()), "flags": ["--evil;rm"]},
    )
    assert r.status_code == 400


def test_new_session_rejects_path_prefix_bypass(populated_app, tmp_path, monkeypatch):
    """#38: a command path like ~/.local/binEVIL/x must NOT pass the
    ~/.local/bin allowlist via str.startswith."""
    client, _, _ = populated_app
    fake_home = tmp_path / "home"
    # Sibling of the allowed ~/.local/bin: would pass startswith() but is not
    # actually a parent in path terms.
    evil_dir = fake_home / ".local" / "binEVIL"
    evil_dir.mkdir(parents=True)
    evil_cmd = evil_dir / "x"
    evil_cmd.write_text("#!/bin/sh\n")
    evil_cmd.chmod(0o755)
    monkeypatch.setattr("backend.api.actions.Path.home", lambda: fake_home)

    r = client.post(
        "/api/sessions/new",
        json={"cwd": str(fake_home), "command": str(evil_cmd)},
    )
    assert r.status_code == 400
    assert "under" in r.json()["detail"].lower()


def test_new_session_accepts_legitimate_local_bin(populated_app, tmp_path, monkeypatch):
    """#38: a real command living under ~/.local/bin must be accepted.

    We point Path.home() at a tmp dir so the test is hermetic — otherwise
    ~/.local/bin/claude on the dev machine is a symlink that resolves elsewhere.
    """
    import subprocess as _sub

    client, _, _ = populated_app
    fake_home = tmp_path / "home"
    bin_dir = fake_home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    cmd = bin_dir / "claude"
    cmd.write_text("#!/bin/sh\n")
    cmd.chmod(0o755)

    monkeypatch.setattr("backend.api.actions.Path.home", lambda: fake_home)

    def fake_run(*args, **kwargs):
        return _sub.CompletedProcess(args[0] if args else [], 0, stdout="", stderr="")

    monkeypatch.setattr("backend.api.actions.subprocess.run", fake_run)

    r = client.post(
        "/api/sessions/new",
        json={"cwd": str(fake_home), "command": str(cmd)},
    )
    assert r.status_code == 200, r.json()


def test_new_session_accepts_effort_flag(populated_app, monkeypatch):
    """#55: --effort is a value-taking flag in the canonical set; using it as
    `--effort high` must no longer 400 due to the missing flag value rule."""
    import subprocess as _sub

    client, _, _ = populated_app

    def fake_run(*args, **kwargs):
        return _sub.CompletedProcess(args[0] if args else [], 0, stdout="", stderr="")

    monkeypatch.setattr("backend.api.actions.subprocess.run", fake_run)
    r = client.post(
        "/api/sessions/new",
        json={"cwd": str(Path.home()), "flags": ["--effort", "high"]},
    )
    assert r.status_code == 200, r.json()


def test_halt_409_on_pid_reuse(populated_app, monkeypatch):
    """#33: when the registered PID no longer refers to a Claude process,
    /halt must 409 instead of SIGINT'ing the unrelated process."""
    client, fastapi_app, sess = populated_app

    # Pretend the PID is alive (psutil.Process won't raise) but is_claude_process
    # returns False.
    class _FakeProc:
        def __init__(self, pid):
            self.pid = pid

    monkeypatch.setattr("backend.api.actions.psutil.Process", _FakeProc)
    monkeypatch.setattr("backend.api.actions.is_claude_process", lambda proc, user: False)

    def fail_kill(*a, **k):
        raise AssertionError("os.kill must not be called when PID is not a claude process")

    monkeypatch.setattr("backend.api.actions.os.kill", fail_kill)

    r = client.post(f"/api/sessions/{sess.pid}/halt")
    assert r.status_code == 409
    assert "claude" in r.json()["detail"].lower()


def test_log_tail_caps_limit(populated_app):
    """#47: the ?limit query must be capped by FastAPI's Query(le=500)."""
    client, _, sess = populated_app
    r = client.get(f"/api/sessions/{sess.pid}/log-tail?limit=99999999")
    assert r.status_code == 422


def test_log_tail_privacy_redacts_text_by_default(populated_app, tmp_path):
    client, fastapi_app, sess = populated_app
    fastapi_app.state.s.config["show_log_text"] = False
    log = tmp_path / "fake.jsonl"
    log.write_text(
        '{"type":"assistant","timestamp":"2026-01-01T00:00:00Z",'
        '"message":{"model":"claude-opus-4-7","content":['
        '{"type":"text","text":"secret content"},'
        '{"type":"tool_use","name":"Bash","input":{"command":"echo s3cret"}}'
        "]}}\n"
    )
    sess.conversation_log_path = str(log)
    r = client.get(f"/api/sessions/{sess.pid}/log-tail")
    assert r.status_code == 200
    data = r.json()
    assert data["privacy_mode"] is True
    blocks = data["entries"][0]["message"]["content"]
    text_block = next(b for b in blocks if b["type"] == "text")
    # Redacted text block: keeps {type} only, no "text" key with content
    assert text_block.get("text") is None or text_block.get("text") == ""
    tool_block = next(b for b in blocks if b["type"] == "tool_use")
    assert tool_block.get("name") == "Bash"
    assert "input" not in tool_block


def test_log_tail_shows_text_when_show_log_text_true(populated_app, tmp_path):
    client, fastapi_app, sess = populated_app
    log = tmp_path / "show.jsonl"
    log.write_text('{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n')
    sess.conversation_log_path = str(log)
    fastapi_app.state.s.config["show_log_text"] = True
    r = client.get(f"/api/sessions/{sess.pid}/log-tail")
    data = r.json()
    assert data["privacy_mode"] is False
    assert data["entries"][0]["message"]["content"][0]["text"] == "hello"


def test_focus_uses_iterm_manager_when_session_id_present(populated_app, monkeypatch):
    """#24: when the session has an iterm_session_id and the iterm_manager is
    available, /focus must call focus_session on it and NOT shell out to
    osascript."""
    from unittest.mock import AsyncMock

    client, fastapi_app, sess = populated_app
    sess.iterm_tty = None  # ensure we don't hit the tty AppleScript branch
    sess.iterm_window_id = "pty-UUID-1"
    sess.iterm_tab_id = "3"
    sess.iterm_session_id = "iterm-sess-xyz"

    fake_mgr = AsyncMock()
    fake_mgr.focus_session = AsyncMock(return_value=True)
    fastapi_app.state.s.iterm_manager = fake_mgr

    def fail_run(*args, **kwargs):
        raise AssertionError(f"osascript should not be invoked; got {args[0]!r}")

    monkeypatch.setattr("backend.api.actions.subprocess.run", fail_run)

    r = client.post(f"/api/sessions/{sess.pid}/focus")
    assert r.status_code == 200
    assert r.json() == {"success": True}
    fake_mgr.focus_session.assert_awaited_once_with("iterm-sess-xyz")


def test_send_text_403_when_disabled(populated_app):
    """Default config has remote_control.enabled=False — POST must 403."""
    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = "sess-xyz"
    fastapi_app.state.s.config.setdefault("remote_control", {})["enabled"] = False
    r = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "hello"},
    )
    assert r.status_code == 403
    assert "remote control" in r.json()["detail"].lower()


def test_send_text_works_when_enabled(populated_app):
    """With remote_control.enabled=True the endpoint forwards to iterm_manager
    and returns success + bytes_sent (including the trailing newline)."""
    from unittest.mock import AsyncMock

    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = "iterm-sess-zzz"
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}

    fake_mgr = AsyncMock()
    fake_mgr.send_text = AsyncMock(return_value=True)
    fastapi_app.state.s.iterm_manager = fake_mgr

    r = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "hello", "submit": True},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["success"] is True
    # "hello" + "\n" = 6 bytes
    assert body["bytes_sent"] == 6
    fake_mgr.send_text.assert_awaited_once_with("iterm-sess-zzz", "hello\n")


def test_send_text_no_submit_omits_newline(populated_app):
    from unittest.mock import AsyncMock

    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = "iterm-sess-zzz"
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}

    fake_mgr = AsyncMock()
    fake_mgr.send_text = AsyncMock(return_value=True)
    fastapi_app.state.s.iterm_manager = fake_mgr

    r = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "hi", "submit": False},
    )
    assert r.status_code == 200
    fake_mgr.send_text.assert_awaited_once_with("iterm-sess-zzz", "hi")


def test_send_text_caps_length(populated_app):
    """Payloads above the byte cap must 413, not be forwarded."""
    from unittest.mock import AsyncMock

    from backend.api.actions import SEND_TEXT_MAX_BYTES

    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = "iterm-sess-zzz"
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}
    fake_mgr = AsyncMock()
    fake_mgr.send_text = AsyncMock(return_value=True)
    fastapi_app.state.s.iterm_manager = fake_mgr

    r = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "x" * (SEND_TEXT_MAX_BYTES + 1)},
    )
    assert r.status_code == 413
    fake_mgr.send_text.assert_not_awaited()


def test_send_text_caps_bytes_not_codepoints(populated_app):
    """#89: the cap is bytes, not codepoints. A 4-byte emoji repeated 4097
    times (≈16 KB + 4 B) blows past SEND_TEXT_MAX_BYTES and must 413, while
    4096 ASCII chars (4096 bytes) sit comfortably below the new cap and pass.
    """
    from unittest.mock import AsyncMock

    from backend.api.actions import SEND_TEXT_MAX_BYTES

    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = "iterm-sess-zzz"
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}
    fake_mgr = AsyncMock()
    fake_mgr.send_text = AsyncMock(return_value=True)
    fastapi_app.state.s.iterm_manager = fake_mgr

    # 4-byte UTF-8 emoji: a single 🚀 is 4 bytes. 4097 * 4 = 16388 bytes.
    emoji_payload = "🚀" * 4097
    assert len(emoji_payload.encode("utf-8")) > SEND_TEXT_MAX_BYTES
    r = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": emoji_payload, "submit": False},
    )
    assert r.status_code == 413, r.json()
    fake_mgr.send_text.assert_not_awaited()

    # 4096 ASCII chars = 4096 bytes — well under the 16 KB cap, must pass.
    r2 = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "x" * 4096, "submit": False},
    )
    assert r2.status_code == 200, r2.json()
    fake_mgr.send_text.assert_awaited_once()


def test_send_text_rate_limits_at_6th_request_within_10s(populated_app):
    """#88: per-PID token bucket — 5 sends in the window pass, the 6th must
    return 429 with a ``Retry-After`` header.
    """
    from unittest.mock import AsyncMock

    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = "iterm-sess-zzz"
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}
    fake_mgr = AsyncMock()
    fake_mgr.send_text = AsyncMock(return_value=True)
    fastapi_app.state.s.iterm_manager = fake_mgr

    # Reset the bucket between tests in case fixture re-use leaves entries.
    fastapi_app.state.s.send_text_rate = {}

    for _ in range(5):
        r = client.post(
            f"/api/sessions/{sess.pid}/send-text",
            json={"text": "hi", "submit": False},
        )
        assert r.status_code == 200, r.json()

    blocked = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "hi", "submit": False},
    )
    assert blocked.status_code == 429, blocked.json()
    assert "Retry-After" in blocked.headers
    assert int(blocked.headers["Retry-After"]) >= 1
    # iTerm.send_text should have been called exactly 5 times, not 6.
    assert fake_mgr.send_text.await_count == 5


def test_send_text_rate_limit_resets_after_window(populated_app, monkeypatch):
    """#88: once the window has elapsed, the bucket should drain and accept
    sends again. We monkeypatch ``time.monotonic`` so we don't have to sleep
    11s in the test.
    """
    from unittest.mock import AsyncMock

    import backend.api.actions as actions_mod

    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = "iterm-sess-zzz"
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}
    fake_mgr = AsyncMock()
    fake_mgr.send_text = AsyncMock(return_value=True)
    fastapi_app.state.s.iterm_manager = fake_mgr
    fastapi_app.state.s.send_text_rate = {}

    fake_now = [1000.0]

    def fake_monotonic():
        return fake_now[0]

    monkeypatch.setattr(actions_mod.time, "monotonic", fake_monotonic)

    # Fill the bucket.
    for _ in range(5):
        r = client.post(
            f"/api/sessions/{sess.pid}/send-text",
            json={"text": "hi", "submit": False},
        )
        assert r.status_code == 200, r.json()

    # Still inside the window → blocked.
    blocked = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "hi", "submit": False},
    )
    assert blocked.status_code == 429

    # Jump past the window — bucket entries should be pruned and the next
    # request must succeed.
    fake_now[0] += 11.0
    after = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "hi", "submit": False},
    )
    assert after.status_code == 200, after.json()


def test_audit_log_includes_payload_preview(populated_app, caplog):
    """#88: at INFO level, the audit log line for /send-text must include the
    first 80 chars of the (sanitized) payload so we have forensic context.
    """
    import logging as _logging
    from unittest.mock import AsyncMock

    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = "iterm-sess-zzz"
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}
    fake_mgr = AsyncMock()
    fake_mgr.send_text = AsyncMock(return_value=True)
    fastapi_app.state.s.iterm_manager = fake_mgr
    fastapi_app.state.s.send_text_rate = {}

    # Distinctive payload so we can grep for it; include a newline to verify
    # the sanitizer strips control chars.
    payload_text = "audit-test-payload-12345\nsecond-line"
    expected_preview = "audit-test-payload-12345 second-line"

    with caplog.at_level(_logging.INFO, logger="backend.api.actions"):
        r = client.post(
            f"/api/sessions/{sess.pid}/send-text",
            json={"text": payload_text, "submit": False},
        )
    assert r.status_code == 200, r.json()
    audit_lines = [rec.getMessage() for rec in caplog.records if rec.name == "backend.api.actions"]
    assert any(expected_preview in line for line in audit_lines), audit_lines


def test_send_text_400_when_no_iterm_session_id(populated_app):
    """No iTerm linkage → 400 (we can't route the text anywhere useful)."""
    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = None
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}

    r = client.post(
        f"/api/sessions/{sess.pid}/send-text",
        json={"text": "hello"},
    )
    assert r.status_code == 400


def test_send_text_404_for_unknown_pid(populated_app):
    client, fastapi_app, _ = populated_app
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}
    r = client.post("/api/sessions/99999/send-text", json={"text": "x"})
    assert r.status_code == 404


def test_send_text_read_only_blocks(populated_app):
    client, fastapi_app, sess = populated_app
    sess.iterm_session_id = "x"
    fastapi_app.state.s.config["remote_control"] = {"enabled": True}
    fastapi_app.state.s.config["read_only"] = True
    r = client.post(f"/api/sessions/{sess.pid}/send-text", json={"text": "x"})
    assert r.status_code == 403


def test_log_stream_returns_404_for_unknown_pid(populated_app):
    client, _, _ = populated_app
    r = client.get("/api/sessions/99999/log-stream")
    assert r.status_code == 404


def test_log_stream_returns_404_when_no_log_path(populated_app):
    client, _, sess = populated_app
    sess.conversation_log_path = None
    r = client.get(f"/api/sessions/{sess.pid}/log-stream")
    assert r.status_code == 404


def test_log_stream_initial_snapshot_event(populated_app, tmp_path):
    """Smoke-test the SSE generator directly: opening the stream emits a
    ``snapshot`` event with the file's existing entries.

    We don't drive this through the TestClient because httpx's sync streaming
    doesn't cooperate well with an async generator that polls forever — the
    snapshot logic is the load-bearing part anyway, so we exercise it
    in-process by stepping the async generator once."""
    import asyncio
    import json as _json
    from unittest.mock import AsyncMock, MagicMock

    from backend.api.sessions import stream_log_tail

    client, fastapi_app, sess = populated_app  # noqa: F841 — fixture also sets up app state
    fastapi_app.state.s.config["show_log_text"] = True
    log = tmp_path / "stream.jsonl"
    log.write_text('{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n')
    sess.conversation_log_path = str(log)

    fake_req = MagicMock()
    fake_req.app = fastapi_app
    fake_req.is_disconnected = AsyncMock(return_value=True)

    async def _run():
        resp = await stream_log_tail(sess.pid, fake_req)
        # Pull just the first yielded chunk — the initial snapshot event.
        agen = resp.body_iterator
        first = await agen.__anext__()
        await agen.aclose()
        return first

    first = asyncio.run(_run())
    if isinstance(first, bytes):
        first = first.decode("utf-8")
    assert "event: snapshot" in first
    payload = first.split("data: ", 1)[1].strip()
    data = _json.loads(payload)
    assert data["entries"][0]["message"]["content"][0]["text"] == "hi"


def test_focus_falls_back_to_applescript_when_manager_returns_false(populated_app, monkeypatch):
    """#24: when focus_session returns False, /focus must fall through to the
    existing AppleScript paths."""
    import subprocess as _sub
    from unittest.mock import AsyncMock

    client, fastapi_app, sess = populated_app
    sess.iterm_tty = "/dev/ttys009"  # AppleScript-by-tty path
    sess.iterm_window_id = None
    sess.iterm_tab_id = None
    sess.iterm_session_id = "iterm-sess-missing"

    fake_mgr = AsyncMock()
    fake_mgr.focus_session = AsyncMock(return_value=False)
    fastapi_app.state.s.iterm_manager = fake_mgr

    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return _sub.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("backend.api.actions.subprocess.run", fake_run)

    r = client.post(f"/api/sessions/{sess.pid}/focus")
    assert r.status_code == 200
    fake_mgr.focus_session.assert_awaited_once_with("iterm-sess-missing")
    # AppleScript-by-tty fallback ran.
    assert any("focus_by_tty.applescript" in part for call in calls for part in call)
