"""Derive a chronological timeline of significant events for a single session.

The dashboard renders these as a forensic log of the session's lifecycle:
start, model switches, first tool call, coalesced tool bursts, subagent
dispatch + completion, thinking activation, permission prompts, errors and
end.

The function reads the raw JSONL once, not via ``parse_log()`` — ``parse_log``
aggregates and discards per-entry ordering, which the timeline needs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.detectors.conversation_log import (
    _ASYNC_ACK_RE,
    _TASK_ID_RE,
    _TASK_NOTIF_OPEN,
    _TASK_SUMMARY_RE,
    _iter_task_notification_bodies,
    _parse_ts,
)
from backend.models import Timeline, TimelineEvent

log = logging.getLogger(__name__)

# Hard cap so a pathological multi-million-entry log can't OOM the response.
MAX_EVENTS = 200

# Window inside which back-to-back same-name tool_use blocks coalesce into one
# "12 Read calls" event. Tunable; 5s matches the spec.
TOOL_COALESCE_WINDOW_SECONDS = 5.0


def _emit(
    events: list[TimelineEvent],
    ts: datetime,
    etype: str,
    description: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Append an event; return True iff we hit the cap."""
    if len(events) >= MAX_EVENTS:
        return True
    events.append(
        TimelineEvent(
            timestamp=ts,
            type=etype,  # type: ignore[arg-type]
            description=description,
            metadata=metadata or {},
        )
    )
    return len(events) >= MAX_EVENTS


