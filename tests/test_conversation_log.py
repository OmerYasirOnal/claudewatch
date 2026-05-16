import json
from datetime import datetime, timezone
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


def _agent_tool_use_entry(ts: str, tool_use_id: str, description: str, subagent_type: str) -> str:
    return (
        json.dumps(
            {
                "type": "assistant",
                "timestamp": ts,
                "message": {
                    "model": "claude-opus-4-7",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": "Agent",
                            "input": {
                                "description": description,
                                "subagent_type": subagent_type,
                                "prompt": "do the thing",
                            },
                        }
                    ],
                },
            }
        )
        + "\n"
    )


def _tool_result_entry(ts: str, tool_use_id: str, content) -> str:
    return (
        json.dumps(
            {
                "type": "user",
                "timestamp": ts,
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": content,
                        }
                    ]
                },
            }
        )
        + "\n"
    )


def test_parse_log_extracts_subagent_runs(tmp_path):
    f = tmp_path / "agent.jsonl"
    f.write_text(
        _agent_tool_use_entry(
            "2026-04-15T19:16:58.000Z",
            "toolu_aaa",
            "X",
            "Explore",
        )
        + _tool_result_entry(
            "2026-04-15T19:17:10.000Z",
            "toolu_aaa",
            "the final answer here",
        )
    )
    pl = parse_log(f)
    assert len(pl.subagents) == 1
    run = pl.subagents[0]
    assert run.tool_use_id == "toolu_aaa"
    assert run.description == "X"
    assert run.subagent_type == "Explore"
    assert run.started_at == datetime(2026, 4, 15, 19, 16, 58, tzinfo=timezone.utc)
    assert run.ended_at == datetime(2026, 4, 15, 19, 17, 10, tzinfo=timezone.utc)
    assert run.duration_seconds == 12
    assert run.status == "completed"
    assert run.result_preview == "the final answer here"


def test_parse_log_pending_subagent_when_result_missing(tmp_path):
    f = tmp_path / "pending.jsonl"
    f.write_text(
        _agent_tool_use_entry(
            "2026-04-15T19:16:58.000Z",
            "toolu_bbb",
            "Investigation",
            "Explore",
        )
    )
    pl = parse_log(f)
    assert len(pl.subagents) == 1
    run = pl.subagents[0]
    assert run.status == "pending"
    assert run.ended_at is None
    assert run.duration_seconds is None
    assert run.result_preview is None


def test_parse_log_subagent_result_from_list_content(tmp_path):
    f = tmp_path / "list_content.jsonl"
    long_text = "hello world " + ("x" * 500)
    f.write_text(
        _agent_tool_use_entry(
            "2026-04-15T19:16:58.000Z",
            "toolu_ccc",
            "Greeter",
            "general-purpose",
        )
        + _tool_result_entry(
            "2026-04-15T19:17:00.000Z",
            "toolu_ccc",
            [{"type": "text", "text": long_text}],
        )
    )
    pl = parse_log(f)
    assert len(pl.subagents) == 1
    run = pl.subagents[0]
    assert run.status == "completed"
    assert run.result_preview is not None
    assert run.result_preview.startswith("hello world")
    assert len(run.result_preview) == 200


def test_parse_log_multiple_subagents_ordered_by_start(tmp_path):
    f = tmp_path / "many.jsonl"
    # Write entries out of chronological order to confirm sort is by started_at.
    f.write_text(
        _agent_tool_use_entry("2026-04-15T19:30:00.000Z", "toolu_z", "third", "Explore")
        + _agent_tool_use_entry("2026-04-15T19:10:00.000Z", "toolu_x", "first", "Explore")
        + _agent_tool_use_entry("2026-04-15T19:20:00.000Z", "toolu_y", "second", "Explore")
    )
    pl = parse_log(f)
    assert [r.description for r in pl.subagents] == ["first", "second", "third"]
    assert [r.tool_use_id for r in pl.subagents] == ["toolu_x", "toolu_y", "toolu_z"]
