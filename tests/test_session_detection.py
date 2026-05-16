"""Tests that don't require live psutil — they exercise is_claude_process with
mock proc-like objects so we cover the exact code paths that decide whether a
process is a Claude CLI session."""

from __future__ import annotations

import getpass
from unittest.mock import MagicMock

import psutil

from backend.detectors.process_detector import is_claude_process

USER = getpass.getuser()


def _mk_proc(cmdline: list[str], username: str = USER) -> MagicMock:
    p = MagicMock(spec=psutil.Process)
    p.username.return_value = username
    p.cmdline.return_value = cmdline
    p.name.return_value = cmdline[0].split("/")[-1] if cmdline else ""
    return p


def test_accept_user_cli_path():
    p = _mk_proc(["/Users/me/.local/bin/claude"])
    assert is_claude_process(p, USER) is True


def test_accept_anthropic_managed_path():
    p = _mk_proc(
        ["/Users/me/Library/Application Support/Claude/claude-code/2.1.128/claude.app/Contents/MacOS/claude"]
    )
    assert is_claude_process(p, USER) is True


def test_accept_with_args():
    p = _mk_proc(["/Users/me/.local/bin/claude", "--model", "claude-opus-4-7"])
    assert is_claude_process(p, USER) is True


def test_reject_claude_desktop_app():
    p = _mk_proc(["/Applications/Claude.app/Contents/MacOS/Claude"])
    assert is_claude_process(p, USER) is False


def test_reject_desktop_app_helper_inside_apps_dir():
    p = _mk_proc(["/Applications/Claude.app/Contents/Frameworks/Helper.app/Helper"])
    assert is_claude_process(p, USER) is False


def test_reject_different_user():
    p = _mk_proc(["/Users/me/.local/bin/claude"], username="someone-else")
    assert is_claude_process(p, USER) is False


def test_reject_python_named_claude():
    """A python script named claude.py should not match (cmdline[0] is python)."""
    p = _mk_proc(["/opt/homebrew/bin/python", "/Users/me/claude.py"])
    assert is_claude_process(p, USER) is False


def test_reject_shell_script_claude():
    p = _mk_proc(["/bin/bash", "/Users/me/.local/bin/claude-wrapper"])
    assert is_claude_process(p, USER) is False


def test_reject_empty_cmdline():
    p = _mk_proc([])
    assert is_claude_process(p, USER) is False


def test_reject_unrelated_process():
    p = _mk_proc(["/Applications/Chrome.app/Contents/MacOS/Chrome"])
    assert is_claude_process(p, USER) is False


def test_handles_access_denied_username():
    p = MagicMock(spec=psutil.Process)
    p.username.side_effect = psutil.AccessDenied()
    assert is_claude_process(p, USER) is False


def test_handles_access_denied_cmdline():
    p = MagicMock(spec=psutil.Process)
    p.username.return_value = USER
    p.cmdline.side_effect = psutil.AccessDenied()
    assert is_claude_process(p, USER) is False


def test_handles_no_such_process_during_check():
    p = MagicMock(spec=psutil.Process)
    p.username.return_value = USER
    p.cmdline.side_effect = psutil.NoSuchProcess(pid=1)
    assert is_claude_process(p, USER) is False


def test_live_detection_finds_at_least_self_when_running():
    """Smoke check: if there's a real claude running, the detector returns at least one."""
    # Not asserting non-empty because in CI there is no Claude — just don't crash.
    from backend.detectors.process_detector import scan_claude_processes

    out = scan_claude_processes()
    assert isinstance(out, list)
    for p in out:
        assert p.pid > 0
        assert isinstance(p.cmdline, list)


def test_is_claude_process_uses_info_cache():
    """#50: when proc.info is pre-populated (the process_iter path), is_claude_process
    must read from it rather than making fresh username()/cmdline() syscalls.
    Asserts no calls were made on the underlying methods."""
    p = MagicMock(spec=psutil.Process)
    p.info = {"username": USER, "cmdline": ["/Users/me/.local/bin/claude"]}
    # Don't pre-set return_value — any call should be a regression.
    assert is_claude_process(p, USER) is True
    assert p.username.call_count == 0
    assert p.cmdline.call_count == 0


def test_is_claude_process_falls_back_when_info_missing():
    """When proc.info wasn't pre-populated (callers using is_claude_process directly
    on a bare psutil.Process), we still need the live syscall fallback."""
    p = MagicMock(spec=psutil.Process)
    # MagicMock(spec=...) doesn't auto-create `.info`; emulate the bare-Process case.
    p.info = {}
    p.username.return_value = USER
    p.cmdline.return_value = ["/Users/me/.local/bin/claude"]
    assert is_claude_process(p, USER) is True
    assert p.username.call_count == 1
    assert p.cmdline.call_count == 1
