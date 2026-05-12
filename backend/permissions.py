from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from backend.detectors.conversation_log import find_log_dir
from backend.models import HealthReport


async def probe_iterm_api(timeout: float = 2.0) -> bool:
    try:
        import iterm2  # type: ignore
    except ImportError:
        return False
    try:
        return await asyncio.wait_for(_probe_iterm_inner(), timeout=timeout)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return False


async def _probe_iterm_inner() -> bool:
    import iterm2  # type: ignore

    conn = await iterm2.Connection.async_create()
    try:
        app = await iterm2.async_get_app(conn)
        return app is not None
    finally:
        try:
            await conn.async_close()
        except Exception:
            pass


def probe_automation() -> bool:
    # macOS only; treat presence of osascript as a coarse proxy.
    # True permission check requires actually running an AppleScript that targets iTerm.
    return shutil.which("osascript") is not None


def probe_tmux() -> bool:
    return shutil.which("tmux") is not None


async def health_report() -> HealthReport:
    iterm_ok = await probe_iterm_api()
    log_dir = find_log_dir()
    issues: list[str] = []
    if not iterm_ok:
        issues.append(
            "iTerm2 Python API not reachable — enable it in iTerm2 Settings → General → Magic"
        )
    if not log_dir:
        issues.append("Conversation log directory not found — token/tool stats unavailable")
    return HealthReport(
        iterm_api=iterm_ok,
        automation=probe_automation(),
        tmux_available=probe_tmux(),
        log_dir_found=log_dir is not None,
        log_dir_path=str(log_dir) if log_dir else None,
        issues=issues,
    )
