from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.detectors.conversation_log import (
    ParsedLog,
    find_log_dir,
    find_logs_for_cwd,
    parse_log,
)
from backend.detectors.filesystem_watch import FilesystemWatcher
from backend.detectors.git_context import get_git_context
from backend.detectors.iterm_applescript import ItermTtyLocation
from backend.detectors.iterm_detector import ItermLocation
from backend.detectors.process_detector import (
    CpuHistory,
    ProcInfo,
    infer_status,
    scan_claude_processes,
)
from backend.detectors.tmux_detector import link_pids_to_tmux
from backend.models import (
    ClaudeSession,
    FileChange,
    GitContext,
    TokenUsage,
    ToolCallStats,
)
from backend.pricing import annotate_usage


def _context_max_for_model(model: str | None) -> int | None:
    if not model:
        return None
    # The `[1m]` suffix is Claude Code's marker for the 1M-context variant.
    if "[1m]" in model:
        return 1_000_000
    if model.startswith("claude-opus-4") or model.startswith("claude-sonnet-4"):
        return 200_000
    if model.startswith("claude-haiku-4"):
        return 200_000
    return None


log = logging.getLogger(__name__)


@dataclass
class LinkerState:
    cpu_history: dict[int, CpuHistory] = field(default_factory=dict)
    log_cache: dict[Path, tuple[float, ParsedLog]] = field(default_factory=dict)
    git_cache: dict[str, tuple[float, GitContext | None]] = field(default_factory=dict)
    log_dir: Path | None = None
    iterm_available: bool = True


def _load_log_cached(state: LinkerState, path: Path) -> ParsedLog:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return parse_log(path)
    cached = state.log_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    pl = parse_log(path)
    state.log_cache[path] = (mtime, pl)
    return pl


def _load_git_cached(state: LinkerState, cwd: str, ttl: float = 10.0) -> GitContext | None:
    now = time.time()
    cached = state.git_cache.get(cwd)
    if cached and (now - cached[0]) < ttl:
        return cached[1]
    gc = get_git_context(cwd)
    state.git_cache[cwd] = (now, gc)
    return gc


def _update_cpu_history(state: LinkerState, pid: int, cpu: float) -> CpuHistory:
    h = state.cpu_history.setdefault(pid, CpuHistory())
    h.samples.append(cpu)
    h.last_seen_ts = time.time()
    if cpu > 1.0:
        h.last_busy_ts = h.last_seen_ts
    return h


def _prune_caches(
    state: LinkerState,
    alive_pids: set[int],
    alive_cwds: set[str],
) -> None:
    """Drop per-pid / per-cwd cache entries for processes that are gone.

    Without this, a long-running daemon accumulates one cpu_history dict entry
    per distinct pid ever observed, one git_cache entry per distinct cwd ever
    observed, and one log_cache entry per distinct conversation log ever read
    (see #48).
    """
    for pid in list(state.cpu_history.keys()):
        if pid not in alive_pids:
            state.cpu_history.pop(pid, None)
    for cwd in list(state.git_cache.keys()):
        if cwd not in alive_cwds:
            state.git_cache.pop(cwd, None)
    # log_cache keys are arbitrary Paths under ~/.claude/projects/<dashed>/.
    # We can't cheaply map them back to a cwd (path-mangling rules differ by
    # Claude Code version), so we evict by recency instead: drop any entry whose
    # underlying file hasn't been touched in 10 minutes, or whose file is gone.
    cutoff = time.time() - 600
    for path in list(state.log_cache.keys()):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            state.log_cache.pop(path, None)
            continue
        if mtime < cutoff:
            state.log_cache.pop(path, None)


