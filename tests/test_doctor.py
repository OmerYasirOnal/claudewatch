"""Unit tests for ``backend.doctor`` self-diagnostic checks.

Every test runs against a ``tmp_path``-backed :class:`DoctorPaths` so we
never touch the user's real ``~/.claudewatch/`` or ``~/.claude/projects/``.
HTTP and iTerm probes are stubbed via injection — no real network calls.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from backend import doctor
from backend.cli import app


def _make_paths(
    tmp_path: Path,
    *,
    make_config_dir: bool = True,
    make_config_file: bool = True,
    make_state_db: bool = True,
    make_pid_file: bool = False,
    pid_file_content: str | None = None,
    make_claude_log_dir: bool = True,
    make_sample_log: bool = True,
    log_mtime_offset_seconds: float | None = None,
) -> doctor.DoctorPaths:
    """Build a fully-isolated :class:`DoctorPaths` rooted at ``tmp_path``."""
    config_dir = tmp_path / ".claudewatch"
    claude_log_dir = tmp_path / ".claude" / "projects"
    if make_config_dir:
        config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    if make_config_file:
        config_path.write_text("port = 7788\n")
    state_db = config_dir / "state.db"
    if make_state_db:
        state_db.write_bytes(b"sqlite-fake")
    pid_file = config_dir / "server.pid"
    if make_pid_file:
        pid_file.write_text(pid_file_content if pid_file_content is not None else str(os.getpid()))
    if make_claude_log_dir:
        claude_log_dir.mkdir(parents=True, exist_ok=True)
        if make_sample_log:
            proj = claude_log_dir / "-Users-someone-proj"
            proj.mkdir(parents=True, exist_ok=True)
            sample = proj / "abc.jsonl"
            sample.write_text('{"type":"user"}\n')
            if log_mtime_offset_seconds is not None:
                target = (
                    datetime.now(timezone.utc) - timedelta(seconds=log_mtime_offset_seconds)
                ).timestamp()
                os.utime(sample, (target, target))
    return doctor.DoctorPaths(
        config_dir=config_dir,
        config_path=config_path,
        state_db=state_db,
        pid_file=pid_file,
        claude_log_dir=claude_log_dir,
    )


# --- Environment checks -----------------------------------------------------


def test_check_python_version_passes_on_modern_python():
    r = doctor.check_python_version()
    assert r.name == "python_version"
    # The test environment itself is ≥ 3.10.
    assert r.status == "ok"
    assert "Python" in r.detail


def test_check_macos_version_either_ok_or_warn():
    r = doctor.check_macos_version()
    assert r.name == "macos_version"
    assert r.status in {"ok", "warn"}


def test_check_venv_returns_ok_or_warn():
    r = doctor.check_venv()
    assert r.name == "venv"
    assert r.status in {"ok", "warn"}


def test_check_psutil_import_ok():
    r = doctor.check_psutil_import()
    assert r.name == "import_psutil"
    assert r.status == "ok"


def test_check_aiosqlite_import_ok():
    r = doctor.check_aiosqlite_import()
    assert r.name == "import_aiosqlite"
    assert r.status == "ok"


def test_check_iterm2_import_present_in_dev_environment():
    # iterm2 is a hard dep (pyproject.toml); should always import in dev.
    r = doctor.check_iterm2_import()
    assert r.name == "import_iterm2"
    assert r.status == "ok"


def test_check_import_handles_missing_module(monkeypatch):
    """A missing module produces a warn/fail with a useful hint."""
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "definitely_not_a_real_module_xyz":
            raise ImportError("nope")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    r = doctor._check_import("definitely_not_a_real_module_xyz", "fail", "install it")
    assert r.status == "fail"
    assert r.hint == "install it"


# --- Filesystem checks ------------------------------------------------------


def test_check_config_dir_ok(tmp_path):
    paths = _make_paths(tmp_path)
    r = doctor.check_config_dir(paths)
    assert r.status == "ok"


def test_check_config_dir_fails_when_missing(tmp_path):
    paths = _make_paths(tmp_path, make_config_dir=False, make_config_file=False, make_state_db=False)
    r = doctor.check_config_dir(paths)
    assert r.status == "fail"
    assert r.hint is not None


def test_check_config_file_warns_when_missing(tmp_path):
    paths = _make_paths(tmp_path, make_config_file=False)
    r = doctor.check_config_file(paths)
    assert r.status == "warn"


def test_check_state_db_reports_size(tmp_path):
    paths = _make_paths(tmp_path)
    r = doctor.check_state_db(paths)
    assert r.status == "ok"
    assert "DB size" in r.detail


def test_check_state_db_warns_when_missing(tmp_path):
    paths = _make_paths(tmp_path, make_state_db=False)
    r = doctor.check_state_db(paths)
    assert r.status == "warn"


def test_check_claude_log_dir_fails_when_missing(tmp_path):
    paths = _make_paths(tmp_path, make_claude_log_dir=False, make_sample_log=False)
    r = doctor.check_claude_log_dir(paths)
    assert r.status == "fail"


def test_check_log_file_readable_ok(tmp_path):
    paths = _make_paths(tmp_path)
    r = doctor.check_log_file_readable(paths)
    assert r.status == "ok"


def test_check_log_file_readable_warns_when_no_files(tmp_path):
    paths = _make_paths(tmp_path, make_sample_log=False)
    r = doctor.check_log_file_readable(paths)
    assert r.status == "warn"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions test")
def test_check_log_file_readable_fails_on_permission_denied(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path)
    target = next(paths.claude_log_dir.glob("*/*.jsonl"))

    def fake_open(*args, **kwargs):
        raise PermissionError(f"EACCES: {target}")

    # Monkey-patch the builtin open used inside the doctor module.
    monkeypatch.setattr("backend.doctor.open", fake_open, raising=False)
    r = doctor.check_log_file_readable(paths)
    assert r.status == "fail"
    assert "Permission denied" in r.detail


# --- PID file check ---------------------------------------------------------


def test_check_pid_file_missing_is_ok(tmp_path):
    paths = _make_paths(tmp_path)  # no PID file by default
    r = doctor.check_pid_file(paths)
    assert r.status == "ok"
    assert "not running" in r.detail.lower()


def test_check_pid_file_non_numeric_warns(tmp_path):
    paths = _make_paths(tmp_path, make_pid_file=True, pid_file_content="not-a-pid\n")
    r = doctor.check_pid_file(paths)
    assert r.status == "warn"
    assert "non-numeric" in r.detail


def test_check_pid_file_dead_pid_warns(tmp_path):
    # PID 99999999 should not exist on any test machine.
    paths = _make_paths(tmp_path, make_pid_file=True, pid_file_content="99999999")
    r = doctor.check_pid_file(paths)
    assert r.status == "warn"
    assert "stale" in r.detail.lower()


def test_check_pid_file_alive(tmp_path):
    paths = _make_paths(tmp_path, make_pid_file=True, pid_file_content=str(os.getpid()))
    r = doctor.check_pid_file(paths)
    assert r.status == "ok"
    assert str(os.getpid()) in r.detail


# --- HTTP health check ------------------------------------------------------


def test_check_http_health_ok_with_stubbed_get():
    def fake_get(url: str) -> tuple[int, dict]:
        return 200, {"ok": True}

    r = doctor.check_http_health(port=7788, http_get=fake_get)
    assert r.status == "ok"
    assert "200" in r.detail


def test_check_http_health_connection_refused():
    def fake_get(url: str) -> tuple[int, dict]:
        raise ConnectionRefusedError("Connection refused")

    r = doctor.check_http_health(port=7788, http_get=fake_get)
    assert r.status == "fail"
    assert r.hint is not None


def test_check_http_health_500_response():
    def fake_get(url: str) -> tuple[int, dict]:
        return 500, {}

    r = doctor.check_http_health(port=7788, http_get=fake_get)
    assert r.status == "fail"
    assert "500" in r.detail


def test_check_http_health_socket_timeout():
    def fake_get(url: str) -> tuple[int, dict]:
        raise TimeoutError("read timeout")

    r = doctor.check_http_health(port=7788, http_get=fake_get)
    assert r.status == "fail"


def test_check_http_health_urlerror():
    def fake_get(url: str) -> tuple[int, dict]:
        raise urllib.error.URLError("dns?")

    r = doctor.check_http_health(port=7788, http_get=fake_get)
    assert r.status == "fail"


# --- PID matches admin status ----------------------------------------------


def test_check_pid_matches_admin_status_match(tmp_path):
    paths = _make_paths(tmp_path, make_pid_file=True, pid_file_content="1234")

    def fake_get(url: str) -> tuple[int, dict]:
        return 200, {"pid": 1234}

    r = doctor.check_pid_matches_admin_status(paths, port=7788, http_get=fake_get)
    assert r.status == "ok"


def test_check_pid_matches_admin_status_mismatch_fails(tmp_path):
    paths = _make_paths(tmp_path, make_pid_file=True, pid_file_content="1234")

    def fake_get(url: str) -> tuple[int, dict]:
        return 200, {"pid": 9999}

    r = doctor.check_pid_matches_admin_status(paths, port=7788, http_get=fake_get)
    assert r.status == "fail"
    assert "1234" in r.detail
    assert "9999" in r.detail


def test_check_pid_matches_admin_status_skipped_without_pid_file(tmp_path):
    paths = _make_paths(tmp_path)  # no pid file

    def fake_get(url: str) -> tuple[int, dict]:
        raise AssertionError("should not be called")

    r = doctor.check_pid_matches_admin_status(paths, port=7788, http_get=fake_get)
    assert r.status == "ok"
    assert "Skipped" in r.detail


# --- iTerm probe ------------------------------------------------------------


def test_check_iterm_api_ok_with_stubbed_probe():
    r = doctor.check_iterm_api(probe=lambda timeout: True)
    assert r.status == "ok"


def test_check_iterm_api_warn_when_probe_false():
    r = doctor.check_iterm_api(probe=lambda timeout: False)
    assert r.status == "warn"
    assert r.hint is not None


def test_check_iterm_api_warn_when_probe_raises():
    def boom(timeout):
        raise RuntimeError("boom")

    r = doctor.check_iterm_api(probe=boom)
    assert r.status == "warn"


# --- Log freshness ----------------------------------------------------------


def test_check_log_freshness_recent(tmp_path):
    paths = _make_paths(tmp_path, log_mtime_offset_seconds=60)
    r = doctor.check_log_freshness(paths)
    assert r.status == "ok"


def test_check_log_freshness_stale_warns(tmp_path):
    # > 24h old
    paths = _make_paths(tmp_path, log_mtime_offset_seconds=25 * 3600)
    r = doctor.check_log_freshness(paths)
    assert r.status == "warn"
    assert "last 24h" in r.detail


def test_check_log_freshness_no_logs(tmp_path):
    paths = _make_paths(tmp_path, make_sample_log=False)
    r = doctor.check_log_freshness(paths)
    assert r.status == "warn"


# --- Runner / overall ok ----------------------------------------------------


def test_run_checks_happy_path(tmp_path):
    paths = _make_paths(tmp_path, log_mtime_offset_seconds=300)
    results = doctor.run_checks(
        paths=paths,
        port=7788,
        http_get=lambda url: (200, {"pid": os.getpid()}),
        iterm_probe=lambda timeout: True,
    )
    # No FAIL when everything is healthy.
    assert doctor.overall_ok(results) is True
    statuses = {r.name: r.status for r in results}
    assert statuses["python_version"] == "ok"
    assert statuses["import_psutil"] == "ok"
    assert statuses["import_aiosqlite"] == "ok"
    assert statuses["config_dir"] == "ok"
    assert statuses["claude_log_dir"] == "ok"
    assert statuses["http_health"] == "ok"
    assert statuses["iterm_api"] == "ok"


def test_run_checks_propagates_failure(tmp_path):
    # No claude log dir → fail; daemon unreachable → fail.
    paths = _make_paths(
        tmp_path,
        make_claude_log_dir=False,
        make_sample_log=False,
    )

    def refuse(url: str) -> tuple[int, dict]:
        raise ConnectionRefusedError("nope")

    results = doctor.run_checks(paths=paths, port=7788, http_get=refuse, iterm_probe=lambda t: True)
    assert doctor.overall_ok(results) is False
    fails = [r.name for r in results if r.status == "fail"]
    assert "claude_log_dir" in fails
    assert "http_health" in fails


def test_to_json_shape(tmp_path):
    paths = _make_paths(tmp_path, log_mtime_offset_seconds=60)
    results = doctor.run_checks(
        paths=paths,
        port=7788,
        http_get=lambda url: (200, {"pid": os.getpid()}),
        iterm_probe=lambda timeout: True,
    )
    blob = doctor.to_json(results)
    # Re-serialize through stdlib json to confirm it's a plain dict, no
    # Pydantic objects leaking through.
    text = json.dumps(blob)
    parsed = json.loads(text)
    assert parsed["ok"] is True
    assert isinstance(parsed["checks"], list)
    assert all({"name", "status", "detail", "hint"} <= set(c.keys()) for c in parsed["checks"])


# --- CLI integration --------------------------------------------------------


def _patch_run_checks(monkeypatch, paths, http_get):
    """Replace ``doctor.run_checks`` with one that uses the injected paths/HTTP.

    We pre-build the result list with the **real** ``run_checks`` (using the
    isolated ``paths``) and stash it in a closure, so the CLI calling our
    patched function gets a fresh, fully-stubbed result without recursion.
    """
    real_run = doctor.run_checks
    pre_built = real_run(paths=paths, port=7788, http_get=http_get, iterm_probe=lambda timeout: True)

    def fake_run_checks(*args, **kwargs):
        return list(pre_built)

    monkeypatch.setattr(doctor, "run_checks", fake_run_checks)


def test_cli_doctor_json_exit_0_on_success(tmp_path, monkeypatch):
    """`claudewatch doctor --json` exits 0 when nothing failed."""
    paths = _make_paths(tmp_path, log_mtime_offset_seconds=60)
    _patch_run_checks(monkeypatch, paths, lambda url: (200, {"pid": os.getpid()}))

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert len(parsed["checks"]) > 0


def test_cli_doctor_json_exit_1_on_failure(tmp_path, monkeypatch):
    """`claudewatch doctor --json` exits 1 when any FAIL is present."""
    paths = _make_paths(
        tmp_path,
        make_claude_log_dir=False,
        make_sample_log=False,
    )
    _patch_run_checks(monkeypatch, paths, lambda url: (200, {"pid": os.getpid()}))

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["ok"] is False


def test_cli_doctor_human_output(tmp_path, monkeypatch):
    """Default human output renders status badges + the summary footer."""
    paths = _make_paths(tmp_path, log_mtime_offset_seconds=60)
    _patch_run_checks(monkeypatch, paths, lambda url: (200, {"pid": os.getpid()}))

    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "[OK]" in result.output
    # Summary footer like "N ok · M warn · K fail"
    assert "ok" in result.output


# --- #144: home-path collapse in detail/hint --------------------------------


def test_friendly_collapses_home_prefix(monkeypatch, tmp_path):
    """``_friendly`` replaces the user's home dir prefix with ``~``."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(doctor.Path, "home", classmethod(lambda cls: fake_home))

    assert doctor._friendly(fake_home / ".claudewatch") == f"~{os.sep}.claudewatch"
    assert doctor._friendly(fake_home) == "~"
    # Paths outside home are returned verbatim.
    outside = tmp_path / "elsewhere" / "x"
    assert doctor._friendly(outside) == str(outside)


