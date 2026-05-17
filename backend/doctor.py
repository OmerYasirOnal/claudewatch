"""Self-diagnostic checks for ClaudeWatch.

``claudewatch doctor`` calls :func:`run_checks` and renders the resulting
:class:`CheckResult` list. Each check is small, isolated, and side-effect free
so unit tests can exercise them against ``tmp_path``-backed fakes.

Three categories of checks:

* **Environment** — Python/macOS version, third-party imports, venv detection.
* **Filesystem** — ``~/.claudewatch/`` (config dir) + ``~/.claude/projects/``
  (Claude CLI's conversation logs).
* **Daemon** — PID file health and a probe against ``/api/health``.
* **iTerm/permissions** — best-effort hints; the actual macOS Accessibility
  grant can't be reliably introspected from Python.
* **Freshness** — most recent conversation log mtime.

The CLI command in ``backend/cli.py`` is a thin wrapper that calls
:func:`run_checks` and formats the output. Tests should exercise the
individual ``_check_*`` functions directly using the ``paths``/HTTP injection
hooks so they never touch the user's real ``~/.claudewatch/`` directory or
make real network calls.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Status = Literal["ok", "warn", "fail"]


class CheckResult(BaseModel):
    """One row of the doctor report.

    ``name``    — short snake_case identifier (stable for scripting).
    ``status``  — ``ok`` / ``warn`` / ``fail``.
    ``detail``  — human-readable summary of what was found.
    ``hint``    — optional remediation pointer; ``None`` when nothing to do.
    """

    name: str
    status: Status
    detail: str
    hint: str | None = None


@dataclass
class DoctorPaths:
    """Filesystem paths the doctor checks against.

    Defaults point at the user's real config + log directories; tests
    construct one of these with ``tmp_path``-backed paths so they never
    mutate (or read from) the user's actual ``~/.claudewatch/`` or
    ``~/.claude/projects/``.
    """

    config_dir: Path
    config_path: Path
    state_db: Path
    pid_file: Path
    claude_log_dir: Path  # ~/.claude/projects/


def default_paths() -> DoctorPaths:
    """Production paths — mirror what ``backend.config`` and the Claude CLI use."""
    from backend.config import CONFIG_DIR, CONFIG_PATH, PID_FILE, STATE_DB

    return DoctorPaths(
        config_dir=CONFIG_DIR,
        config_path=CONFIG_PATH,
        state_db=STATE_DB,
        pid_file=PID_FILE,
        claude_log_dir=Path.home() / ".claude" / "projects",
    )


def _friendly(path: Path | str) -> str:
    """Collapse the user's home directory prefix to ``~`` for display.

    Issue #144: every ``detail``/``hint`` string used to leak the user's
    macOS username via the absolute home path (e.g.
    ``/Users/alice/.claudewatch``). When users paste ``claudewatch doctor``
    output into bug reports, that's a small but real privacy ding. Route
    every path interpolation through this helper to keep messages generic.

    The collapse is purely cosmetic — the JSON output (``--json``) goes
    through the same helper for consistency, and tests using ``tmp_path``
    paths see no change because ``tmp_path`` never lives under ``~``.
    """
    s = str(path)
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        return s
    if not home:
        return s
    if s == home:
        return "~"
    prefix = home + os.sep
    if s.startswith(prefix):
        return "~" + os.sep + s[len(prefix):]
    return s


# --- Environment ------------------------------------------------------------


def check_python_version() -> CheckResult:
    """FAIL if running Python < 3.10 (project floor in ``pyproject.toml``)."""
    v = sys.version_info
    detail = f"Python {v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < (3, 10):
        return CheckResult(
            name="python_version",
            status="fail",
            detail=detail,
            hint="Install Python 3.10 or newer (project requires >=3.10).",
        )
    return CheckResult(name="python_version", status="ok", detail=detail)


def check_macos_version() -> CheckResult:
    """WARN if macOS < 14 (Sonoma) — the menu-bar .app requires Sonoma+."""
    if platform.system() != "Darwin":
        return CheckResult(
            name="macos_version",
            status="warn",
            detail=f"Not macOS (running {platform.system()})",
            hint="ClaudeWatch is designed for macOS; some features will be unavailable.",
        )
    mac_ver, _, _ = platform.mac_ver()
    if not mac_ver:
        return CheckResult(
            name="macos_version",
            status="warn",
            detail="Could not detect macOS version",
            hint=None,
        )
    try:
        major = int(mac_ver.split(".", 1)[0])
    except (ValueError, IndexError):
        return CheckResult(
            name="macos_version",
            status="warn",
            detail=f"Could not parse macOS version '{mac_ver}'",
            hint=None,
        )
    if major < 14:
        return CheckResult(
            name="macos_version",
            status="warn",
            detail=f"macOS {mac_ver} (< Sonoma 14)",
            hint="The bundled menu-bar app requires macOS 14+. CLI still works.",
        )
    return CheckResult(name="macos_version", status="ok", detail=f"macOS {mac_ver}")


def check_venv() -> CheckResult:
    """Informational — tell the user whether they're in a venv (and which)."""
    # sys.prefix differs from sys.base_prefix when running inside a venv (PEP 405).
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        return CheckResult(
            name="venv",
            status="ok",
            detail=f"Running in virtual env: {sys.prefix}",
        )
    return CheckResult(
        name="venv",
        status="warn",
        detail=f"Running outside venv (sys.prefix={sys.prefix})",
        hint="Prefer a project-local venv to avoid clashes with system packages.",
    )