async def build_sessions(
    config: dict[str, Any],
    state: LinkerState,
    watcher: FilesystemWatcher | None = None,
    iterm_loc_map: dict[int, ItermLocation] | None = None,
    iterm_tty_map: dict[int, ItermTtyLocation] | None = None,
) -> list[ClaudeSession]:
    """Build the current session snapshot.

    iTerm lookups are NOT performed here — they're driven on a separate
    cadence by the iTerm scheduler in `server.py` and passed in via the
    `iterm_loc_map` / `iterm_tty_map` parameters.
    """
    # Re-resolve log_dir whenever it's not a valid dir — covers the case where
    # Claude Code wasn't installed when the daemon started but has since been
    # installed, or where the user wiped ~/.claude (#49). Cheap: 3 is_dir calls.
    if state.log_dir is None or not state.log_dir.is_dir():
        state.log_dir = find_log_dir()

    procs: list[ProcInfo] = await asyncio.to_thread(scan_claude_processes)
    pids = [p.pid for p in procs]
    pid_set = set(pids)
    alive_cwds = {p.cwd for p in procs if p.cwd}
    _prune_caches(state, pid_set, alive_cwds)

    tmux_loc_map = await asyncio.to_thread(link_pids_to_tmux, pids)
    iterm_loc_map = iterm_loc_map or {}
    iterm_tty_map = iterm_tty_map or {}

    pricing_cfg = config.get("pricing", {})
    file_retention = int(config.get("file_change_retention_minutes", 10))

    sessions: list[ClaudeSession] = []
    now = datetime.now(timezone.utc)

    for p in procs:
        cpu_history = _update_cpu_history(state, p.pid, p.cpu_percent)
        cwd = p.cwd or ""

        # Conversation log — prefer cmdline session_id if present
        parsed: ParsedLog | None = None
        session_id = p.cmdline_parsed.get("session_id")
        if cwd:
            logs = find_logs_for_cwd(cwd, state.log_dir)
            chosen = None
            if session_id and logs:
                for lp in logs:
                    if lp.stem == session_id:
                        chosen = lp
                        break
            if chosen is None and logs:
                chosen = logs[0]
            if chosen is not None:
                parsed = _load_log_cached(state, chosen)

        last_activity_at = now
        if parsed and parsed.last_activity_at:
            last_activity_at = parsed.last_activity_at
        last_log_age_seconds: float | None = None
        if parsed and parsed.last_activity_at:
            last_log_age_seconds = (now - parsed.last_activity_at).total_seconds()

        status = infer_status(cpu_history, last_log_age_seconds)

        # Location
        location_type: str = "headless"
        iterm_window_id = iterm_tab_id = iterm_tab_index = None
        iterm_session_id = iterm_tab_title = iterm_tty = None
        tmux_session = tmux_window = tmux_pane = None

        def _apply_iterm_python(iloc: Any) -> None:
            nonlocal iterm_window_id, iterm_tab_id, iterm_session_id, iterm_tab_title
            iterm_window_id, iterm_tab_id = iloc.window_id, iloc.tab_id
            iterm_session_id, iterm_tab_title = iloc.session_id, iloc.tab_title

        def _apply_iterm_tty(iloc: Any) -> None:
            nonlocal iterm_window_id, iterm_tab_index, iterm_session_id, iterm_tab_title, iterm_tty
            # ItermTtyLocation.window_id is str now (#36) — matches SessionStatus
            # field type, so no bridging needed.
            iterm_window_id = iloc.window_id
            iterm_tab_index = iloc.tab_index
            iterm_session_id = iloc.unique_id
            iterm_tab_title = iloc.name
            iterm_tty = iloc.tty

        if p.pid in tmux_loc_map:
            location_type = "tmux"
            loc = tmux_loc_map[p.pid]
            tmux_session, tmux_window, tmux_pane = loc.session, loc.window, loc.pane
            if p.pid in iterm_loc_map:
                _apply_iterm_python(iterm_loc_map[p.pid])
            elif p.pid in iterm_tty_map:
                _apply_iterm_tty(iterm_tty_map[p.pid])
        elif p.pid in iterm_loc_map:
            location_type = "iterm"
            _apply_iterm_python(iterm_loc_map[p.pid])
        elif p.pid in iterm_tty_map:
            location_type = "iterm"
            _apply_iterm_tty(iterm_tty_map[p.pid])

        # Usage + cost
        usage: TokenUsage | None = None
        if parsed:
            usage = parsed.usage
            annotate_usage(parsed.model, usage, pricing_cfg)

        # Permission mode: prefer log, then cmdline
        permission_mode = (parsed.permission_mode if parsed else None) or p.cmdline_parsed.get(
            "permission_mode_flag"
        )

        # Model: prefer cmdline (more current), else log
        model = p.cmdline_parsed.get("model") or (parsed.model if parsed else None)

        # Files
        recent_files: list[FileChange] = []
        if watcher and cwd:
            recent_files = watcher.get_recent(cwd, file_retention)

        # Git
        git_ctx: GitContext | None = None
        if cwd:
            git_ctx = _load_git_cached(state, cwd)

        duration = max(0, int((now - p.started_at).total_seconds()))

        # Context window utilization (from the latest assistant turn)
        context_tokens = None
        context_max = None
        context_pct = None
        last_turn_tokens = None
        last_turn_at = None
        last_stop_reason = None
        is_in_flight = False
        current_task_subject = None
        current_task_active_form = None
        current_task_id = None
        current_task_started_at = None
        current_task_elapsed = None
        if parsed:
            lt = parsed.last_assistant_usage
            context_tokens = lt.input_tokens + lt.cache_read_input_tokens + lt.cache_creation_input_tokens
            context_max = _context_max_for_model(model)
            # Heuristic: if the session has already exceeded the standard 200k cap, it must be running
            # the 1M-context variant (Claude Code would otherwise have errored out). Upgrade the cap.
            if context_max and context_max < 1_000_000 and context_tokens > context_max:
                context_max = 1_000_000
            if context_tokens > 0 and context_max:
                context_pct = round(min(1.0, context_tokens / context_max), 4)
            last_turn_tokens = lt.output_tokens
            last_turn_at = parsed.last_assistant_at
            last_stop_reason = parsed.last_stop_reason
            is_in_flight = parsed.is_in_flight
            current_task_subject = parsed.current_task_subject
            current_task_active_form = parsed.current_task_active_form
            current_task_id = parsed.current_task_id
            current_task_started_at = parsed.current_task_started_at
            if current_task_started_at:
                current_task_elapsed = max(0, int((now - current_task_started_at).total_seconds()))

        sessions.append(
            ClaudeSession(
                pid=p.pid,
                cwd=cwd,
                started_at=p.started_at,
                duration_seconds=duration,
                cpu_percent=p.cpu_percent,
                memory_mb=p.memory_mb,
                status=status,  # type: ignore[arg-type]
                location_type=location_type,  # type: ignore[arg-type]
                iterm_window_id=iterm_window_id,
                iterm_tab_id=iterm_tab_id,
                iterm_tab_index=iterm_tab_index,
                iterm_session_id=iterm_session_id,
                iterm_tab_title=iterm_tab_title,
                iterm_tty=iterm_tty,
                tmux_session=tmux_session,
                tmux_window=tmux_window,
                tmux_pane=tmux_pane,
                last_activity_at=last_activity_at,
                model=model,
                cli_version=parsed.cli_version if parsed else None,
                conversation_id=parsed.conversation_id if parsed else None,
                conversation_log_path=str(parsed.log_path) if parsed else None,
                message_count=parsed.message_count if parsed else 0,
                usage=usage,
                thinking_enabled=parsed.thinking_enabled if parsed else None,
                permission_mode=permission_mode,
                extra_flags=p.cmdline_parsed.get("extra_flags", []),
                tool_calls=parsed.tool_calls if parsed else ToolCallStats(),
                recent_file_changes=recent_files[-20:],  # cap UI list
                git=git_ctx,
                is_in_flight=is_in_flight,
                last_turn_tokens=last_turn_tokens,
                last_turn_at=last_turn_at,
                last_stop_reason=last_stop_reason,
                context_tokens=context_tokens,
                context_max_tokens=context_max,
                context_pct=context_pct,
                current_task_subject=current_task_subject,
                current_task_active_form=current_task_active_form,
                current_task_id=current_task_id,
                current_task_started_at=current_task_started_at,
                current_task_elapsed_seconds=current_task_elapsed,
                subagents=parsed.subagents if parsed else [],
            )
        )
    return sessions
