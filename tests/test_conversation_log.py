from pathlib import Path

from backend.detectors.conversation_log import (
    cwd_to_project_folder,
    parse_log,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_log.jsonl"


def test_cwd_to_project_folder():
    assert cwd_to_project_folder("/Users/x/Projects/y") == "-Users-x-Projects-y"
    assert cwd_to_project_folder("/Users/x/Projects/y/") == "-Users-x-Projects-y"


def test_cwd_to_project_folder_username_with_dot():
    # macOS Active Directory accounts and similar identity providers commonly
    # produce usernames with a dot (e.g. `first.last`). Claude Code encodes
    # `.` as `-` in the project folder name, matching its `/` handling.
    assert cwd_to_project_folder("/Users/s.onal/Projects/claudewatch") == "-Users-s-onal-Projects-claudewatch"


def test_cwd_to_project_folder_dotted_directory():
    # Hidden directories like `.claude` and dotted folder names must be encoded
    # the same way Claude Code stores them on disk.
    assert cwd_to_project_folder("/Users/x/.claude/worktrees/repo") == "-Users-x--claude-worktrees-repo"


def test_find_logs_for_cwd_with_dotted_username(tmp_path):
    # End-to-end check against a fake log dir laid out the way Claude Code
    # writes it on a machine whose username contains a dot.
    from backend.detectors.conversation_log import find_logs_for_cwd

    folder = tmp_path / "-Users-s-onal-Projects-claudewatch"
    folder.mkdir()
    log = folder / "session-abc.jsonl"
    log.write_text("{}\n")
    found = find_logs_for_cwd("/Users/s.onal/Projects/claudewatch", tmp_path)
    assert found == [log]


def test_parse_log_basic_metadata():
    pl = parse_log(FIXTURE)
    assert pl.conversation_id == "sample_log"
    assert pl.model == "claude-opus-4-7"
    assert pl.cli_version is not None
    assert pl.cwd == "/Users/example/Projects/demo"
    assert pl.permission_mode == "auto"


def test_parse_log_aggregates_usage():
    pl = parse_log(FIXTURE)
    # Pre-computed from fixture inspection
    assert pl.usage.input_tokens == 37
    assert pl.usage.output_tokens == 11685
    assert pl.usage.cache_read_input_tokens == 580592
    assert pl.usage.cache_creation_input_tokens == 48188


def test_parse_log_tool_calls():
    pl = parse_log(FIXTURE)
    assert pl.tool_calls.total == 7
    assert pl.tool_calls.breakdown == {"Bash": 5, "Write": 2}


def test_parse_log_thinking_detected():
    pl = parse_log(FIXTURE)
    assert pl.thinking_enabled is True


def test_parse_log_handles_malformed_lines(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text(
        '{"type":"user","timestamp":"2026-01-01T00:00:00Z","cwd":"/a"}\n'
        "not-json-at-all\n"
        '{"type":"assistant","message":{"model":"claude-opus-4-7",'
        '"usage":{"input_tokens":10,"output_tokens":5},"content":[]}}\n'
    )
    pl = parse_log(f)
    assert pl.model == "claude-opus-4-7"
    assert pl.usage.input_tokens == 10
    assert pl.message_count == 2


def test_parse_log_handles_non_numeric_tokens(tmp_path):
    """Issue #29: malformed `usage.*_tokens` (string, dict, etc.) must NOT crash
    the parser — the offending field is treated as 0, everything else still
    aggregates."""
    f = tmp_path / "weird.jsonl"
    f.write_text(
        '{"type":"assistant","message":{"model":"claude-opus-4-7",'
        '"usage":{"input_tokens":"not a number","output_tokens":5,'
        '"cache_read_input_tokens":null,"cache_creation_input_tokens":{"x":1}},'
        '"content":[]}}\n'
        '{"type":"assistant","message":{"model":"claude-opus-4-7",'
        '"usage":{"input_tokens":7,"output_tokens":2},"content":[]}}\n'
    )
    pl = parse_log(f)
    # Garbage fields contribute 0; well-formed siblings still count.
    assert pl.usage.input_tokens == 7
    assert pl.usage.output_tokens == 7
    assert pl.usage.cache_read_input_tokens == 0
    assert pl.usage.cache_creation_input_tokens == 0
    assert pl.message_count == 2


def test_parse_log_normalizes_naive_timestamps(tmp_path):
    """Issue #46: a JSONL row with an offset-less timestamp must parse to a
    tz-aware datetime (assumed UTC), so `(now - last_activity_at)` math
    elsewhere in the codebase doesn't raise the "can't subtract naive and
    aware" TypeError."""
    f = tmp_path / "naive.jsonl"
    # Note: NO `Z`, NO `+00:00` — a naive ISO timestamp.
    f.write_text(
        '{"type":"user","timestamp":"2026-01-01T00:00:00","cwd":"/a"}\n'
        '{"type":"assistant","timestamp":"2026-01-01T00:00:05",'
        '"message":{"model":"claude-opus-4-7","usage":{"input_tokens":1,'
        '"output_tokens":1},"content":[]}}\n'
    )
    pl = parse_log(f)
    assert pl.last_activity_at is not None
    assert pl.last_activity_at.tzinfo is not None
    assert pl.last_assistant_at is not None
    assert pl.last_assistant_at.tzinfo is not None
