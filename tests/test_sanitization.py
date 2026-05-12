from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api.actions import sanitize_new_session
from backend.models import NewSessionRequest

HOME = str(Path.home())


def test_accept_safe_flags():
    body = NewSessionRequest(cwd=HOME, flags=["--dangerously-skip-permissions"])
    cwd, argv = sanitize_new_session(body)
    assert argv == ["claude", "--dangerously-skip-permissions"]


def test_reject_shell_injection_value():
    body = NewSessionRequest(cwd=HOME, flags=["--model", "; rm -rf /"])
    with pytest.raises(HTTPException) as ei:
        sanitize_new_session(body)
    assert ei.value.status_code == 400


def test_reject_flag_with_metacharacter():
    body = NewSessionRequest(cwd=HOME, flags=["--evil;rm"])
    with pytest.raises(HTTPException):
        sanitize_new_session(body)


def test_reject_flag_with_backticks_in_value():
    body = NewSessionRequest(cwd=HOME, flags=["--model", "x`whoami`"])
    with pytest.raises(HTTPException):
        sanitize_new_session(body)


def test_reject_nonexistent_cwd():
    body = NewSessionRequest(cwd="/does/not/exist/abc/xyz", flags=[])
    with pytest.raises(HTTPException) as ei:
        sanitize_new_session(body)
    assert ei.value.status_code == 400


def test_reject_cwd_outside_home(tmp_path, monkeypatch):
    # tmp_path on macOS is /private/var/folders/... which is outside $HOME
    body = NewSessionRequest(cwd=str(tmp_path), flags=[])
    with pytest.raises(HTTPException) as ei:
        sanitize_new_session(body)
    assert ei.value.status_code == 400


def test_accept_model_flag_with_value():
    body = NewSessionRequest(cwd=HOME, flags=["--model", "claude-opus-4-7"])
    _, argv = sanitize_new_session(body)
    assert argv[1:] == ["--model", "claude-opus-4-7"]


def test_accept_equals_form():
    body = NewSessionRequest(cwd=HOME, flags=["--model=claude-opus-4-7"])
    _, argv = sanitize_new_session(body)
    assert argv[1:] == ["--model=claude-opus-4-7"]


def test_reject_value_flag_without_value():
    body = NewSessionRequest(cwd=HOME, flags=["--model"])
    with pytest.raises(HTTPException) as ei:
        sanitize_new_session(body)
    assert ei.value.status_code == 400
