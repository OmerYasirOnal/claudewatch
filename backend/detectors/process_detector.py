from __future__ import annotations

import getpass
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psutil

CLAUDE_NAMES = {"claude"}
CLAUDE_CMD_MARKERS = ("claude-code", "@anthropic-ai/claude-code", "/claude")
DESKTOP_APP_MARKERS = ("/Applications/Claude.app/", "/Applications/Claude ")

PERMISSION_SKIP_FLAGS = {
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
}

VALUE_TAKING_FLAGS = {
    "--model",
    "--system-prompt-file",
    "--print",
    "--allowed-tools",
    "--disallowed-tools",
    "--allowedTools",
    "--disallowedTools",
    "-p",
    "--mcp-config",
    "--settings",
    "--add-dir",
    "--resume",
    "--session-id",
    "--agent",
    "--permission-mode",
    "--permission-prompt-tool",
    "--effort",
    "--output-format",
    "--input-format",
    "--plugin-dir",
}


def _info_or_call(proc: psutil.Process, key: str, caller):
    """Read pre-fetched proc.info[key] if available (cheap, populated by
    process_iter([...])), else fall back to caller() which performs a syscall.
    Lets is_claude_process skip ~2 syscalls per candidate during a scan (#50)."""
    info = getattr(proc, "info", None)
    if info is not None and key in info:
        return info[key]
    return caller()


def is_claude_process(proc: psutil.Process, current_user: str) -> bool:
    try:
        if _info_or_call(proc, "username", proc.username) != current_user:
            return False
        cmdline = _info_or_call(proc, "cmdline", proc.cmdline) or []
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return False
    if not cmdline:
        return False
    exe_path = cmdline[0] or ""
    # Exclude Claude desktop app FIRST — its name() is "Claude" which would otherwise pass.
    if any(m in exe_path for m in DESKTOP_APP_MARKERS):
        return False
    # Case-sensitive basename: CLI is `claude` (lowercase).
    first_cs = os.path.basename(exe_path)
    if first_cs == "claude":
        return True
    joined = " ".join(cmdline)
    if any(marker in joined for marker in CLAUDE_CMD_MARKERS):
        if "node" in first_cs.lower():
            return True
    return False


def parse_cmdline(cmdline: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model": None,
        "permission_mode_flag": None,
        "session_id": None,
        "extra_flags": [],
    }
    i = 1  # skip executable
    while i < len(cmdline):
        token = cmdline[i]
        if token in PERMISSION_SKIP_FLAGS:
            out["permission_mode_flag"] = "dangerously-skip"
            out["extra_flags"].append(token)
        elif token == "--model" and i + 1 < len(cmdline):
            out["model"] = cmdline[i + 1]
            out["extra_flags"].append(token)
            out["extra_flags"].append(cmdline[i + 1])
            i += 1
        elif token.startswith("--model="):
            out["model"] = token.split("=", 1)[1]
            out["extra_flags"].append(token)
        elif token == "--permission-mode" and i + 1 < len(cmdline):
            out["permission_mode_flag"] = cmdline[i + 1]
            out["extra_flags"].append(token)
            out["extra_flags"].append(cmdline[i + 1])
            i += 1
        elif token in ("--resume", "--session-id") and i + 1 < len(cmdline):
            out["session_id"] = cmdline[i + 1]
            out["extra_flags"].append(token)
            out["extra_flags"].append(cmdline[i + 1])
            i += 1
        elif token in VALUE_TAKING_FLAGS and i + 1 < len(cmdline):
            out["extra_flags"].append(token)
            out["extra_flags"].append(cmdline[i + 1])
            i += 1
        elif token.startswith("--"):
            out["extra_flags"].append(token)
        i += 1
    return out


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    cwd: str | None
    started_at: datetime
    cpu_percent: float
    memory_mb: float
    cmdline: list[str]
    cmdline_parsed: dict[str, Any]


def scan_claude_processes() -> list[ProcInfo]:
    user = getpass.getuser()
    out: list[ProcInfo] = []
    # process_iter pre-fetches these attrs in one batched syscall per process;
    # we re-read them via proc.info below instead of issuing fresh syscalls.
    # Saves ~2 syscalls per candidate and ~3 syscalls per real Claude proc per
    # scheduler tick on busy systems (#50).
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline", "username", "create_time"]):
        try:
            if not is_claude_process(proc, user):
                continue
            info = proc.info
            cmdline = info.get("cmdline") or []
            ppid = info.get("ppid")
            if ppid is None:
                ppid = proc.ppid()
            create_time = info.get("create_time")
            if create_time is None:
                create_time = proc.create_time()
            cwd = None
            try:
                cwd = proc.cwd()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            try:
                cpu = proc.cpu_percent(interval=None)
            except psutil.Error:
                cpu = 0.0
            try:
                mem = proc.memory_info().rss / (1024 * 1024)
            except psutil.Error:
                mem = 0.0
            out.append(
                ProcInfo(
                    pid=proc.pid,
                    ppid=ppid,
                    cwd=cwd,
                    started_at=datetime.fromtimestamp(create_time, tz=timezone.utc),
                    cpu_percent=float(cpu),
                    memory_mb=float(mem),
                    cmdline=cmdline,
                    cmdline_parsed=parse_cmdline(cmdline),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


@dataclass
class CpuHistory:
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=30))
    last_seen_ts: float = 0.0
    last_busy_ts: float = 0.0  # last time CPU was non-trivial


def infer_status(
    cpu_history: CpuHistory,
    last_log_activity_seconds_ago: float | None,
) -> str:
    if not cpu_history.samples:
        return "idle"
    recent_5 = list(cpu_history.samples)[-5:]
    recent_15 = list(cpu_history.samples)[-15:]
    if recent_5 and (sum(recent_5) / len(recent_5)) > 5.0:
        return "working"
    if recent_15 and (sum(recent_15) / len(recent_15)) < 1.0:
        if last_log_activity_seconds_ago is not None and last_log_activity_seconds_ago > 300:
            return "idle"
        return "waiting"
    return "waiting"
