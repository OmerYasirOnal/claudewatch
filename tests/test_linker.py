from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.config import DEFAULT_CONFIG
from backend.detectors.iterm_applescript import ItermTtyLocation
from backend.detectors.iterm_detector import ItermLocation
from backend.detectors.linker import LinkerState, build_sessions
from backend.detectors.process_detector import ProcInfo
from backend.detectors.tmux_detector import TmuxLocation

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _proc(pid: int, cwd: str, model: str | None = None, session_id: str | None = None) -> ProcInfo:
    cmdline = ["claude"]
    if model:
        cmdline += ["--model", model]
    if session_id:
        cmdline += ["--resume", session_id]
    return ProcInfo(
        pid=pid,
        ppid=1,
        cwd=cwd,
        started_at=datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC),
        cpu_percent=0.0,
        memory_mb=200.0,
        cmdline=cmdline,
        cmdline_parsed={
            "model": model,
            "permission_mode_flag": None,
            "session_id": session_id,
            "extra_flags": [],
        },
    )


@pytest.fixture
def isolated_log_dir(tmp_path, monkeypatch):
    """Build a fake ~/.claude/projects layout under tmp_path."""
    log_dir = tmp_path / "projects"
    cwd = "/tmp/fakecwd"  # outside home, but we patch find_logs_for_cwd via log_dir override
    target = log_dir / "-tmp-fakecwd"
    target.mkdir(parents=True)
    src = FIXTURE_DIR / "multi_assistant.jsonl"
    # Copy with a session-uuid filename so the linker can find it by sessionId
    (target / "sess-A.jsonl").write_bytes(src.read_bytes())
    yield log_dir, cwd


async def test_build_sessions_with_log_match(isolated_log_dir, monkeypatch):
    log_dir, cwd = isolated_log_dir
    procs = [_proc(pid=9999, cwd=cwd, session_id="sess-A")]
    state = LinkerState()
    state.log_dir = log_dir

    with (
        patch("backend.detectors.linker.scan_claude_processes", return_value=procs),
        patch("backend.detectors.linker.link_pids_to_tmux", return_value={}),
    ):
        sessions = await build_sessions(DEFAULT_CONFIG, state)

    assert len(sessions) == 1
    s = sessions[0]
    assert s.pid == 9999
    assert s.cwd == cwd
    assert s.model == "claude-opus-4-7"
    assert s.conversation_id == "sess-A"
    assert s.message_count == 6
    assert s.usage is not None
    assert s.usage.input_tokens == 135  # from multi_assistant fixture
    assert s.usage.output_tokens == 73
    assert s.usage.cache_read_input_tokens == 300
    assert s.tool_calls.total == 4
    assert s.tool_calls.breakdown == {"Edit": 2, "Bash": 2}
    assert s.permission_mode == "auto"
    assert s.thinking_enabled is True
    assert s.usage.cost_estimate_usd is not None  # opus-4-7 priced


async def test_build_sessions_with_tmux_location(isolated_log_dir):
    log_dir, cwd = isolated_log_dir
    procs = [_proc(pid=1234, cwd=cwd)]
    state = LinkerState()
    state.log_dir = log_dir
    tmux_map = {1234: TmuxLocation(session="main", window="0", pane="1")}

    with (
        patch("backend.detectors.linker.scan_claude_processes", return_value=procs),
        patch("backend.detectors.linker.link_pids_to_tmux", return_value=tmux_map),
    ):
        sessions = await build_sessions(DEFAULT_CONFIG, state)

    s = sessions[0]
    assert s.location_type == "tmux"
    assert s.tmux_session == "main"
    assert s.tmux_pane == "1"


