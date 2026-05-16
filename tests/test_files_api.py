"""Tests for backend.api.files: cross-session changes, git diff, open-in-editor.

We mount only the ``files`` router on a bare FastAPI app and attach a
hand-built ``AppState`` so we don't pay for the full lifespan. The home
directory is monkey-patched to a tmp_path-rooted fake so the path-safety
checks in ``_resolve_safe_paths`` accept our fixture directories.
"""

from __future__ import annotations

import subprocess
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import files as files_api
from backend.models import ClaudeSession, FileChange
from backend.server import AppState


class _FakeWatcher:
    """Minimal stand-in for FilesystemWatcher exposing only what files.py uses.

    The real watcher does inotify and would require running event loops; for
    these tests we just need ``.changes`` (the per-cwd deque map) and
    ``.get_recent`` (returns FileChange entries newer than ``minutes``).
    """

    def __init__(self) -> None:
        self.changes: dict[str, deque[FileChange]] = {}

    def get_recent(self, cwd: str, minutes: int) -> list[FileChange]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return [c for c in self.changes.get(cwd, deque()) if c.ts >= cutoff]


def _make_session(pid: int, cwd: str) -> ClaudeSession:
    now = datetime.now(timezone.utc)
    return ClaudeSession(pid=pid, cwd=cwd, started_at=now, last_activity_at=now)