def _check_import(module_name: str, status_if_missing: Status, hint: str | None) -> CheckResult:
    """Try to import ``module_name``; report OK on success, otherwise FAIL/WARN."""
    try:
        __import__(module_name)
    except ImportError as e:
        return CheckResult(
            name=f"import_{module_name}",
            status=status_if_missing,
            detail=f"Cannot import '{module_name}': {e}",
            hint=hint,
        )
    return CheckResult(
        name=f"import_{module_name}",
        status="ok",
        detail=f"'{module_name}' importable",
    )


def check_iterm2_import() -> CheckResult:
    return _check_import(
        "iterm2",
        status_if_missing="warn",
        hint="`pip install iterm2`; without it, iTerm detection falls back to AppleScript.",
    )


def check_psutil_import() -> CheckResult:
    return _check_import(
        "psutil",
        status_if_missing="fail",
        hint="`pip install psutil` — required for process detection.",
    )


def check_aiosqlite_import() -> CheckResult:
    return _check_import(
        "aiosqlite",
        status_if_missing="fail",
        hint="`pip install aiosqlite` — required for the state database.",
    )


# --- Filesystem -------------------------------------------------------------


def check_config_dir(paths: DoctorPaths) -> CheckResult:
    """FAIL if ``~/.claudewatch/`` is missing or not writable."""
    pretty = _friendly(paths.config_dir)
    if not paths.config_dir.exists():
        return CheckResult(
            name="config_dir",
            status="fail",
            detail=f"{pretty} does not exist",
            hint="Run `claudewatch start` once to create the config dir.",
        )
    if not paths.config_dir.is_dir():
        return CheckResult(
            name="config_dir",
            status="fail",
            detail=f"{pretty} is not a directory",
            hint="Remove the file at that path so claudewatch can create the dir.",
        )
    if not os.access(paths.config_dir, os.W_OK):
        return CheckResult(
            name="config_dir",
            status="fail",
            detail=f"{pretty} is not writable",
            hint="Fix permissions: `chmod u+rwx ~/.claudewatch`.",
        )
    return CheckResult(
        name="config_dir",
        status="ok",
        detail=f"{pretty} exists and is writable",
    )


def check_config_file(paths: DoctorPaths) -> CheckResult:
    """WARN if ``config.toml`` is missing — it's auto-created on first run."""
    pretty = _friendly(paths.config_path)
    if not paths.config_path.exists():
        return CheckResult(
            name="config_file",
            status="warn",
            detail=f"{pretty} not found",
            hint="It will be created on next `claudewatch start`.",
        )
    if not os.access(paths.config_path, os.R_OK):
        return CheckResult(
            name="config_file",
            status="fail",
            detail=f"{pretty} not readable",
            hint=f"Fix permissions: `chmod u+r {pretty}`.",
        )
    return CheckResult(
        name="config_file",
        status="ok",
        detail=f"{pretty} present and readable",
    )


def _format_bytes(n: int) -> str:
    """Compact human-readable byte size (KB, MB, GB)."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def check_state_db(paths: DoctorPaths) -> CheckResult:
    """WARN if the SQLite state DB is missing — it's auto-created on first run."""
    pretty = _friendly(paths.state_db)
    if not paths.state_db.exists():
        return CheckResult(
            name="state_db",
            status="warn",
            detail=f"{pretty} not found",
            hint="It will be created on next `claudewatch start`.",
        )
    try:
        size = paths.state_db.stat().st_size
    except OSError as e:
        return CheckResult(
            name="state_db",
            status="fail",
            detail=f"Cannot stat {pretty}: {e}",
            hint=None,
        )
    return CheckResult(
        name="state_db",
        status="ok",
        detail=f"DB size: {_format_bytes(size)}",
    )


