from __future__ import annotations

import asyncio
import getpass
import logging
import os
import re
import shlex
import signal
import subprocess
from pathlib import Path

import psutil
from fastapi import APIRouter, HTTPException, Request

from backend.detectors.process_detector import VALUE_TAKING_FLAGS, is_claude_process
from backend.models import NewSessionRequest, SendTextRequest

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)

APPLESCRIPT_DIR = Path(__file__).resolve().parent.parent / "applescript"

# A flag is `--name` or `--name=value`. Names: lowercase + digits + dash, must start lowercase.
SAFE_FLAG_RE = re.compile(r"^--[a-z][a-z0-9-]*(=[A-Za-z0-9._/=:,@-]+)?$")
UNSAFE_VALUE_CHARS = (";", "&", "|", "`", "$", "\n", "\r", ">", "<", "\\", "\x00")

# Hard cap on /send-text payloads. The endpoint hands raw text to iTerm's
# `async_send_text`, which would happily forward megabytes — bound it well below
# anything a human would type so a stuck client can't flood the target session.
SEND_TEXT_MAX_CHARS = 4096


def _state(request: Request):
    return request.app.state.s


def _check_read_only(request: Request) -> None:
    if request.app.state.s.config.get("read_only"):
        raise HTTPException(403, "server is in read-only mode")


def sanitize_new_session(body: NewSessionRequest) -> tuple[str, list[str]]:
    cwd = Path(body.cwd).expanduser()
    try:
        cwd_resolved = cwd.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise HTTPException(400, f"cwd does not exist: {body.cwd}")
    if not cwd_resolved.is_dir():
        raise HTTPException(400, "cwd is not a directory")
    home = Path.home().resolve()
    if home not in cwd_resolved.parents and cwd_resolved != home:
        raise HTTPException(400, "cwd must be under the user's home directory")

    # Command path
    if body.command == "claude":
        cmd_str = "claude"
    else:
        try:
            cmd_path = Path(body.command).expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise HTTPException(400, "command not found")
        allowed_prefixes = [home / ".local" / "bin", home / "Library" / "Application Support" / "Claude"]
        if not any(cmd_path == p or p in cmd_path.parents for p in allowed_prefixes):
            raise HTTPException(400, "command must live under ~/.local/bin or Claude support dir")
        cmd_str = str(cmd_path)

    i = 0
    out_flags: list[str] = []
    while i < len(body.flags):
        f = body.flags[i]
        if not SAFE_FLAG_RE.match(f):
            raise HTTPException(400, f"unsafe flag: {f}")
        out_flags.append(f)
        # Value-taking flag → swallow next token as value
        flag_name = f.split("=", 1)[0]
        if flag_name in VALUE_TAKING_FLAGS and "=" not in f:
            if i + 1 >= len(body.flags):
                raise HTTPException(400, f"flag {f} requires value")
            v = body.flags[i + 1]
            if any(c in v for c in UNSAFE_VALUE_CHARS):
                raise HTTPException(400, f"unsafe flag value for {f}")
            out_flags.append(v)
            i += 2
        else:
            i += 1
    return str(cwd_resolved), [cmd_str, *out_flags]


@router.post("/sessions/new")
async def new_session(body: NewSessionRequest, request: Request):
    _check_read_only(request)
    cwd, argv = sanitize_new_session(body)
    cmd_str = shlex.join(argv)
    script_name = (
        "new_iterm_window.applescript" if body.window_type == "new-window" else "new_iterm_tab.applescript"
    )
    script_path = APPLESCRIPT_DIR / script_name
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["osascript", str(script_path), cwd, cmd_str],
            check=True,
            timeout=10,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        log.error("AppleScript failed: %s", e.stderr)
        raise HTTPException(500, f"AppleScript failed: {e.stderr.strip() or e}")
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "AppleScript timed out")
    return {"success": True, "cwd": cwd, "command": cmd_str}


@router.post("/sessions/{pid}/halt")
async def halt(pid: int, request: Request):
    _check_read_only(request)
    s = _state(request)
    if pid not in s.sessions:
        raise HTTPException(404, "session not found")
    # #33: re-verify the target is still a Claude process before SIGINT'ing it.
    # Between the scan and now the PID could have been reused by an unrelated
    # process belonging to the same user.
    try:
        proc = psutil.Process(pid)
        if not is_claude_process(proc, getpass.getuser()):
            raise HTTPException(409, "PID no longer refers to a claude session")
    except psutil.NoSuchProcess:
        raise HTTPException(404, "process not running")
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        raise HTTPException(404, "process not running")
    except PermissionError:
        raise HTTPException(403, "permission denied")
    # Wait up to 5s for the process to exit
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while loop.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return {"success": True, "exited": True}
        await asyncio.sleep(0.2)
    return {"success": True, "exited": False, "note": "SIGINT sent; process still running"}


