from collections import deque

from backend.detectors.process_detector import (
    CpuHistory,
    infer_status,
    parse_cmdline,
)


def test_parse_cmdline_dangerously_skip():
    out = parse_cmdline(["claude", "--dangerously-skip-permissions"])
    assert out["permission_mode_flag"] == "dangerously-skip"
    assert "--dangerously-skip-permissions" in out["extra_flags"]


def test_parse_cmdline_allow_dangerously_skip():
    out = parse_cmdline(["claude", "--allow-dangerously-skip-permissions"])
    assert out["permission_mode_flag"] == "dangerously-skip"


def test_parse_cmdline_permission_mode_value():
    out = parse_cmdline(["claude", "--permission-mode", "auto"])
    assert out["permission_mode_flag"] == "auto"


def test_parse_cmdline_model_separate():
    out = parse_cmdline(["claude", "--model", "claude-opus-4-7"])
    assert out["model"] == "claude-opus-4-7"


def test_parse_cmdline_model_equals():
    out = parse_cmdline(["claude", "--model=claude-haiku-4-5"])
    assert out["model"] == "claude-haiku-4-5"


def test_parse_cmdline_no_flags():
    out = parse_cmdline(["claude"])
    assert out["model"] is None
    assert out["permission_mode_flag"] is None
    assert out["extra_flags"] == []


def test_parse_cmdline_keeps_value_flag_pair():
    out = parse_cmdline(["claude", "--mcp-config", "/path/cfg.json"])
    assert "--mcp-config" in out["extra_flags"]
    assert "/path/cfg.json" in out["extra_flags"]


def test_infer_status_working():
    h = CpuHistory(samples=deque([12.0, 10.0, 8.0, 9.0, 15.0], maxlen=30))
    assert infer_status(h, last_log_activity_seconds_ago=5) == "working"


def test_infer_status_idle_when_no_log_activity_for_a_while():
    h = CpuHistory(samples=deque([0.0] * 20, maxlen=30))
    assert infer_status(h, last_log_activity_seconds_ago=600) == "idle"


def test_infer_status_waiting_when_quiet_but_recent_activity():
    h = CpuHistory(samples=deque([0.0] * 20, maxlen=30))
    assert infer_status(h, last_log_activity_seconds_ago=30) == "waiting"


def test_infer_status_empty_history():
    h = CpuHistory()
    assert infer_status(h, last_log_activity_seconds_ago=None) == "idle"


def test_parse_cmdline_continue_does_not_swallow_next_flag():
    out = parse_cmdline(["claude", "--continue", "--model", "claude-opus-4-7"])
    assert out["model"] == "claude-opus-4-7"
    assert out["extra_flags"] == ["--continue", "--model", "claude-opus-4-7"]


def test_parse_cmdline_continue_alone():
    out = parse_cmdline(["claude", "--continue"])
    assert out["extra_flags"] == ["--continue"]