def check_claude_log_dir(paths: DoctorPaths) -> CheckResult:
    """FAIL if ``~/.claude/projects/`` doesn't exist — no sessions can be detected."""
    pretty = _friendly(paths.claude_log_dir)
    if not paths.claude_log_dir.exists():
        return CheckResult(
            name="claude_log_dir",
            status="fail",
            detail=f"{pretty} does not exist",
            hint="Start a `claude` session at least once to create the log dir.",
        )
    if not paths.claude_log_dir.is_dir():
        return CheckResult(
            name="claude_log_dir",
            status="fail",
            detail=f"{pretty} is not a directory",
            hint=None,
        )
    return CheckResult(
        name="claude_log_dir",
        status="ok",
        detail=f"{pretty} present",
    )


def check_log_file_readable(paths: DoctorPaths) -> CheckResult:
    """Spot-check that the first ``*.jsonl`` we find under ``claude_log_dir`` is readable."""
    if not paths.claude_log_dir.is_dir():
        return CheckResult(
            name="log_file_readable",
            status="warn",
            detail="No claude log dir to spot-check",
            hint=None,
        )
    try:
        first = next(paths.claude_log_dir.glob("*/*.jsonl"), None)
    except OSError as e:
        return CheckResult(
            name="log_file_readable",
            status="fail",
            detail=f"Cannot enumerate {_friendly(paths.claude_log_dir)}: {e}",
            hint=None,
        )
    if first is None:
        return CheckResult(
            name="log_file_readable",
            status="warn",
            detail="No conversation log files found yet",
            hint="Run `claude` in some project to generate logs.",
        )
    try:
        # Open + read a single byte; cheaper than stat + os.access and exercises
        # the actual read codepath that ConversationLog uses.
        with open(first, "rb") as f:
            f.read(1)
    except PermissionError:
        return CheckResult(
            name="log_file_readable",
            status="fail",
            detail=f"Permission denied reading {_friendly(first)}",
            hint="Fix file permissions or relaunch claudewatch with the right user.",
        )
    except OSError as e:
        return CheckResult(
            name="log_file_readable",
            status="fail",
            detail=f"Cannot read {_friendly(first)}: {e}",
            hint=None,
        )
    return CheckResult(
        name="log_file_readable",
        status="ok",
        detail=f"Sampled {first.name} successfully",
    )


# --- Daemon -----------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    """Cross-platform ``kill -0`` — True if a process with this PID exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but we don't own it. Still "alive".
        return True
    except OSError:
        return False
    return True


def check_pid_file(paths: DoctorPaths) -> CheckResult:
    """Inspect ``~/.claudewatch/server.pid`` — present? numeric? process alive?"""
    pretty = _friendly(paths.pid_file)
    if not paths.pid_file.exists():
        return CheckResult(
            name="pid_file",
            status="ok",
            detail="No PID file (daemon not running)",
        )
    try:
        # Read as bytes + decode defensively: a binary/non-UTF-8 PID file (e.g.
        # corrupted write, accidental redirect) used to raise UnicodeDecodeError
        # straight through `read_text()`, which is a ValueError subclass and so
        # bypassed the OSError handler — `claudewatch doctor` then crashed with
        # a traceback on the very situation the check exists to surface (#147).
        raw = paths.pid_file.read_bytes().decode("ascii", errors="replace").strip()
    except OSError as e:
        return CheckResult(
            name="pid_file",
            status="warn",
            detail=f"Could not read PID file: {e}",
            hint=f"Delete the file: `rm {pretty}`.",
        )
    try:
        pid = int(raw)
    except ValueError:
        return CheckResult(
            name="pid_file",
            status="warn",
            detail=f"PID file contains non-numeric content: {raw!r}",
            hint=f"Delete the stale PID file: `rm {pretty}`.",
        )
    if not _pid_alive(pid):
        return CheckResult(
            name="pid_file",
            status="warn",
            detail=f"PID {pid} from {pretty} is not running (stale)",
            hint=f"Delete the stale PID file: `rm {pretty}`.",
        )
    return CheckResult(
        name="pid_file",
        status="ok",
        detail=f"Daemon running as PID {pid}",
    )


def _default_port() -> int:
    """Read the configured port; fall back to 7788 if config load fails."""
    try:
        from backend.config import load_config

        return int(load_config().get("port", 7788))
    except Exception:  # noqa: BLE001 — config corruption shouldn't crash the doctor
        return 7788


