from datetime import datetime

from backend.models import (
    ClaudeSession,
    FileChange,
    GitContext,
    HealthReport,
    NewSessionRequest,
    TokenUsage,
    ToolCallStats,
)


def test_token_usage_total_tokens():
    u = TokenUsage(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=200,
        cache_creation_input_tokens=10,
    )
    assert u.total_tokens == 360


def test_tool_call_stats_defaults():
    s = ToolCallStats()
    assert s.total == 0
    assert s.breakdown == {}
    assert s.last_used is None


def test_file_change_validates_kind():
    fc = FileChange(path="a.py", kind="modified", ts=datetime.now())
    assert fc.kind == "modified"


def test_git_context_defaults():
    g = GitContext()
    assert g.branch is None
    assert g.is_dirty is False


def test_new_session_request_defaults():
    r = NewSessionRequest(cwd="/Users/me")
    assert r.window_type == "new-window"
    assert r.command == "claude"
    assert r.flags == []


def test_claude_session_minimal():
    now = datetime.now()
    s = ClaudeSession(pid=123, cwd="/x", started_at=now, last_activity_at=now)
    assert s.status == "idle"
    assert s.location_type == "headless"
    assert s.tool_calls.total == 0


def test_health_report_defaults():
    h = HealthReport(iterm_api=True, automation=True, tmux_available=True, log_dir_found=True)
    assert h.issues == []
