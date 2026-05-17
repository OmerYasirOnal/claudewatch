"""Tests for ``backend.detectors.timeline.derive_timeline``.

All fixtures are synthetic JSONL written to ``tmp_path`` — nothing reads or
writes the user's real ~/.claude/projects directory.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.detectors.timeline import (
    MAX_EVENTS,
    TOOL_COALESCE_WINDOW_SECONDS,
    derive_timeline,
)


def _write(path: Path, entries: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _user(ts: str, **extra) -> dict:
    base: dict = {
        "type": "user",
        "timestamp": ts,
        "message": {"content": extra.pop("content", "hi")},
    }
    base.update(extra)
    return base


def _assistant(
    ts: str,
    model: str = "claude-opus-4-7",
    content: list | None = None,
    *,
    is_error: bool = False,
    **extra,
) -> dict:
    msg: dict = {
        "model": model,
        "content": content if content is not None else [],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    if is_error:
        msg["is_error"] = True
    base = {
        "type": "assistant",
        "timestamp": ts,
        "message": msg,
    }
    base.update(extra)
    return base


def _tool_use(name: str, tool_id: str, **inp) -> dict:
    return {"type": "tool_use", "name": name, "id": tool_id, "input": inp}


def _tool_result(ts: str, tool_use_id: str, content, *, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"content": [block]},
    }


# ---------------------------------------------------------------------------
# Empty / minimal cases
# ---------------------------------------------------------------------------


def test_missing_log_returns_empty_timeline(tmp_path):
    tl = derive_timeline(tmp_path / "does-not-exist.jsonl", pid=42)
    assert tl.pid == 42
    assert tl.events == []
    assert tl.truncated is False


def test_empty_log_returns_empty_timeline(tmp_path):
    f = tmp_path / "empty.jsonl"
    f.write_text("")
    tl = derive_timeline(f, pid=7)
    assert tl.events == []
    assert tl.truncated is False


def test_single_user_message_emits_started(tmp_path):
    f = tmp_path / "single.jsonl"
    _write(f, [_user("2026-05-17T10:00:00Z", cwd="/Users/x/y", version="2.1.0")])
    tl = derive_timeline(f, pid=1)
    assert len(tl.events) >= 1
    assert tl.events[0].type == "started"
    assert tl.events[0].metadata.get("cwd") == "/Users/x/y"
    assert tl.events[0].metadata.get("cli_version") == "2.1.0"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_events_are_sorted_by_timestamp(tmp_path):
    f = tmp_path / "ordered.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant(
                "2026-05-17T10:00:05Z",
                content=[_tool_use("Bash", "b1")],
            ),
            _tool_result("2026-05-17T10:00:06Z", "b1", "ok"),
        ],
    )
    tl = derive_timeline(f, pid=1)
    timestamps = [e.timestamp for e in tl.events]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# Tool-call coalescing
# ---------------------------------------------------------------------------


def test_tool_calls_within_window_coalesce(tmp_path):
    f = tmp_path / "coalesce.jsonl"
    # 3 Bash calls all within 5s
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant("2026-05-17T10:00:01Z", content=[_tool_use("Bash", "b1")]),
            _assistant("2026-05-17T10:00:02Z", content=[_tool_use("Bash", "b2")]),
            _assistant("2026-05-17T10:00:04Z", content=[_tool_use("Bash", "b3")]),
        ],
    )
    tl = derive_timeline(f, pid=1)
    coalesced = [e for e in tl.events if e.type == "tool_call"]
    assert len(coalesced) == 1
    assert coalesced[0].metadata == {"tool": "Bash", "count": 3}
    assert "3 Bash calls" in coalesced[0].description


def test_tool_calls_separated_by_gap_do_not_coalesce(tmp_path):
    f = tmp_path / "gap.jsonl"
    # Calls 5s+1s = 6s apart — outside window — should split
    gap = TOOL_COALESCE_WINDOW_SECONDS + 1
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant("2026-05-17T10:00:01Z", content=[_tool_use("Bash", "b1")]),
            _assistant(
                f"2026-05-17T10:00:{int(1 + gap):02d}Z",
                content=[_tool_use("Bash", "b2")],
            ),
        ],
    )
    tl = derive_timeline(f, pid=1)
    coalesced = [e for e in tl.events if e.type == "tool_call"]
    assert len(coalesced) == 2


def test_different_tool_names_do_not_coalesce(tmp_path):
    f = tmp_path / "different.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant("2026-05-17T10:00:01Z", content=[_tool_use("Bash", "b1")]),
            _assistant("2026-05-17T10:00:02Z", content=[_tool_use("Read", "r1")]),
        ],
    )
    tl = derive_timeline(f, pid=1)
    types = [e.metadata.get("tool") for e in tl.events if e.type == "tool_call"]
    assert types == ["Bash", "Read"]


def test_first_tool_event_precedes_first_tool_call(tmp_path):
    f = tmp_path / "first.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant("2026-05-17T10:00:01Z", content=[_tool_use("Read", "r1")]),
            _assistant("2026-05-17T10:00:02Z", content=[_tool_use("Read", "r2")]),
        ],
    )
    tl = derive_timeline(f, pid=1)
    first_tools = [e for e in tl.events if e.type == "first_tool"]
    assert len(first_tools) == 1
    assert first_tools[0].metadata["tool"] == "Read"


# ---------------------------------------------------------------------------
# Model switching
# ---------------------------------------------------------------------------


def test_model_switch_emits_event(tmp_path):
    f = tmp_path / "swap.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant("2026-05-17T10:00:01Z", model="claude-opus-4-7"),
            _assistant("2026-05-17T10:00:02Z", model="claude-sonnet-4-5"),
        ],
    )
    tl = derive_timeline(f, pid=1)
    switches = [e for e in tl.events if e.type == "model_switch"]
    assert len(switches) == 1
    assert switches[0].metadata["from"] == "claude-opus-4-7"
    assert switches[0].metadata["to"] == "claude-sonnet-4-5"


def test_first_model_does_not_emit_switch(tmp_path):
    f = tmp_path / "init_model.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant("2026-05-17T10:00:01Z", model="claude-opus-4-7"),
        ],
    )
    tl = derive_timeline(f, pid=1)
    switches = [e for e in tl.events if e.type == "model_switch"]
    assert switches == []
    # And the "started" metadata gets enriched with the initial model.
    started = [e for e in tl.events if e.type == "started"][0]
    assert started.metadata.get("model") == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# Subagents
# ---------------------------------------------------------------------------


def test_subagent_dispatch_and_finish_paired(tmp_path):
    f = tmp_path / "agent.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant(
                "2026-05-17T10:00:01Z",
                content=[
                    _tool_use(
                        "Agent",
                        "ag1",
                        description="Refactor x",
                        subagent_type="Explore",
                    )
                ],
            ),
            _tool_result("2026-05-17T10:00:15Z", "ag1", "all done"),
        ],
    )
    tl = derive_timeline(f, pid=1)
    started = [e for e in tl.events if e.type == "subagent_started"]
    finished = [e for e in tl.events if e.type == "subagent_finished"]
    assert len(started) == 1
    assert len(finished) == 1
    assert started[0].metadata["description"] == "Refactor x"
    assert started[0].metadata["subagent_type"] == "Explore"
    assert finished[0].metadata["duration_seconds"] == 14
    assert finished[0].metadata["tool_use_id"] == "ag1"


def test_background_subagent_completes_via_task_notification(tmp_path):
    f = tmp_path / "bg.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant(
                "2026-05-17T10:00:01Z",
                content=[
                    _tool_use(
                        "Agent",
                        "ag2",
                        description="Bg job",
                        subagent_type="general-purpose",
                    )
                ],
            ),
            _tool_result(
                "2026-05-17T10:00:01.5Z",
                "ag2",
                "Async agent launched successfully. · agentId: XYZ (internal id n)",
            ),
            {
                "type": "user",
                "timestamp": "2026-05-17T10:01:00Z",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<task-notification>\n"
                                "<task-id>XYZ</task-id>\n"
                                "<status>completed</status>\n"
                                "<result>job output</result>\n"
                                "</task-notification>"
                            ),
                        }
                    ]
                },
            },
        ],
    )
    tl = derive_timeline(f, pid=1)
    finished = [e for e in tl.events if e.type == "subagent_finished"]
    assert len(finished) == 1
    assert finished[0].metadata.get("background") is True
    assert finished[0].metadata["duration_seconds"] == 59


def test_pending_subagent_emits_only_started(tmp_path):
    f = tmp_path / "pending.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant(
                "2026-05-17T10:00:01Z",
                content=[
                    _tool_use(
                        "Agent",
                        "agZ",
                        description="Pending one",
                        subagent_type="Explore",
                    )
                ],
            ),
        ],
    )
    tl = derive_timeline(f, pid=1)
    started = [e for e in tl.events if e.type == "subagent_started"]
    finished = [e for e in tl.events if e.type == "subagent_finished"]
    assert len(started) == 1
    assert len(finished) == 0


# ---------------------------------------------------------------------------
# Thinking
# ---------------------------------------------------------------------------


def test_thinking_started_emitted_once(tmp_path):
    f = tmp_path / "think.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant(
                "2026-05-17T10:00:01Z",
                content=[{"type": "thinking", "thinking": "..."}],
            ),
            _assistant(
                "2026-05-17T10:00:02Z",
                content=[{"type": "thinking", "thinking": "..."}],
            ),
        ],
    )
    tl = derive_timeline(f, pid=1)
    thinks = [e for e in tl.events if e.type == "thinking_started"]
    assert len(thinks) == 1


# ---------------------------------------------------------------------------
# Permission mode
# ---------------------------------------------------------------------------


def test_permission_mode_changes_emit_events(tmp_path):
    f = tmp_path / "perm.jsonl"
    _write(
        f,
        [
            {
                "type": "permission-mode",
                "permissionMode": "plan",
                "timestamp": "2026-05-17T10:00:00Z",
            },
            _assistant("2026-05-17T10:00:01Z"),
            {
                "type": "permission-mode",
                "permissionMode": "auto",
                "timestamp": "2026-05-17T10:00:02Z",
            },
        ],
    )
    tl = derive_timeline(f, pid=1)
    perms = [e for e in tl.events if e.type == "permission_prompt"]
    assert len(perms) == 2
    assert perms[0].metadata["mode"] == "plan"
    assert perms[1].metadata["mode"] == "auto"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_tool_result_error_emits_error_event(tmp_path):
    f = tmp_path / "err.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant(
                "2026-05-17T10:00:01Z",
                content=[_tool_use("Bash", "b1")],
            ),
            _tool_result(
                "2026-05-17T10:00:02Z",
                "b1",
                "Permission denied\nstack trace here",
                is_error=True,
            ),
        ],
    )
    tl = derive_timeline(f, pid=1)
    errs = [e for e in tl.events if e.type == "error"]
    assert len(errs) == 1
    assert "Permission denied" in errs[0].description


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def test_more_than_max_events_sets_truncated(tmp_path):
    """Synthesize >MAX_EVENTS distinct non-coalescable tool calls."""
    f = tmp_path / "lots.jsonl"
    entries: list[dict] = [_user("2026-05-17T10:00:00Z")]
    # Use alternating tool names so events do NOT coalesce — each is its own.
    base = datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(MAX_EVENTS + 50):
        ts = base.replace(second=0, microsecond=0).timestamp() + (i * 10)
        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        name = "Bash" if i % 2 == 0 else "Read"
        entries.append(_assistant(ts_iso, content=[_tool_use(name, f"x{i}")]))
    _write(f, entries)
    tl = derive_timeline(f, pid=1)
    assert tl.truncated is True
    assert len(tl.events) == MAX_EVENTS


def test_under_cap_does_not_truncate(tmp_path):
    f = tmp_path / "small.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant("2026-05-17T10:00:01Z", content=[_tool_use("Bash", "b1")]),
        ],
    )
    tl = derive_timeline(f, pid=1)
    assert tl.truncated is False


# ---------------------------------------------------------------------------
# Ended marker
# ---------------------------------------------------------------------------


def test_ended_marker_appended_for_finite_log(tmp_path):
    f = tmp_path / "ended.jsonl"
    _write(
        f,
        [
            _user("2026-05-17T10:00:00Z"),
            _assistant("2026-05-17T10:00:01Z", content=[_tool_use("Bash", "b1")]),
        ],
    )
    tl = derive_timeline(f, pid=1)
    # Last event should be "ended" with the last activity timestamp.
    assert tl.events[-1].type == "ended"


# ---------------------------------------------------------------------------
# Fixture-based smoke test (reuses existing JSONL fixtures)
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def test_sample_log_fixture_produces_expected_event_types():
    tl = derive_timeline(FIXTURES / "sample_log.jsonl", pid=999)
    types = {e.type for e in tl.events}
    # Sample log has: permission-mode, user msg, multi tool_use, thinking,
    # final permission-mode at the end. All these should appear.
    assert "started" in types
    assert "thinking_started" in types
    assert "first_tool" in types
    assert "tool_call" in types
    assert "permission_prompt" in types
    assert tl.events == sorted(tl.events, key=lambda e: e.timestamp)


def test_multi_assistant_fixture_handles_no_model_switch():
    # All assistant messages share the same model; no switch events.
    tl = derive_timeline(FIXTURES / "multi_assistant.jsonl", pid=1)
    switches = [e for e in tl.events if e.type == "model_switch"]
    assert switches == []


# ---------------------------------------------------------------------------
# API endpoint integration (uses TestClient fixture from test_api.py pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_session(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.STATE_DB", tmp_path / "state.db")
    monkeypatch.setattr("backend.server.STATE_DB", tmp_path / "state.db")

    async def _no_scheduler(s):
        return None

    monkeypatch.setattr("backend.server._scheduler_loop", _no_scheduler)
    from fastapi.testclient import TestClient

    from backend.models import ClaudeSession
    from backend.server import create_app

    app = create_app()

    log_path = tmp_path / "session.jsonl"
    _write(
        log_path,
        [
            _user("2026-05-17T10:00:00Z", cwd="/Users/x/y"),
            _assistant("2026-05-17T10:00:01Z", content=[_tool_use("Bash", "b1")]),
        ],
    )
    now = datetime.now(timezone.utc)
    sess = ClaudeSession(
        pid=4242,
        cwd="/Users/x/y",
        started_at=now,
        last_activity_at=now,
        conversation_log_path=str(log_path),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        app.state.s.sessions = {sess.pid: sess}
        yield client, app, sess


def test_endpoint_returns_timeline_for_known_session(app_with_session):
    client, _, sess = app_with_session
    r = client.get(f"/api/sessions/{sess.pid}/timeline")
    assert r.status_code == 200
    body = r.json()
    assert body["pid"] == sess.pid
    assert isinstance(body["events"], list)
    assert any(e["type"] == "started" for e in body["events"])


def test_endpoint_404_for_unknown_pid(app_with_session):
    client, _, _ = app_with_session
    r = client.get("/api/sessions/999999/timeline")
    assert r.status_code == 404
