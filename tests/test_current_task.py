"""Tests for current-task tracking in conversation_log parser."""
from __future__ import annotations

import json
from datetime import datetime

from backend.detectors.conversation_log import parse_log


def _entry(idx: int, ts: str, content: list) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": f"u{idx}",
            "timestamp": ts,
            "message": {"model": "claude-opus-4-7", "content": content, "stop_reason": "tool_use", "usage": {"input_tokens": 1}},
        }
    )


def test_in_progress_task_subject_captured(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        _entry(1, "2026-01-01T00:00:00Z", [{"type": "tool_use", "name": "TaskCreate", "input": {"subject": "Run all tests", "activeForm": "Running tests"}}])
        + "\n"
        + _entry(2, "2026-01-01T00:00:01Z", [{"type": "tool_use", "name": "TaskUpdate", "input": {"taskId": "1", "status": "in_progress"}}])
        + "\n"
    )
    pl = parse_log(f)
    assert pl.current_task_subject == "Run all tests"
    assert pl.current_task_active_form == "Running tests"
    assert pl.current_task_id == "1"
    assert pl.current_task_started_at is not None


def test_in_progress_cleared_when_completed(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        _entry(1, "2026-01-01T00:00:00Z", [{"type": "tool_use", "name": "TaskCreate", "input": {"subject": "S"}}])
        + "\n"
        + _entry(2, "2026-01-01T00:00:01Z", [{"type": "tool_use", "name": "TaskUpdate", "input": {"taskId": "1", "status": "in_progress"}}])
        + "\n"
        + _entry(3, "2026-01-01T00:00:02Z", [{"type": "tool_use", "name": "TaskUpdate", "input": {"taskId": "1", "status": "completed"}}])
        + "\n"
    )
    pl = parse_log(f)
    assert pl.current_task_subject is None
    assert pl.current_task_id is None


def test_latest_in_progress_wins(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        _entry(1, "2026-01-01T00:00:00Z", [{"type": "tool_use", "name": "TaskCreate", "input": {"subject": "First"}}])
        + "\n"
        + _entry(2, "2026-01-01T00:00:01Z", [{"type": "tool_use", "name": "TaskUpdate", "input": {"taskId": "1", "status": "in_progress"}}])
        + "\n"
        + _entry(3, "2026-01-01T00:00:02Z", [{"type": "tool_use", "name": "TaskUpdate", "input": {"taskId": "1", "status": "completed"}}])
        + "\n"
        + _entry(4, "2026-01-01T00:00:03Z", [{"type": "tool_use", "name": "TaskCreate", "input": {"subject": "Second"}}])
        + "\n"
        + _entry(5, "2026-01-01T00:00:04Z", [{"type": "tool_use", "name": "TaskUpdate", "input": {"taskId": "2", "status": "in_progress"}}])
        + "\n"
    )
    pl = parse_log(f)
    assert pl.current_task_subject == "Second"
    assert pl.current_task_id == "2"


def test_last_assistant_usage_is_not_cumulative(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        '{"type":"assistant","message":{"model":"claude-opus-4-7","content":[],"usage":{"input_tokens":100,"output_tokens":50,"cache_read_input_tokens":1000}}}\n'
        '{"type":"assistant","message":{"model":"claude-opus-4-7","content":[],"usage":{"input_tokens":5,"output_tokens":3,"cache_read_input_tokens":2000}}}\n'
    )
    pl = parse_log(f)
    # cumulative
    assert pl.usage.input_tokens == 105
    assert pl.usage.output_tokens == 53
    # latest snapshot only
    assert pl.last_assistant_usage.input_tokens == 5
    assert pl.last_assistant_usage.output_tokens == 3
    assert pl.last_assistant_usage.cache_read_input_tokens == 2000


def test_in_flight_when_stop_reason_is_tool_use(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        '{"type":"assistant","message":{"model":"x","content":[],"usage":{},"stop_reason":"tool_use"}}\n'
    )
    pl = parse_log(f)
    assert pl.is_in_flight is True
    assert pl.last_stop_reason == "tool_use"


def test_not_in_flight_when_end_turn(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        '{"type":"assistant","message":{"model":"x","content":[],"usage":{},"stop_reason":"end_turn"}}\n'
    )
    pl = parse_log(f)
    assert pl.is_in_flight is False


def test_in_flight_when_stop_reason_missing(tmp_path):
    """Mid-stream entry without stop_reason yet — treat as in_flight."""
    f = tmp_path / "t.jsonl"
    f.write_text(
        '{"type":"assistant","message":{"model":"x","content":[],"usage":{}}}\n'
    )
    pl = parse_log(f)
    assert pl.is_in_flight is True


def test_task_update_without_matching_create(tmp_path):
    """Resumed session: TaskUpdate references a taskId created in a prior log file."""
    f = tmp_path / "t.jsonl"
    f.write_text(
        _entry(1, "2026-01-01T00:00:00Z", [{"type": "tool_use", "name": "TaskUpdate", "input": {"taskId": "99", "status": "in_progress"}}])
        + "\n"
    )
    pl = parse_log(f)
    # We don't know the subject (created in another file), but we still surface the in-progress signal
    assert pl.current_task_id == "99"
    assert pl.current_task_started_at is not None


def test_taskupdate_input_can_be_non_dict(tmp_path):
    """Schema-drift safety: don't crash if TaskUpdate input shape is unexpected."""
    f = tmp_path / "t.jsonl"
    f.write_text(
        _entry(1, "2026-01-01T00:00:00Z", [{"type": "tool_use", "name": "TaskUpdate", "input": "weird-string"}])
        + "\n"
    )
    pl = parse_log(f)
    assert pl.current_task_id is None
