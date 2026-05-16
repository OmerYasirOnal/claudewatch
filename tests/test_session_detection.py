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