def test_check_config_dir_missing_uses_friendly_path(monkeypatch, tmp_path):
    """When config_dir is missing under HOME, detail collapses to ``~/...``."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(doctor.Path, "home", classmethod(lambda cls: fake_home))

    # Build paths that *look* like they live under HOME but don't actually exist.
    paths = doctor.DoctorPaths(
        config_dir=fake_home / ".claudewatch",
        config_path=fake_home / ".claudewatch" / "config.toml",
        state_db=fake_home / ".claudewatch" / "state.db",
        pid_file=fake_home / ".claudewatch" / "server.pid",
        claude_log_dir=fake_home / ".claude" / "projects",
    )
    r = doctor.check_config_dir(paths)
    assert r.status == "fail"
    assert "~" in r.detail
    assert str(fake_home) not in r.detail


def test_check_config_file_hint_uses_friendly_path(monkeypatch, tmp_path):
    """The hint about chmod must not leak the absolute home prefix."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    cfg_dir = fake_home / ".claudewatch"
    cfg_dir.mkdir()
    cfg = cfg_dir / "config.toml"
    cfg.write_text("port=7788")
    # Drop read permission so check_config_file hits the "not readable" branch.
    os.chmod(cfg, 0)
    monkeypatch.setattr(doctor.Path, "home", classmethod(lambda cls: fake_home))
    try:
        paths = doctor.DoctorPaths(
            config_dir=cfg_dir,
            config_path=cfg,
            state_db=cfg_dir / "state.db",
            pid_file=cfg_dir / "server.pid",
            claude_log_dir=fake_home / ".claude" / "projects",
        )
        # Some sandboxes still let root read mode-0 files; only meaningful if access truly denied.
        if os.access(cfg, os.R_OK):
            pytest.skip("filesystem ignores mode bits in this environment")
        r = doctor.check_config_file(paths)
        assert r.status == "fail"
        assert r.hint is not None
        assert "~" in r.hint
        assert str(fake_home) not in r.hint
        assert str(fake_home) not in r.detail
    finally:
        os.chmod(cfg, 0o600)


def test_check_pid_file_stale_hint_uses_friendly_path(monkeypatch, tmp_path):
    """Stale-PID hint (`rm <path>`) must use ``~`` not the absolute home path."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    cfg_dir = fake_home / ".claudewatch"
    cfg_dir.mkdir()
    pid_file = cfg_dir / "server.pid"
    pid_file.write_text("99999999")  # unlikely to exist
    monkeypatch.setattr(doctor.Path, "home", classmethod(lambda cls: fake_home))

    paths = doctor.DoctorPaths(
        config_dir=cfg_dir,
        config_path=cfg_dir / "config.toml",
        state_db=cfg_dir / "state.db",
        pid_file=pid_file,
        claude_log_dir=fake_home / ".claude" / "projects",
    )
    r = doctor.check_pid_file(paths)
    assert r.status == "warn"
    assert r.hint is not None
    assert "~" in r.hint
    assert str(fake_home) not in r.detail
    assert str(fake_home) not in r.hint