def check_http_health(
    port: int | None = None,
    http_get: Callable[[str], tuple[int, dict]] | None = None,
) -> CheckResult:
    """Probe ``GET http://127.0.0.1:<port>/api/health``.

    ``http_get`` is injectable so tests can simulate connection-refused and
    non-200 responses without touching a real socket. The default returns
    ``(status_code, parsed_json_or_empty)``.
    """
    if port is None:
        port = _default_port()
    if http_get is None:
        http_get = _default_http_get
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        status, body = http_get(url)
    except (TimeoutError, ConnectionRefusedError, ConnectionError, urllib.error.URLError, OSError) as e:
        return CheckResult(
            name="http_health",
            status="fail",
            detail=f"Daemon not responding on {url}: {e}",
            hint="Start the daemon: `claudewatch start --daemon`.",
        )
    if status != 200:
        return CheckResult(
            name="http_health",
            status="fail",
            detail=f"GET {url} returned HTTP {status}",
            hint="Check `claudewatch logs` for errors.",
        )
    return CheckResult(
        name="http_health",
        status="ok",
        detail=f"GET {url} → 200 OK",
    )


def _default_http_get(url: str) -> tuple[int, dict]:
    """Production-mode HTTP GET; returns ``(status_code, parsed_json_or_empty)``."""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=2) as resp:
        status = resp.status
        try:
            body = json.loads(resp.read())
            if not isinstance(body, dict):
                body = {}
        except Exception:  # noqa: BLE001
            body = {}
    return status, body


def check_pid_matches_admin_status(
    paths: DoctorPaths,
    port: int | None = None,
    http_get: Callable[[str], tuple[int, dict]] | None = None,
) -> CheckResult:
    """Catch the "two daemons fighting for the same port" failure mode.

    Compares the PID in ``server.pid`` against the ``pid`` field that
    ``GET /api/admin/status`` reports. If they differ, some other process
    has claimed the port out from under the recorded daemon.
    """
    if port is None:
        port = _default_port()
    if http_get is None:
        http_get = _default_http_get
    if not paths.pid_file.exists():
        return CheckResult(
            name="pid_matches_admin_status",
            status="ok",
            detail="Skipped (no PID file)",
        )
    try:
        # Decode as bytes for the same #147 reason as check_pid_file — the
        # (OSError, ValueError) tuple does NOT include UnicodeDecodeError
        # against some Python versions if a custom codec is registered, so
        # be explicit and force-decode replacements before int() parsing.
        recorded = int(paths.pid_file.read_bytes().decode("ascii", errors="replace").strip())
    except (OSError, ValueError):
        return CheckResult(
            name="pid_matches_admin_status",
            status="warn",
            detail="PID file unreadable; skipped",
        )
    url = f"http://127.0.0.1:{port}/api/admin/status"
    try:
        status, body = http_get(url)
    except (TimeoutError, ConnectionRefusedError, ConnectionError, urllib.error.URLError, OSError):
        return CheckResult(
            name="pid_matches_admin_status",
            status="warn",
            detail="Could not reach /api/admin/status; skipped",
        )
    if status != 200:
        return CheckResult(
            name="pid_matches_admin_status",
            status="warn",
            detail=f"/api/admin/status returned HTTP {status}",
        )
    reported = body.get("pid")
    if reported is None:
        return CheckResult(
            name="pid_matches_admin_status",
            status="warn",
            detail="Admin status missing 'pid'",
        )
    if int(reported) != recorded:
        return CheckResult(
            name="pid_matches_admin_status",
            status="fail",
            detail=f"PID file says {recorded}, daemon on port {port} reports {reported}",
            hint="Two daemons may be running. Stop both and start a fresh one.",
        )
    return CheckResult(
        name="pid_matches_admin_status",
        status="ok",
        detail=f"PID {recorded} matches /api/admin/status",
    )


# --- Permissions / iTerm ----------------------------------------------------


def check_accessibility_hint() -> CheckResult:
    """Surface a pointer to the permissions doc — actual grant isn't detectable."""
    return CheckResult(
        name="accessibility_hint",
        status="ok",
        detail="Accessibility / Automation grants cannot be auto-detected",
        hint="See docs/permissions-setup.md if focus / new-session actions fail.",
    )


