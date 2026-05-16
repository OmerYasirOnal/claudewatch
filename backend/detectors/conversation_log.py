from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.models import SubagentRun, TokenUsage, ToolCallStats

# Issue #66: when Agent is invoked with run_in_background:true, the matching
# tool_result is a dispatch acknowledgment, not the real completion. We
# recognize it by the leading text and extract the agentId so we can match
# the later <task-notification> entry.
_ASYNC_ACK_RE = re.compile(r"Async agent launched successfully\.?\s*[·\-:]?\s*agentId:\s*([A-Za-z0-9_-]+)")

# Background completions arrive as a user-entry whose serialized content
# contains a <task-notification> block keyed by <task-id>. We parse with a
# regex rather than a real XML parser because the surrounding text may
# contain arbitrary characters (including unescaped angle brackets) and
# we only need a few specific fields.
_TASK_NOTIF_BLOCK_RE = re.compile(
    r"<task-notification>(.*?)</task-notification>",
    re.DOTALL,
)
_TASK_ID_RE = re.compile(r"<task-id>([A-Za-z0-9_-]+)</task-id>")
_TASK_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_TASK_RESULT_RE = re.compile(r"<result>(.*?)</result>", re.DOTALL)

log = logging.getLogger(__name__)


def _safe_int(value: Any) -> int:
    """Coerce arbitrary JSONL field values into a non-negative int.

    Conversation logs are written by the Claude CLI and have been observed to
    contain unexpected types in `usage.*_tokens` (e.g. strings, floats, None)
    on partial writes or schema drift. We never want session parsing to crash
    over a single bad number — return 0 in that case (Issue #29).
    """
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


LOG_DIR_CANDIDATES = [
    Path.home() / ".claude" / "projects",
    Path.home() / ".config" / "claude" / "projects",
    Path.home() / "Library" / "Application Support" / "Claude" / "projects",
]


def find_log_dir() -> Path | None:
    for p in LOG_DIR_CANDIDATES:
        if p.is_dir():
            return p
    return None


def cwd_to_project_folder(cwd: str) -> str:
    """Convert /Users/x/Projects/y to -Users-x-Projects-y (Claude Code convention).

    Claude Code encodes both `/` and `.` as `-`, so usernames like `first.last`
    (common with macOS AD-joined accounts) and dotted directories like
    `.claude` map to `first-last` and `-claude` respectively.
    """
    cwd = cwd.rstrip("/")
    if not cwd:
        return ""
    return cwd.replace("/", "-").replace(".", "-")


