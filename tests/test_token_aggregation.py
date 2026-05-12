from pathlib import Path

from backend.detectors.conversation_log import parse_log

FIXTURE = Path(__file__).parent / "fixtures" / "multi_assistant.jsonl"


def test_multi_assistant_sums_input_output():
    pl = parse_log(FIXTURE)
    assert pl.usage.input_tokens == 100 + 10 + 20 + 5
    assert pl.usage.output_tokens == 50 + 5 + 15 + 3


def test_multi_assistant_sums_cache():
    pl = parse_log(FIXTURE)
    assert pl.usage.cache_read_input_tokens == 200 + 100
    assert pl.usage.cache_creation_input_tokens == 30


def test_total_tokens_property():
    pl = parse_log(FIXTURE)
    expected = pl.usage.input_tokens + pl.usage.output_tokens + pl.usage.cache_read_input_tokens + pl.usage.cache_creation_input_tokens
    assert pl.usage.total_tokens == expected


def test_tool_breakdown_counts():
    pl = parse_log(FIXTURE)
    assert pl.tool_calls.total == 4
    assert pl.tool_calls.breakdown == {"Edit": 2, "Bash": 2}


def test_last_tool_used_is_most_recent():
    pl = parse_log(FIXTURE)
    assert pl.tool_calls.last_used == "Bash"  # t4 was the last tool_use entry


def test_permission_mode_takes_latest():
    pl = parse_log(FIXTURE)
    assert pl.permission_mode == "auto"  # later entry overrides "plan"


def test_message_count_excludes_system_entries():
    pl = parse_log(FIXTURE)
    # 2 user + 4 assistant = 6
    assert pl.message_count == 6


def test_thinking_detected_once_is_enough():
    pl = parse_log(FIXTURE)
    assert pl.thinking_enabled is True


def test_missing_usage_treated_as_zero(tmp_path):
    f = tmp_path / "no_usage.jsonl"
    f.write_text(
        '{"type":"assistant","message":{"model":"claude-opus-4-7","content":[]}}\n'
        '{"type":"assistant","message":{"model":"claude-opus-4-7","content":[],"usage":{"input_tokens":10}}}\n'
    )
    pl = parse_log(f)
    assert pl.usage.input_tokens == 10
    assert pl.usage.output_tokens == 0


def test_string_usage_values_treated_as_zero(tmp_path):
    f = tmp_path / "weird.jsonl"
    f.write_text(
        '{"type":"assistant","message":{"model":"x","content":[],"usage":{"input_tokens":null}}}\n'
    )
    pl = parse_log(f)
    assert pl.usage.input_tokens == 0


def test_empty_file_yields_empty_log(tmp_path):
    f = tmp_path / "empty.jsonl"
    f.write_text("")
    pl = parse_log(f)
    assert pl.message_count == 0
    assert pl.usage.total_tokens == 0
    assert pl.model is None
