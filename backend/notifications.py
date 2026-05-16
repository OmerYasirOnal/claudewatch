from __future__ import annotations

import asyncio
import logging
import subprocess

log = logging.getLogger(__name__)


def _safe_as(s: str) -> str:
    """Escape backslashes and double quotes for inclusion in an AppleScript string.

    AppleScript string literals use double quotes and use backslash as an
    escape character — so anything that might be attacker-controlled (cwd
    paths, PIDs rendered from session data, etc.) must have backslashes and
    quotes escaped before being interpolated into a script.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


async def notify(title: str, message: str, subtitle: str = "") -> None:
    """Show a macOS notification via osascript. Best-effort; never raises."""
    title_s = _safe_as(title)
    message_s = _safe_as(message)
    subtitle_s = _safe_as(subtitle)
    script = f'display notification "{message_s}" with title "{title_s}"' + (
        f' subtitle "{subtitle_s}"' if subtitle else ""
    )
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            check=False,
            timeout=3,
            capture_output=True,
        )
    except Exception as e:  # noqa: BLE001
        log.debug("notify failed: %s", e)