async def test_build_sessions_disambiguates_two_pids_same_cwd(tmp_path):
    """Two claudes in the same cwd, each with its own --resume id → each gets the right log."""
    cwd = "/tmp/dupcwd"
    folder = tmp_path / "-tmp-dupcwd"
    folder.mkdir()
    (folder / "alpha.jsonl").write_text(
        '{"type":"assistant","message":{"model":"claude-opus-4-7","content":[],"usage":{"input_tokens":100}}}\n'
    )
    (folder / "beta.jsonl").write_text(
        '{"type":"assistant","message":{"model":"claude-sonnet-4-6","content":[],"usage":{"input_tokens":200}}}\n'
    )

    procs = [_proc(pid=1, cwd=cwd, session_id="alpha"), _proc(pid=2, cwd=cwd, session_id="beta")]
    state = LinkerState()
    state.log_dir = tmp_path

    with (
        patch("backend.detectors.linker.scan_claude_processes", return_value=procs),
        patch("backend.detectors.linker.link_pids_to_tmux", return_value={}),
    ):
        sessions = await build_sessions(DEFAULT_CONFIG, state)

    by_pid = {s.pid: s for s in sessions}
    assert by_pid[1].conversation_id == "alpha"
    assert by_pid[1].usage.input_tokens == 100
    assert by_pid[1].model == "claude-opus-4-7"
    assert by_pid[2].conversation_id == "beta"
    assert by_pid[2].usage.input_tokens == 200
    assert by_pid[2].model == "claude-sonnet-4-6"


async def test_build_sessions_consumes_iterm_loc_map_arg(isolated_log_dir):
    """build_sessions must use the iterm map passed in, never call iTerm itself."""
    log_dir, cwd = isolated_log_dir
    procs = [_proc(pid=5555, cwd=cwd)]
    state = LinkerState()
    state.log_dir = log_dir
    iterm_map = {5555: ItermLocation(window_id=1, tab_id=2, session_id="sess-abc", tab_title="my tab")}

    with (
        patch("backend.detectors.linker.scan_claude_processes", return_value=procs),
        patch("backend.detectors.linker.link_pids_to_tmux", return_value={}),
    ):
        sessions = await build_sessions(DEFAULT_CONFIG, state, iterm_loc_map=iterm_map, iterm_tty_map={})

    s = sessions[0]
    assert s.location_type == "iterm"
    assert s.iterm_window_id == 1
    assert s.iterm_tab_id == 2
    assert s.iterm_session_id == "sess-abc"
    assert s.iterm_tab_title == "my tab"


async def test_build_sessions_consumes_iterm_tty_map_arg(isolated_log_dir):
    """When only the AppleScript fallback map is populated, it is used."""
    log_dir, cwd = isolated_log_dir
    procs = [_proc(pid=6666, cwd=cwd)]
    state = LinkerState()
    state.log_dir = log_dir
    tty_map = {
        6666: ItermTtyLocation(window_id=7, tab_index=3, tty="/dev/ttys000", unique_id="u-1", name="tab-name")
    }

    with (
        patch("backend.detectors.linker.scan_claude_processes", return_value=procs),
        patch("backend.detectors.linker.link_pids_to_tmux", return_value={}),
    ):
        sessions = await build_sessions(DEFAULT_CONFIG, state, iterm_loc_map={}, iterm_tty_map=tty_map)

    s = sessions[0]
    assert s.location_type == "iterm"
    assert s.iterm_window_id == 7
    assert s.iterm_tab_index == 3
    assert s.iterm_tty == "/dev/ttys000"
    assert s.iterm_session_id == "u-1"
    assert s.iterm_tab_title == "tab-name"


async def test_build_sessions_no_log_gracefully(tmp_path):
    procs = [_proc(pid=42, cwd="/some/path/that/has/no/logs")]
    state = LinkerState()
    state.log_dir = tmp_path

    with (
        patch("backend.detectors.linker.scan_claude_processes", return_value=procs),
        patch("backend.detectors.linker.link_pids_to_tmux", return_value={}),
    ):
        sessions = await build_sessions(DEFAULT_CONFIG, state)

    s = sessions[0]
    assert s.usage is None
    assert s.conversation_id is None
    assert s.location_type == "headless"
    assert s.tool_calls.total == 0