@router.post("/sessions/{pid}/focus")
async def focus(pid: int, request: Request):
    _check_read_only(request)
    s = _state(request)
    sess = s.sessions.get(pid)
    if not sess:
        raise HTTPException(404, "session not found")
    if sess.location_type == "headless":
        raise HTTPException(400, "headless session has no window to focus")

    # Issue #12: if it's a tmux session with no iTerm linkage and no attached
    # tmux client, focusing would silently no-op. Detect and 409 instead.
    has_iterm_link = bool(sess.iterm_tty) or (
        sess.iterm_window_id is not None and sess.iterm_tab_id is not None
    )
    if sess.location_type == "tmux" and not has_iterm_link and sess.tmux_session is not None:
        try:
            r = await asyncio.to_thread(
                subprocess.run,
                ["tmux", "list-clients", "-t", sess.tmux_session],
                check=False,
                timeout=3,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(500, "focus action timed out")
        if not r.stdout.strip():
            raise HTTPException(409, "session is in detached tmux; no UI to focus")

    # #24: Prefer the persistent Python-API path — match on the iTerm session
    # UUID (always unique within a running iTerm) rather than the (window_id,
    # tab_id) tuple, which can race when tabs are moved or windows reordered.
    # If the API path can't find the session (e.g. iTerm restarted, or the
    # connection is in backoff), we fall through to the existing AppleScript
    # branches below.
    iterm_manager = getattr(request.app.state.s, "iterm_manager", None)
    api_focused = False
    if iterm_manager is not None and sess.iterm_session_id:
        try:
            api_focused = await iterm_manager.focus_session(sess.iterm_session_id)
        except Exception as e:  # noqa: BLE001
            log.debug("iterm_manager.focus_session raised: %s", e)
            api_focused = False
    try:
        if api_focused:
            pass  # Python-API path handled it; skip AppleScript.
        elif sess.iterm_tty:
            r = await asyncio.to_thread(
                subprocess.run,
                [
                    "osascript",
                    str(APPLESCRIPT_DIR / "focus_by_tty.applescript"),
                    sess.iterm_tty,
                ],
                check=True,
                timeout=5,
                capture_output=True,
                text=True,
            )
            if r.stdout.strip() == "not_found":
                raise HTTPException(404, f"iTerm session for {sess.iterm_tty} not found")
        if sess.location_type == "tmux" and sess.tmux_session is not None:
            await asyncio.to_thread(
                subprocess.run,
                ["tmux", "select-window", "-t", f"{sess.tmux_session}:{sess.tmux_window}"],
                check=True,
                timeout=3,
                capture_output=True,
                text=True,
            )
            await asyncio.to_thread(
                subprocess.run,
                [
                    "tmux",
                    "select-pane",
                    "-t",
                    f"{sess.tmux_session}:{sess.tmux_window}.{sess.tmux_pane}",
                ],
                check=True,
                timeout=3,
                capture_output=True,
                text=True,
            )
    except subprocess.CalledProcessError as e:
        log.error("focus failed: %s", e.stderr)
        raise HTTPException(500, f"focus failed: {e.stderr.strip() or e}")
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "focus action timed out")
    return {"success": True}


@router.post("/sessions/{pid}/send-text")
async def send_text(pid: int, body: SendTextRequest, request: Request):
    """Type ``body.text`` into the iTerm session backing this Claude PID.

    Opt-in: requires ``config.remote_control.enabled = True``. The dashboard's
    Settings page surfaces a toggle; until it's flipped we 403 so a typo in the
    URL bar can't push text into a running session.
    """
    _check_read_only(request)
    s = _state(request)
    remote_cfg = s.config.get("remote_control", {}) or {}
    if not remote_cfg.get("enabled", False):
        raise HTTPException(403, "remote control is disabled; enable in settings")
    sess = s.sessions.get(pid)
    if not sess:
        raise HTTPException(404, "session not found")
    if not sess.iterm_session_id:
        raise HTTPException(400, "session has no iTerm linkage")

    text = body.text
    if len(text) > SEND_TEXT_MAX_CHARS:
        raise HTTPException(413, f"text exceeds {SEND_TEXT_MAX_CHARS}-character limit")

    iterm_manager = getattr(s, "iterm_manager", None)
    if iterm_manager is None:
        raise HTTPException(503, "iTerm manager unavailable")

    # Build the payload up-front so the byte count we log + return reflects
    # exactly what was sent to iTerm (including the trailing newline).
    payload = text + ("\n" if body.submit else "")
    log.info(
        "remote send-text to PID %d session %s: %d chars",
        pid,
        sess.iterm_session_id,
        len(payload),
    )
    try:
        ok = await iterm_manager.send_text(sess.iterm_session_id, payload)
    except Exception as e:  # noqa: BLE001
        log.error("send_text raised: %s", e)
        raise HTTPException(500, f"send_text failed: {e}")
    if not ok:
        raise HTTPException(502, "iTerm did not accept the text (session not found or API error)")
    return {"success": True, "bytes_sent": len(payload)}
