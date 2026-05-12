from pathlib import Path

from backend.detectors.conversation_log import (
    cwd_to_project_folder,
    parse_log,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_log.jsonl"


def test_cwd_to_project_folder():
    assert cwd_to_project_folder("/Users/x/Projects/y") == "-Users-x-Projects-y"
    assert cwd_to_project_folder("/Users/x/Projects/y/") == "-Users-x-Projects-y"


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