def find_logs_for_cwd(cwd: str, log_dir: Path | None = None) -> list[Path]:
    base = log_dir or find_log_dir()
    if base is None:
        return []
    folder = base / cwd_to_project_folder(cwd)
    if not folder.is_dir():
        return []
    files = [f for f in folder.glob("*.jsonl") if f.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    # Issue #46: some log lines carry naive timestamps (no offset / no `Z`).
    # Treat them as UTC so downstream `(now - ts)` math doesn't raise
    # TypeError("can't subtract offset-naive and offset-aware datetimes").
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class ParsedLog:
    conversation_id: str
    log_path: Path
    model: str | None = None
    cli_version: str | None = None
    permission_mode: str | None = None
    message_count: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    thinking_enabled: bool = False
    tool_calls: ToolCallStats = field(default_factory=ToolCallStats)
    last_activity_at: datetime | None = None
    cwd: str | None = None
    git_branch: str | None = None
    # Latest assistant turn (NOT cumulative) — for context % and "current-turn" display
    last_assistant_at: datetime | None = None
    last_assistant_usage: TokenUsage = field(default_factory=TokenUsage)
    last_stop_reason: str | None = None
    # True when last entry is assistant with stop_reason="tool_use" or null/None,
    # meaning the model is mid-stream (called a tool, awaiting result).
    is_in_flight: bool = False
    # Currently in-progress Task* task (if any)
    current_task_subject: str | None = None
    current_task_active_form: str | None = None
    current_task_id: str | None = None
    current_task_started_at: datetime | None = None
    subagents: list[SubagentRun] = field(default_factory=list)


def _extract_tool_result_preview(content: Any) -> str | None:
    """Extract a short preview from a tool_result's `content` field.

    The Claude Code JSONL format stores tool_result content as either:
      - a raw string (most tools), or
      - a list of typed blocks like `[{"type": "text", "text": "..."}, ...]`.
    We collapse newlines to " · " for compact single-line display and cap at
    200 characters.
    """
    text: str | None = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    text = t
                    break
    if text is None:
        return None
    text = text.strip().replace("\r\n", "\n").replace("\n", " · ")
    return text[:200]


def parse_log(path: Path, now: datetime | None = None) -> ParsedLog:
    """Walk all JSONL entries and aggregate into ParsedLog. Robust to schema drift.

    `now` is the reference time used to decide whether a `tool_use` stop_reason
    still counts as in-flight (see Issue #7). Defaults to current UTC time;
    tests inject a fixed value.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    pl = ParsedLog(conversation_id=path.stem, log_path=path)
    breakdown: dict[str, int] = {}
    last_tool_used: str | None = None
    last_tool_used_at: datetime | None = None
    # Task tracking: index every TaskCreate by its 1-based position within THIS file,
    # then walk TaskUpdate calls in order to determine which task is currently in_progress.
    # Note: Task IDs are global across the whole Claude session and may reference tasks
    # created in prior log files. We can't resolve those cross-session.
    tasks_created_here: list[dict] = []  # [{"subject", "active_form", "ts", "local_id"}]
    current_in_progress: dict | None = None
    current_in_progress_started_at: datetime | None = None
    # Subagent tracking: Agent tool_use → matching tool_result by tool_use_id.
    # We keep them as a dict so we can mutate the entry when its tool_result arrives.
    subagents_by_id: dict[str, SubagentRun] = {}
    # Issue #66: background Agent dispatches store their assigned agentId here
    # so we can match the later <task-notification> user entry.
    # tool_use_id → agentId
    agent_id_by_tool_use: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(entry.get("timestamp"))
                if ts and (pl.last_activity_at is None or ts > pl.last_activity_at):
                    pl.last_activity_at = ts
                if not pl.cwd and entry.get("cwd"):
                    pl.cwd = entry.get("cwd")
                if not pl.cli_version and entry.get("version"):
                    pl.cli_version = entry.get("version")
                if not pl.git_branch and entry.get("gitBranch"):
                    pl.git_branch = entry.get("gitBranch")

                etype = entry.get("type")
                if etype == "permission-mode":
                    pm = entry.get("permissionMode")
                    if pm:
                        pl.permission_mode = pm
                elif etype in ("user", "assistant"):
                    pl.message_count += 1
                if etype == "user":
                    # Scan tool_result blocks for matches against pending Agent runs.
                    msg = entry.get("message") or {}
                    content = msg.get("content") or []
                    if isinstance(content, list):
                        # Collect all text across this user-entry's content so we can
                        # look for a <task-notification> block (Issue #66 — background
                        # subagent completions are delivered this way, not as a
                        # tool_result).
                        notification_text_parts: list[str] = []
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type")
                            if btype == "text":
                                t = block.get("text")
                                if isinstance(t, str):
                                    notification_text_parts.append(t)
                                continue
                            if btype != "tool_result":
                                continue
                            tu_id = block.get("tool_use_id")
                            if not isinstance(tu_id, str):
                                continue
                            run = subagents_by_id.get(tu_id)
                            if run is None:
                                continue
                            preview = _extract_tool_result_preview(block.get("content"))
                            ack_match = _ASYNC_ACK_RE.search(preview or "")
                            if ack_match:
                                # Issue #66: background dispatch ACK — don't mark the
                                # run as completed; remember the agentId so a later
                                # <task-notification> entry can close it out.
                                agent_id_by_tool_use[tu_id] = ack_match.group(1)
                                continue
                            run.ended_at = ts
                            if ts is not None:
                                run.duration_seconds = max(0, int((ts - run.started_at).total_seconds()))
                            run.status = "completed"
                            run.result_preview = preview
                        if notification_text_parts:
                            joined = "\n".join(notification_text_parts)
                            if "<task-notification>" in joined and "<task-id>" in joined:
                                for block_match in _TASK_NOTIF_BLOCK_RE.finditer(joined):
                                    body = block_match.group(1)
                                    id_match = _TASK_ID_RE.search(body)
                                    if id_match is None:
                                        continue
                                    agent_id = id_match.group(1)
                                    summary_match = _TASK_SUMMARY_RE.search(body)
                                    result_match = _TASK_RESULT_RE.search(body)
                                    summary = summary_match.group(1) if summary_match else None
                                    result_text = result_match.group(1) if result_match else None
                                    # Resolve the SubagentRun whose stored agentId
                                    # matches this <task-id>.
                                    matched_tu_id = next(
                                        (tu for tu, aid in agent_id_by_tool_use.items() if aid == agent_id),
                                        None,
                                    )
                                    if matched_tu_id is None:
                                        continue
                                    run = subagents_by_id.get(matched_tu_id)
                                    if run is None:
                                        continue
                                    run.ended_at = ts
                                    if ts is not None:
                                        run.duration_seconds = max(
                                            0,
                                            int((ts - run.started_at).total_seconds()),
                                        )
                                    run.status = "completed"
                                    preview_src: str | None = None
                                    if result_text and result_text.strip():
                                        preview_src = result_text.strip()
                                    elif summary and summary.strip():
                                        preview_src = summary.strip()
                                    if preview_src is not None:
                                        preview_src = preview_src.replace("\r\n", "\n").replace("\n", " · ")
                                        run.result_preview = preview_src[:200]
                if etype == "assistant":
                    msg = entry.get("message") or {}
                    model = msg.get("model")
                    if model:
                        pl.model = model
                    usage = msg.get("usage") or {}
                    in_t = _safe_int(usage.get("input_tokens"))
                    out_t = _safe_int(usage.get("output_tokens"))
                    cr_t = _safe_int(usage.get("cache_read_input_tokens"))
                    cc_t = _safe_int(usage.get("cache_creation_input_tokens"))
                    pl.usage.input_tokens += in_t
                    pl.usage.output_tokens += out_t
                    pl.usage.cache_read_input_tokens += cr_t
                    pl.usage.cache_creation_input_tokens += cc_t
                    # Latest assistant snapshot (overwritten each iteration)
                    pl.last_assistant_at = ts
                    pl.last_assistant_usage = TokenUsage(
                        input_tokens=in_t,
                        output_tokens=out_t,
                        cache_read_input_tokens=cr_t,
                        cache_creation_input_tokens=cc_t,
                    )
                    pl.last_stop_reason = msg.get("stop_reason")
                    # In-flight: assistant turn that's still mid-stream (tool_use stop
                    # means a tool call was issued and the assistant is waiting for the result,
                    # or null/None means streaming isn't finished).
                    pl.is_in_flight = pl.last_stop_reason in (None, "tool_use")

                    content = msg.get("content") or []
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type")
                            if btype == "thinking":
                                pl.thinking_enabled = True
                            elif btype == "tool_use":
                                name = block.get("name") or "unknown"
                                breakdown[name] = breakdown.get(name, 0) + 1
                                pl.tool_calls.total += 1
                                last_tool_used = name
                                last_tool_used_at = ts
                                inp = block.get("input") or {}
                                if name == "Agent" and isinstance(inp, dict) and ts is not None:
                                    tool_use_id = block.get("id")
                                    if isinstance(tool_use_id, str) and tool_use_id:
                                        subagents_by_id[tool_use_id] = SubagentRun(
                                            tool_use_id=tool_use_id,
                                            description=str(inp.get("description") or ""),
                                            subagent_type=str(inp.get("subagent_type") or "general-purpose"),
                                            started_at=ts,
                                            status="pending",
                                        )
                                if name == "TaskCreate" and isinstance(inp, dict):
                                    tasks_created_here.append(
                                        {
                                            "subject": inp.get("subject"),
                                            "active_form": inp.get("activeForm"),
                                            "ts": ts,
                                        }
                                    )
                                elif name == "TaskUpdate" and isinstance(inp, dict):
                                    tid = str(inp.get("taskId") or "")
                                    status = inp.get("status")
                                    if status == "in_progress":
                                        # Look up subject by 1-based taskId index within THIS file.
                                        # If taskId is out-of-range it was created in a prior log file
                                        # we can't see — fall back to a synthetic subject (Issue #13).
                                        subject = None
                                        active_form = None
                                        try:
                                            idx = int(tid)
                                            if 1 <= idx <= len(tasks_created_here):
                                                t = tasks_created_here[idx - 1]
                                                subject = t["subject"]
                                                active_form = t["active_form"]
                                            else:
                                                subject = f"Task #{tid}"
                                        except (TypeError, ValueError):
                                            pass
                                        current_in_progress = {
                                            "id": tid,
                                            "subject": subject,
                                            "active_form": active_form,
                                        }
                                        current_in_progress_started_at = ts
                                    elif (
                                        status in ("completed", "deleted")
                                        and current_in_progress
                                        and current_in_progress.get("id") == tid
                                    ):
                                        current_in_progress = None
                                        current_in_progress_started_at = None
    except OSError as e:
        log.warning("Cannot read log %s: %s", path, e)
        return pl
    pl.tool_calls.breakdown = dict(sorted(breakdown.items(), key=lambda kv: -kv[1]))
    pl.tool_calls.last_used = last_tool_used
    pl.tool_calls.last_used_at = last_tool_used_at
    if current_in_progress:
        pl.current_task_id = current_in_progress.get("id")
        pl.current_task_subject = current_in_progress.get("subject")
        pl.current_task_active_form = current_in_progress.get("active_form")
        pl.current_task_started_at = current_in_progress_started_at
    pl.subagents = sorted(subagents_by_id.values(), key=lambda r: r.started_at)
    # Issue #7: a tool_use stop_reason left dangling from a user-halted session
    # would keep the UI showing "in-flight" forever. Clear it once the last
    # assistant entry is older than the recency window.
    if pl.is_in_flight and pl.last_assistant_at is not None:
        age = (now - pl.last_assistant_at).total_seconds()
        if age >= 60:
            pl.is_in_flight = False
    return pl


def parse_logs_for_cwd(cwd: str, log_dir: Path | None = None, max_files: int = 3) -> ParsedLog | None:
    """Return the freshest log for the cwd (highest mtime)."""
    logs = find_logs_for_cwd(cwd, log_dir)
    if not logs:
        return None
    return parse_log(logs[0])