@pytest.fixture
def app_with_files(tmp_path, monkeypatch):
    """FastAPI app with the files router mounted + a Path.home() pointing at
    ``tmp_path / 'home'`` so the safety checks accept our fixtures."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("backend.api.files.Path.home", lambda: fake_home)

    app = FastAPI()
    app.include_router(files_api.router)
    state = AppState(config={"editor": {"enabled": False, "command": "code"}})
    app.state.s = state
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app, state, fake_home


# ---------------------------------------------------------------------------
# /api/file-changes
# ---------------------------------------------------------------------------


def test_all_file_changes_dedups_and_sorts(app_with_files):
    client, _, state, home = app_with_files
    cwd_a = str(home / "a")
    cwd_b = str(home / "b")
    Path(cwd_a).mkdir()
    Path(cwd_b).mkdir()

    state.sessions = {
        1: _make_session(1, cwd_a),
        2: _make_session(2, cwd_b),
    }

    watcher = _FakeWatcher()
    now = datetime.now(timezone.utc)
    # Same (cwd_a, "foo.py") twice — newer ts must win.
    watcher.changes[cwd_a] = deque(
        [
            FileChange(path="foo.py", kind="created", ts=now - timedelta(seconds=30)),
            FileChange(path="foo.py", kind="modified", ts=now - timedelta(seconds=5)),
            FileChange(path="bar.py", kind="modified", ts=now - timedelta(seconds=60)),
        ]
    )
    watcher.changes[cwd_b] = deque(
        [
            FileChange(path="baz.py", kind="modified", ts=now - timedelta(seconds=10)),
        ]
    )
    state.fs_watcher = watcher

    r = client.get("/api/file-changes?minutes=10")
    assert r.status_code == 200
    data = r.json()
    # 3 unique (cwd, path) keys: (a, foo.py), (a, bar.py), (b, baz.py)
    assert len(data) == 3
    keys = {(d["cwd"], d["path"]) for d in data}
    assert keys == {(cwd_a, "foo.py"), (cwd_a, "bar.py"), (cwd_b, "baz.py")}

    # Dedup kept the NEWER row for (cwd_a, foo.py) — kind="modified".
    foo = next(d for d in data if d["path"] == "foo.py")
    assert foo["kind"] == "modified"
    assert foo["session_pids"] == [1]

    baz = next(d for d in data if d["path"] == "baz.py")
    assert baz["session_pids"] == [2]
    assert baz["project"] == "b"

    # Sorted ts desc.
    timestamps = [d["ts"] for d in data]
    assert timestamps == sorted(timestamps, reverse=True)


def test_all_file_changes_returns_empty_when_no_watcher(app_with_files):
    client, _, state, _ = app_with_files
    state.fs_watcher = None
    r = client.get("/api/file-changes")
    assert r.status_code == 200
    assert r.json() == []


def test_all_file_changes_caps_results(app_with_files):
    """A pathological cwd with >500 changes should be capped at 500."""
    client, _, state, home = app_with_files
    cwd = str(home / "big")
    Path(cwd).mkdir()
    state.sessions = {1: _make_session(1, cwd)}

    watcher = _FakeWatcher()
    now = datetime.now(timezone.utc)
    watcher.changes[cwd] = deque(
        FileChange(
            path=f"f{i:04d}.py",
            kind="modified",
            # Stagger ts so dedup-by-key doesn't collapse them.
            ts=now - timedelta(seconds=i),
        )
        for i in range(700)
    )
    state.fs_watcher = watcher

    r = client.get("/api/file-changes?minutes=120")
    assert r.status_code == 200
    assert len(r.json()) == 500


# ---------------------------------------------------------------------------
# /api/files/diff
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
        },
    )


def test_file_diff_returns_diff_for_git_file(app_with_files):
    client, _, state, home = app_with_files
    repo = home / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    target = repo / "state.py"
    target.write_text("line1\nline2\nline3\n")
    _git(repo, "add", "state.py")
    _git(repo, "commit", "-q", "-m", "init")
    # Now modify in the worktree so `git diff` has something to say.
    target.write_text("line1\nNEW LINE\nline3\n")

    state.sessions = {1: _make_session(1, str(repo))}

    r = client.get(
        "/api/files/diff",
        params={"cwd": str(repo), "path": "state.py"},
    )
    assert r.status_code == 200, r.json()
    data = r.json()
    assert data["is_git"] is True
    assert data["tracked"] is True
    assert "NEW LINE" in data["diff"]
    assert "state.py" in data["diff"]
    assert "1 file changed" in data["stat"]
    assert data["untracked_preview"] is None


def test_file_diff_returns_untracked_preview(app_with_files):
    client, _, state, home = app_with_files
    repo = home / "repo2"
    repo.mkdir()
    _git(repo, "init", "-q")
    # Untracked file.
    target = repo / "fresh.txt"
    target.write_text("hello world\n")

    state.sessions = {1: _make_session(1, str(repo))}

    r = client.get(
        "/api/files/diff",
        params={"cwd": str(repo), "path": "fresh.txt"},
    )
    assert r.status_code == 200, r.json()
    data = r.json()
    assert data["is_git"] is True
    assert data["tracked"] is False
    assert data["diff"] == ""
    assert data["untracked_preview"] == "hello world\n"


def test_file_diff_non_git_dir_returns_preview(app_with_files):
    client, _, state, home = app_with_files
    plain = home / "plain"
    plain.mkdir()
    (plain / "note.txt").write_text("just a note")
    state.sessions = {1: _make_session(1, str(plain))}

    r = client.get(
        "/api/files/diff",
        params={"cwd": str(plain), "path": "note.txt"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["is_git"] is False
    assert data["tracked"] is False
    assert data["untracked_preview"] == "just a note"


def test_file_diff_rejects_path_traversal(app_with_files):
    client, _, state, home = app_with_files
    repo = home / "repo3"
    repo.mkdir()
    _git(repo, "init", "-q")
    state.sessions = {1: _make_session(1, str(repo))}

    r = client.get(
        "/api/files/diff",
        params={"cwd": str(repo), "path": "../../../etc/passwd"},
    )
    assert r.status_code == 400


def test_files_diff_handles_symlink_loop(app_with_files):
    """#97: a symlink loop in the user-supplied path must surface as 400, not
    bubble RuntimeError from Path.resolve() up as a generic 500."""
    client, _, state, home = app_with_files
    repo = home / "repo_loop"
    repo.mkdir()
    # Make a symlink loop: a -> b -> a (both inside repo).
    a = repo / "a"
    b = repo / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    state.sessions = {1: _make_session(1, str(repo))}

    r = client.get(
        "/api/files/diff",
        params={"cwd": str(repo), "path": "a"},
    )
    assert r.status_code == 400, r.json()
    detail = r.json().get("detail", "").lower()
    assert "path resolution failed" in detail or "symlink" in detail


def test_file_diff_rejects_cwd_outside_active_sessions(app_with_files):
    client, _, state, home = app_with_files
    # An existing dir under HOME, but no session lives there.
    stranger = home / "stranger"
    stranger.mkdir()
    state.sessions = {}

    r = client.get(
        "/api/files/diff",
        params={"cwd": str(stranger), "path": "x.txt"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/files/open
# ---------------------------------------------------------------------------


def test_open_editor_403_when_disabled(app_with_files):
    client, _, state, home = app_with_files
    cwd = home / "edit_off"
    cwd.mkdir()
    (cwd / "a.py").write_text("")
    state.sessions = {1: _make_session(1, str(cwd))}
    state.config["editor"] = {"enabled": False, "command": "code"}

    r = client.post(
        "/api/files/open",
        json={"cwd": str(cwd), "path": "a.py"},
    )
    assert r.status_code == 403


def test_open_editor_runs_command_when_enabled(app_with_files, monkeypatch):
    client, _, state, home = app_with_files
    cwd = home / "edit_on"
    cwd.mkdir()
    target = cwd / "main.py"
    target.write_text("")
    state.sessions = {1: _make_session(1, str(cwd))}
    state.config["editor"] = {"enabled": True, "command": "code"}

    fake_popen = MagicMock()
    monkeypatch.setattr("backend.api.files.subprocess.Popen", fake_popen)

    r = client.post(
        "/api/files/open",
        json={"cwd": str(cwd), "path": "main.py"},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["success"] is True

    fake_popen.assert_called_once()
    args, kwargs = fake_popen.call_args
    argv = args[0]
    assert argv[0] == "code"
    assert argv[-1] == str(target.resolve())
    assert kwargs.get("start_new_session") is True


def test_open_editor_supports_multi_word_command(app_with_files, monkeypatch):
    """``open -t`` is a common macOS editor incantation — must be honored."""
    client, _, state, home = app_with_files
    cwd = home / "edit_multi"
    cwd.mkdir()
    (cwd / "f.txt").write_text("x")
    state.sessions = {1: _make_session(1, str(cwd))}
    state.config["editor"] = {"enabled": True, "command": "open -t"}

    fake_popen = MagicMock()
    monkeypatch.setattr("backend.api.files.subprocess.Popen", fake_popen)

    r = client.post(
        "/api/files/open",
        json={"cwd": str(cwd), "path": "f.txt"},
    )
    assert r.status_code == 200
    argv = fake_popen.call_args[0][0]
    assert argv[:2] == ["open", "-t"]


def test_open_editor_rejects_traversal(app_with_files, monkeypatch):
    client, _, state, home = app_with_files
    cwd = home / "edit_trav"
    cwd.mkdir()
    state.sessions = {1: _make_session(1, str(cwd))}
    state.config["editor"] = {"enabled": True, "command": "code"}

    fake_popen = MagicMock()
    monkeypatch.setattr("backend.api.files.subprocess.Popen", fake_popen)

    r = client.post(
        "/api/files/open",
        json={"cwd": str(cwd), "path": "../../etc/passwd"},
    )
    assert r.status_code == 400
    fake_popen.assert_not_called()


def test_open_editor_404_when_file_missing(app_with_files, monkeypatch):
    client, _, state, home = app_with_files
    cwd = home / "edit_missing"
    cwd.mkdir()
    state.sessions = {1: _make_session(1, str(cwd))}
    state.config["editor"] = {"enabled": True, "command": "code"}

    fake_popen = MagicMock()
    monkeypatch.setattr("backend.api.files.subprocess.Popen", fake_popen)

    r = client.post(
        "/api/files/open",
        json={"cwd": str(cwd), "path": "nope.py"},
    )
    assert r.status_code == 404
    fake_popen.assert_not_called()


def test_open_editor_rejects_unsafe_configured_command(app_with_files, monkeypatch):
    """A hand-edited config.toml with shell metacharacters in the editor
    command must NOT be honored even if it bypassed the API validator."""
    client, _, state, home = app_with_files
    cwd = home / "edit_evil"
    cwd.mkdir()
    (cwd / "a.py").write_text("")
    state.sessions = {1: _make_session(1, str(cwd))}
    # Intentionally invalid — semicolon makes this a shell injection vector.
    state.config["editor"] = {"enabled": True, "command": "code; rm -rf /"}

    fake_popen = MagicMock()
    monkeypatch.setattr("backend.api.files.subprocess.Popen", fake_popen)

    r = client.post(
        "/api/files/open",
        json={"cwd": str(cwd), "path": "a.py"},
    )
    assert r.status_code == 400
    fake_popen.assert_not_called()


# ---------------------------------------------------------------------------
# /api/config — the plan + editor allow-list extensions
# ---------------------------------------------------------------------------


def test_config_update_accepts_plan(tmp_path, monkeypatch):
    """Smoke-test that the ConfigUpdate allow-list accepts the new plan key."""
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    from backend.api import config_api as cfg_api

    app = FastAPI()
    app.include_router(cfg_api.router)
    state = MagicMock()
    state.config = {}
    app.state.s = state

    with TestClient(app, base_url="http://127.0.0.1") as client:
        r = client.post("/api/config", json={"plan": "max"})
        assert r.status_code == 200, r.json()
        assert r.json()["plan"] == "max"

        # Invalid plan value -> 422.
        r2 = client.post("/api/config", json={"plan": "enterprise"})
        assert r2.status_code == 422


def test_config_update_accepts_editor_block(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("backend.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("backend.config.LOGS_DIR", tmp_path / "logs")

    from backend.api import config_api as cfg_api

    app = FastAPI()
    app.include_router(cfg_api.router)
    state = MagicMock()
    state.config = {}
    app.state.s = state

    with TestClient(app, base_url="http://127.0.0.1") as client:
        r = client.post(
            "/api/config",
            json={"editor": {"enabled": True, "command": "cursor"}},
        )
        assert r.status_code == 200, r.json()
        out = r.json()
        assert out["editor"]["enabled"] is True
        assert out["editor"]["command"] == "cursor"

        # Shell metachars -> 422.
        r2 = client.post(
            "/api/config",
            json={"editor": {"command": "code; rm -rf /"}},
        )
        assert r2.status_code == 422
