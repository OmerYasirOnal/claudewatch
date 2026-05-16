"""Performance benchmarks for hot paths.

Plain ``time.perf_counter`` assertions (no pytest-benchmark dep). The point is
to catch order-of-magnitude regressions on the paths that the 2s scheduler
tick depends on — not to produce statistically rigorous timings. Thresholds
are generous (4–10x typical observed wall-clock on this dev machine) so they
don't flake under CI load.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

from backend.config import DEFAULT_CONFIG
from backend.detectors.conversation_log import parse_log
from backend.detectors.tmux_detector import TmuxPane
from backend.models import (
    ClaudeSession,
    FileChange,
    SubagentRun,
    TokenUsage,
    ToolCallStats,
)
from backend.server import AppState, _emit_diffs, _session_hash
from backend.state import State

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mk_realistic_session(pid: int) -> ClaudeSession:
    """A session with 30 subagents, 20 file changes, 50 tool kinds.

    Mirrors what /api/sessions returns under sustained load — gives
    _session_hash a real payload to chew on (it walks the full Pydantic dump).
    """
    now = _now()
    subagents = [
        SubagentRun(
            tool_use_id=f"toolu_{i:03d}",
            description=f"Agent run #{i} doing some long-form task",
            subagent_type="general-purpose",
            started_at=now,
            ended_at=now if i % 2 == 0 else None,
            duration_seconds=12 + i,
            status="completed" if i % 2 == 0 else "pending",
            result_preview=("preview text for run " + str(i)) * 3,
        )
        for i in range(30)
    ]
    file_changes = [FileChange(path=f"src/module_{i:02d}.py", kind="modified", ts=now) for i in range(20)]
    breakdown = {f"Tool{i:02d}": (i * 3 + 1) for i in range(50)}
    return ClaudeSession(
        pid=pid,
        cwd=f"/Users/dev/Projects/proj-{pid}",
        started_at=now,
        duration_seconds=600,
        cpu_percent=12.3,
        memory_mb=812.0,
        status="working",
        location_type="iterm",
        iterm_window_id=f"pty-{pid}",
        iterm_tab_id=str(pid),
        iterm_session_id=f"session-{pid}",
        iterm_tty=f"/dev/ttys{pid % 256:03d}",
        last_activity_at=now,
        model="claude-opus-4-7",
        cli_version="1.2.3",
        conversation_id=f"conv-{pid}",
        conversation_log_path=f"/tmp/conv-{pid}.jsonl",
        message_count=42,
        usage=TokenUsage(
            input_tokens=12_000,
            output_tokens=3_400,
            cache_read_input_tokens=80_000,
            cache_creation_input_tokens=5_000,
            cost_estimate_usd=1.234,
        ),
        permission_mode="auto",
        tool_calls=ToolCallStats(
            total=sum(breakdown.values()),
            breakdown=breakdown,
            last_used="Tool00",
        ),
        recent_file_changes=file_changes,
        subagents=subagents,
    )


# ---------------------------------------------------------------------------
# parse_log
# ---------------------------------------------------------------------------


def test_parse_log_resists_regex_dos(tmp_path):
    """Issue #86: a user prompt with thousands of unmatched <task-notification>
    opens used to cause O(n^2) backtracking inside ``_TASK_NOTIF_BLOCK_RE``
    because of the lazy ``.*?`` body. With the non-backtracking ``str.find``
    scan and the length-cap the parser must finish near-instantly even on
    adversarial input."""
    f = tmp_path / "dos.jsonl"
    # 5000 unmatched opens followed by a stray <task-id> — the regex used
    # to walk back over every open looking for a matching close on each
    # attempt.
    nasty = "<task-notification>" * 5000 + "<task-id>x</task-id>"
    f.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-05-17T10:00:00Z",
                "cwd": "/Users/dev/proj",
                "message": {
                    "content": [{"type": "text", "text": nasty}],
                },
            }
        )
        + "\n"
    )

    start = time.perf_counter()
    pl = parse_log(f)
    elapsed = time.perf_counter() - start

    # Parser ran (didn't crash). We don't care that nothing useful was
    # extracted from the garbage input.
    assert pl.message_count == 1
    assert elapsed < 0.2, f"parse_log took {elapsed:.3f}s on regex-DoS input"


def test_parse_log_handles_10k_entries_in_under_2s(tmp_path):
    """A 10k-line JSONL must parse in well under one scheduler tick.

    Realistic shape: alternating user/assistant, with one tool_use Agent every
    100 lines. Aggregates usage, breakdown, subagents — i.e. exercises the
    full hot path inside ``parse_log``.
    """
    log = tmp_path / "big.jsonl"
    lines: list[str] = []
    ts = "2026-05-12T10:00:00Z"
    for i in range(10_000):
        if i % 2 == 0:
            lines.append(
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": ts,
                        "cwd": "/Users/dev/proj",
                        "message": {"content": f"prompt {i}"},
                    }
                )
            )
        else:
            content: list[dict] = [{"type": "text", "text": f"reply {i}"}]
            if i % 100 == 1:
                # Occasional Agent tool_use — exercises the subagent path.
                content.append(
                    {
                        "type": "tool_use",
                        "id": f"toolu_{i:05d}",
                        "name": "Agent",
                        "input": {
                            "description": "sub-task",
                            "subagent_type": "general-purpose",
                        },
                    }
                )
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": ts,
                        "message": {
                            "model": "claude-opus-4-7",
                            "usage": {
                                "input_tokens": 10,
                                "output_tokens": 5,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0,
                            },
                            "stop_reason": "end_turn",
                            "content": content,
                        },
                    }
                )
            )
    log.write_text("\n".join(lines) + "\n")

    start = time.perf_counter()
    pl = parse_log(log)
    elapsed = time.perf_counter() - start

    # Sanity: parser actually consumed the file.
    assert pl.message_count == 10_000
    assert pl.usage.input_tokens == 5_000 * 10
    assert elapsed < 2.0, f"parse_log took {elapsed:.3f}s for 10k entries"


# ---------------------------------------------------------------------------
# _session_hash
# ---------------------------------------------------------------------------


def test_session_hash_is_under_1ms():
    """The diff emitter calls _session_hash once per active session, every
    tick. At ~50 sessions this needs to stay well under the 2s tick budget."""
    sess = _mk_realistic_session(pid=1234)
    iterations = 100
    start = time.perf_counter()
    for _ in range(iterations):
        _session_hash(sess)
    elapsed = time.perf_counter() - start
    avg_ms = (elapsed / iterations) * 1000
    assert avg_ms < 1.0, f"_session_hash avg {avg_ms:.3f}ms — too slow"


# ---------------------------------------------------------------------------
# _emit_diffs
# ---------------------------------------------------------------------------


async def test_emit_diffs_skips_unchanged_in_constant_time():
    """Second emit with identical sessions must produce zero broadcasts —
    the hash-equal path short-circuits before serializing for SSE."""
    s = AppState(config=dict(DEFAULT_CONFIG))
    events: list[dict] = []

    async def _capture(event):
        events.append(event)

    s.broadcast = _capture  # type: ignore[assignment]
    sessions = [_mk_realistic_session(pid=1000 + i) for i in range(10)]

    # Prime the hash cache.
    await _emit_diffs(s, sessions)
    initial_count = len(events)
    assert initial_count == 10  # all session.started

    # Identical sessions: no events, fast path.
    events.clear()
    start = time.perf_counter()
    await _emit_diffs(s, sessions)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert events == []
    assert elapsed_ms < 5.0, f"unchanged-skip path took {elapsed_ms:.3f}ms"


async def test_emit_diffs_handles_100_sessions_in_under_50ms():
    """100 brand-new sessions all firing session.started must finish well
    inside one tick. This is the worst-case shape (full hash + broadcast for
    every pid)."""
    s = AppState(config=dict(DEFAULT_CONFIG))

    async def _noop(event):
        return None

    s.broadcast = _noop  # type: ignore[assignment]
    sessions = [_mk_realistic_session(pid=2000 + i) for i in range(100)]
    start = time.perf_counter()
    await _emit_diffs(s, sessions)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 50.0, f"100-session emit took {elapsed_ms:.3f}ms"


# ---------------------------------------------------------------------------
# link_pids_to_tmux
# ---------------------------------------------------------------------------


def test_link_pids_to_tmux_with_500_pids(monkeypatch):
    """Tmux linker iterates panes x descendants; verify it stays cheap at the
    upper end of what a workstation might actually run (20 panes, 500 PIDs)."""
    from backend.detectors import tmux_detector

    panes = [
        TmuxPane(
            session="work",
            window=str(i),
            pane="0",
            pane_pid=100_000 + i,
            current_command="zsh",
            current_path="/tmp",
        )
        for i in range(20)
    ]
    monkeypatch.setattr(tmux_detector, "list_tmux_panes", lambda: panes)

    # 25 descendants per pane, none of which (intentionally) match the claude
    # PIDs we hand in — we want to measure the iteration cost, not the hit
    # count.
    def fake_descendants(pid, max_depth=10):
        return {pid + j * 10_000 for j in range(25)}

    monkeypatch.setattr(tmux_detector, "_descendants", fake_descendants)

    claude_pids = list(range(50_000, 50_500))  # 500 PIDs

    start = time.perf_counter()
    result = tmux_detector.link_pids_to_tmux(claude_pids)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert isinstance(result, dict)
    assert elapsed_ms < 100.0, f"link_pids_to_tmux took {elapsed_ms:.3f}ms"


# ---------------------------------------------------------------------------
# State.upsert_active concurrent (PR #19 — connection pool)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
async def test_state_upsert_500_sessions_concurrent(tmp_path):
    """500 concurrent upserts on a single connection-pool-backed State must
    not deadlock or serialize past the 2s scheduler budget."""
    state = State(tmp_path / "state.db")
    await state.init_db()
    try:
        sessions = [_mk_realistic_session(pid=10_000 + i) for i in range(500)]
        start = time.perf_counter()
        await asyncio.gather(*[state.upsert_active(s) for s in sessions])
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"500 concurrent upserts took {elapsed:.3f}s"
    finally:
        await state.close()