def derive_timeline(log_path: Path, pid: int) -> Timeline:
    """Return a ``Timeline`` derived from the conversation log at ``log_path``.

    Missing or empty files yield an empty timeline (no error) so the
    endpoint can call this unconditionally and the frontend can render an
    "No events yet" placeholder for fresh sessions.
    """
    events: list[TimelineEvent] = []
    truncated = False

    if not log_path.is_file():
        return Timeline(pid=pid, events=events, truncated=False)

    # Coalescing state for tool_use bursts ----------------------------------
    # When N consecutive tool_use blocks share the same name AND each is
    # within TOOL_COALESCE_WINDOW_SECONDS of the prior, we keep only one
    # event whose description summarizes the count. Flushed on:
    #   - a different tool name
    #   - a gap > window
    #   - any non-tool event we need to emit before this one
    #   - end of file
    coalesce: dict[str, Any] | None = None
    seen_first_tool = False
    last_model: str | None = None
    last_ts: datetime | None = None
    # tool_use_id -> dict of bookkeeping for subagent dispatch (description,
    # subagent_type, started_at, agent_id, completed).
    subagents: dict[str, dict[str, Any]] = {}
    permission_mode_seen: str | None = None
    has_any_entry = False

    def flush_coalesce() -> bool:
        """Emit pending coalesced tool event if any; return True if cap hit."""
        nonlocal coalesce
        if coalesce is None:
            return False
        name = coalesce["name"]
        count = coalesce["count"]
        ts = coalesce["last_ts"]
        if count == 1:
            desc = f"{name} tool call"
        else:
            desc = f"{count} {name} calls"
        meta = {"tool": name, "count": count}
        coalesce = None
        return _emit(events, ts, "tool_call", desc, meta)

    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(entry.get("timestamp"))
                if ts is None:
                    # Skip entries with un-parseable timestamps; timeline is
                    # strictly time-ordered so we can't position them.
                    continue
                has_any_entry = True
                last_ts = ts
                etype = entry.get("type")

                # "started" — first entry of any kind.
                if not events and coalesce is None:
                    desc_started = "Session started"
                    md_started: dict[str, Any] = {}
                    if isinstance(entry.get("cwd"), str):
                        md_started["cwd"] = entry["cwd"]
                    if isinstance(entry.get("version"), str):
                        md_started["cli_version"] = entry["version"]
                    truncated = truncated or _emit(events, ts, "started", desc_started, md_started)
                    if truncated:
                        break

                # permission-mode events -> "permission_prompt"
                if etype == "permission-mode":
                    pm = entry.get("permissionMode")
                    if isinstance(pm, str) and pm and pm != permission_mode_seen:
                        # Flush any pending tool burst — order matters.
                        if flush_coalesce():
                            truncated = True
                            break
                        permission_mode_seen = pm
                        truncated = truncated or _emit(
                            events,
                            ts,
                            "permission_prompt",
                            f"Permission mode changed to {pm}",
                            {"mode": pm},
                        )
                        if truncated:
                            break
                    continue

                if etype == "assistant":
                    msg = entry.get("message") or {}
                    model = msg.get("model")
                    if isinstance(model, str) and model:
                        if last_model is None:
                            # First assistant entry — record the initial model
                            # as part of "started" metadata, but DON'T emit a
                            # model_switch (there was nothing to switch from).
                            last_model = model
                            if events and events[0].type == "started":
                                events[0].metadata.setdefault("model", model)
                        elif model != last_model:
                            if flush_coalesce():
                                truncated = True
                                break
                            prev = last_model
                            last_model = model
                            truncated = truncated or _emit(
                                events,
                                ts,
                                "model_switch",
                                f"Model switched: {prev} -> {model}",
                                {"from": prev, "to": model},
                            )
                            if truncated:
                                break

                    content = msg.get("content") or []
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type")
                            if btype == "thinking":
                                if flush_coalesce():
                                    truncated = True
                                    break
                                # Only emit the FIRST occurrence to avoid
                                # one-per-turn spam; the description tracks
                                # the start of extended thinking.
                                already = any(e.type == "thinking_started" for e in events)
                                if not already:
                                    truncated = truncated or _emit(
                                        events,
                                        ts,
                                        "thinking_started",
                                        "Extended thinking activated",
                                        {},
                                    )
                                    if truncated:
                                        break
                            elif btype == "tool_use":
                                name = block.get("name") or "unknown"
                                tool_id = block.get("id") if isinstance(block.get("id"), str) else None
                                is_agent = name == "Agent"

                                # The very first tool call gets its own
                                # "first_tool" event, then the regular
                                # tool_call event still fires (so the burst
                                # coalescing continues to work).
                                if not seen_first_tool:
                                    if flush_coalesce():
                                        truncated = True
                                        break
                                    seen_first_tool = True
                                    truncated = truncated or _emit(
                                        events,
                                        ts,
                                        "first_tool",
                                        f"First tool call ({name})",
                                        {"tool": name},
                                    )
                                    if truncated:
                                        break

                                if is_agent:
                                    # Subagent dispatch is its own event, so
                                    # flush any current burst first.
                                    if flush_coalesce():
                                        truncated = True
                                        break
                                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                                    desc = ""
                                    sa_type = "general-purpose"
                                    if isinstance(inp, dict):
                                        desc = str(inp.get("description") or "")
                                        sa_type = str(inp.get("subagent_type") or "general-purpose")
                                    if tool_id:
                                        subagents[tool_id] = {
                                            "description": desc,
                                            "subagent_type": sa_type,
                                            "started_at": ts,
                                            "agent_id": None,
                                            "completed": False,
                                        }
                                    label = desc or sa_type
                                    truncated = truncated or _emit(
                                        events,
                                        ts,
                                        "subagent_started",
                                        f"Subagent dispatched: {label}",
                                        {
                                            "subagent_type": sa_type,
                                            "description": desc,
                                            "tool_use_id": tool_id,
                                        },
                                    )
                                    if truncated:
                                        break
                                    continue

                                # Regular tool_use — coalescing path.
                                can_merge = False
                                if coalesce is not None and coalesce["name"] == name:
                                    delta = (ts - coalesce["last_ts"]).total_seconds()
                                    if 0 <= delta <= TOOL_COALESCE_WINDOW_SECONDS:
                                        can_merge = True
                                if can_merge:
                                    coalesce["count"] += 1
                                    coalesce["last_ts"] = ts
                                else:
                                    if flush_coalesce():
                                        truncated = True
                                        break
                                    coalesce = {
                                        "name": name,
                                        "count": 1,
                                        "first_ts": ts,
                                        "last_ts": ts,
                                    }
                        if truncated:
                            break

                    # is_error on the assistant message itself
                    if msg.get("is_error") is True or entry.get("isError") is True:
                        if flush_coalesce():
                            truncated = True
                            break
                        err_text = msg.get("error") or "Assistant returned an error"
                        truncated = truncated or _emit(
                            events,
                            ts,
                            "error",
                            str(err_text)[:200],
                            {},
                        )
                        if truncated:
                            break

                elif etype == "user":
                    msg = entry.get("message") or {}
                    content = msg.get("content") or []
                    if isinstance(content, list):
                        notification_parts: list[str] = []
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type")
                            if btype == "text":
                                t = block.get("text")
                                if isinstance(t, str):
                                    notification_parts.append(t)
                                continue
                            if btype != "tool_result":
                                continue
                            tu_id = block.get("tool_use_id")
                            # Errors surfaced inside tool_results
                            if block.get("is_error") is True:
                                if flush_coalesce():
                                    truncated = True
                                    break
                                # Try to pluck a one-line description.
                                raw = block.get("content")
                                err_desc = "Tool error"
                                if isinstance(raw, str):
                                    err_desc = raw.strip().split("\n")[0][:200] or err_desc
                                elif isinstance(raw, list):
                                    for blk in raw:
                                        if isinstance(blk, dict) and isinstance(blk.get("text"), str):
                                            err_desc = blk["text"].strip().split("\n")[0][:200] or err_desc
                                            break
                                truncated = truncated or _emit(
                                    events,
                                    ts,
                                    "error",
                                    err_desc,
                                    {"tool_use_id": tu_id if isinstance(tu_id, str) else None},
                                )
                                if truncated:
                                    break
                            # Subagent completion via foreground tool_result
                            if isinstance(tu_id, str) and tu_id in subagents:
                                run = subagents[tu_id]
                                if run["completed"]:
                                    continue
                                # Check for async-launch ACK — that's NOT a
                                # completion; remember the agentId for later.
                                raw = block.get("content")
                                text_for_ack: str | None = None
                                if isinstance(raw, str):
                                    text_for_ack = raw
                                elif isinstance(raw, list):
                                    for blk in raw:
                                        if isinstance(blk, dict) and isinstance(blk.get("text"), str):
                                            text_for_ack = blk["text"]
                                            break
                                ack = _ASYNC_ACK_RE.search(text_for_ack or "")
                                if ack:
                                    run["agent_id"] = ack.group(1)
                                    continue
                                # Synchronous completion.
                                if flush_coalesce():
                                    truncated = True
                                    break
                                duration = max(0, int((ts - run["started_at"]).total_seconds()))
                                label = run["description"] or run["subagent_type"]
                                truncated = truncated or _emit(
                                    events,
                                    ts,
                                    "subagent_finished",
                                    f"Subagent finished: {label}",
                                    {
                                        "subagent_type": run["subagent_type"],
                                        "description": run["description"],
                                        "tool_use_id": tu_id,
                                        "duration_seconds": duration,
                                    },
                                )
                                if truncated:
                                    break
                                run["completed"] = True
                        if truncated:
                            break

                        # Background subagent completion via task-notification
                        if notification_parts:
                            joined = "\n".join(notification_parts)
                            if _TASK_NOTIF_OPEN in joined and "<task-id>" in joined:
                                for body in _iter_task_notification_bodies(joined):
                                    id_match = _TASK_ID_RE.search(body)
                                    if id_match is None:
                                        continue
                                    agent_id = id_match.group(1)
                                    # Find the most recent pending subagent
                                    # whose stored agentId matches.
                                    matched_id: str | None = None
                                    for tu_id, run in reversed(list(subagents.items())):
                                        if run["completed"]:
                                            continue
                                        if run.get("agent_id") == agent_id:
                                            matched_id = tu_id
                                            break
                                    if matched_id is None:
                                        continue
                                    run = subagents[matched_id]
                                    if flush_coalesce():
                                        truncated = True
                                        break
                                    duration = max(0, int((ts - run["started_at"]).total_seconds()))
                                    summary_match = _TASK_SUMMARY_RE.search(body)
                                    summary = summary_match.group(1).strip() if summary_match else None
                                    label = run["description"] or summary or run["subagent_type"]
                                    truncated = truncated or _emit(
                                        events,
                                        ts,
                                        "subagent_finished",
                                        f"Subagent finished: {label}",
                                        {
                                            "subagent_type": run["subagent_type"],
                                            "description": run["description"],
                                            "tool_use_id": matched_id,
                                            "duration_seconds": duration,
                                            "background": True,
                                        },
                                    )
                                    if truncated:
                                        break
                                    run["completed"] = True
                                if truncated:
                                    break

    except OSError as e:
        log.warning("Cannot read log %s: %s", log_path, e)
        return Timeline(pid=pid, events=events, truncated=truncated)

    # Flush trailing tool burst (if any).
    if not truncated and flush_coalesce():
        truncated = True

    # Synthetic "ended" event: only when we have at least one real event AND
    # the log isn't truncated. We surface the last activity timestamp so the
    # UI can render "session last active at X".
    # (Only meaningful when the session is presumed over — for live sessions
    # this still gives a "last activity" marker that updates as the timeline
    # is re-fetched.)
    if has_any_entry and last_ts is not None and events and not truncated:
        # Only append "ended" if it isn't already the last event AND would
        # not push us over the cap.
        if events[-1].type != "ended" and len(events) < MAX_EVENTS:
            events.append(
                TimelineEvent(
                    timestamp=last_ts,
                    type="ended",
                    description="Last recorded activity",
                    metadata={},
                )
            )

    # Defensive re-sort. Source log is normally in order but JSONL is
    # append-only — be robust.
    events.sort(key=lambda e: e.timestamp)
    return Timeline(pid=pid, events=events, truncated=truncated)