def check_iterm_api(
    probe: Callable[[float], bool] | None = None,
    timeout: float = 1.0,
) -> CheckResult:
    """Try to open an iTerm2 Python API connection; report success/failure.

    ``probe`` is injectable so tests can stub the (real, network-bound)
    ``iterm2.Connection.async_create`` call. The default implementation
    runs the probe under a fresh event loop with a 1 s timeout and never
    touches existing terminal sessions.
    """
    if probe is None:
        probe = _default_iterm_probe
    try:
        ok = probe(timeout)
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            name="iterm_api",
            status="warn",
            detail=f"iTerm probe raised: {e}",
            hint="Enable iTerm2 → Settings → General → Magic → Python API.",
        )
    if ok:
        return CheckResult(
            name="iterm_api",
            status="ok",
            detail="iTerm2 Python API reachable",
        )
    return CheckResult(
        name="iterm_api",
        status="warn",
        detail="iTerm2 Python API not reachable",
        hint="Enable iTerm2 → Settings → General → Magic → Python API.",
    )


def _default_iterm_probe(timeout: float) -> bool:
    """Run ``iterm2.Connection.async_create()`` under a fresh event loop."""
    try:
        import iterm2  # type: ignore
    except ImportError:
        return False
    import asyncio

    async def _go() -> bool:
        try:
            await asyncio.wait_for(iterm2.Connection.async_create(), timeout=timeout)
            return True
        except Exception:  # noqa: BLE001
            return False

    try:
        return asyncio.run(_go())
    except RuntimeError:
        # If we're already inside a running loop (unusual for the CLI),
        # fall back to a private loop. Should not happen from ``claudewatch
        # doctor`` but keeps the function robust.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_go())
        finally:
            loop.close()


# --- Log freshness ----------------------------------------------------------


def check_log_freshness(paths: DoctorPaths, now: datetime | None = None) -> CheckResult:
    """Inspect mtime of the most recently modified ``*.jsonl`` log."""
    if not paths.claude_log_dir.is_dir():
        return CheckResult(
            name="log_freshness",
            status="warn",
            detail="No claude log dir; skipping freshness check",
        )
    try:
        files = list(paths.claude_log_dir.glob("*/*.jsonl"))
    except OSError as e:
        return CheckResult(
            name="log_freshness",
            status="warn",
            detail=f"Cannot enumerate logs: {e}",
        )
    if not files:
        return CheckResult(
            name="log_freshness",
            status="warn",
            detail="No conversation logs found",
        )
    try:
        latest_mtime = max(f.stat().st_mtime for f in files)
    except OSError as e:
        return CheckResult(
            name="log_freshness",
            status="warn",
            detail=f"Cannot stat logs: {e}",
        )
    if now is None:
        now = datetime.now(timezone.utc)
    latest = datetime.fromtimestamp(latest_mtime, tz=timezone.utc)
    age_sec = max(0.0, (now - latest).total_seconds())
    age_human = _format_age(age_sec)
    if age_sec > 24 * 3600:
        return CheckResult(
            name="log_freshness",
            status="warn",
            detail=f"Most recent log modified {age_human} ago (no activity in last 24h)",
            hint=None,
        )
    return CheckResult(
        name="log_freshness",
        status="ok",
        detail=f"Most recent log modified {age_human} ago",
    )


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


# --- Runner -----------------------------------------------------------------


def run_checks(
    paths: DoctorPaths | None = None,
    port: int | None = None,
    http_get: Callable[[str], tuple[int, dict]] | None = None,
    iterm_probe: Callable[[float], bool] | None = None,
    now: datetime | None = None,
) -> list[CheckResult]:
    """Run all checks in declared order. Returns the list of ``CheckResult``s.

    Every external dependency (filesystem, HTTP, iTerm) is injectable so
    tests can run the entire pipeline without side effects.
    """
    if paths is None:
        paths = default_paths()
    return [
        # Environment
        check_python_version(),
        check_macos_version(),
        check_venv(),
        check_psutil_import(),
        check_aiosqlite_import(),
        check_iterm2_import(),
        # Filesystem
        check_config_dir(paths),
        check_config_file(paths),
        check_state_db(paths),
        check_claude_log_dir(paths),
        check_log_file_readable(paths),
        # Daemon
        check_pid_file(paths),
        check_http_health(port=port, http_get=http_get),
        check_pid_matches_admin_status(paths, port=port, http_get=http_get),
        # Permissions / iTerm
        check_accessibility_hint(),
        check_iterm_api(probe=iterm_probe),
        # Freshness
        check_log_freshness(paths, now=now),
    ]


def overall_ok(results: list[CheckResult]) -> bool:
    """True when no check FAILed (WARN doesn't fail the overall report)."""
    return not any(r.status == "fail" for r in results)


def to_json(results: list[CheckResult]) -> dict:
    """Machine-readable shape for ``claudewatch doctor --json``."""
    return {
        "ok": overall_ok(results),
        "checks": [r.model_dump() for r in results],
    }
