from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import tomli_w

# tomllib is stdlib on Python 3.11+; fall back to the `tomli` package on 3.10.
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — exercised in CI's 3.10 matrix
    import tomli as tomllib

CONFIG_DIR = Path.home() / ".claudewatch"
CONFIG_PATH = CONFIG_DIR / "config.toml"
STATE_DB = CONFIG_DIR / "state.db"
LOGS_DIR = CONFIG_DIR / "logs"
PID_FILE = CONFIG_DIR / "server.pid"

DEFAULT_CONFIG: dict[str, Any] = {
    "port": 7788,
    "read_only": False,
    "privacy_mode": True,
    "show_log_text": False,
    "file_change_retention_minutes": 10,
    "process_scan_interval_seconds": 2,
    "iterm_refresh_interval_seconds": 5,
    "ignore_patterns": [
        ".git/",
        "node_modules/",
        "__pycache__/",
        ".venv/",
        "venv/",
        ".DS_Store",
        "*.pyc",
        "*.log",
        "dist/",
        "build/",
        "target/",
        ".next/",
    ],
    "notifications": {
        "enabled": True,
        "on_session_end": True,
        "on_high_cost": True,
        "cost_threshold_usd": 5.0,
    },
    "remote_control": {
        # Opt-in: when False (the default), POST /api/sessions/{pid}/send-text
        # returns 403. Flipping this gives the dashboard the ability to type
        # text into running Claude sessions via the iTerm Python API.
        "enabled": False,
    },
    "pricing": {
        "claude-opus-4-7": {
            "input": 15.00,
            "output": 75.00,
            "cache_read": 1.50,
            "cache_write": 18.75,
        },
        "claude-opus-4-6": {
            "input": 15.00,
            "output": 75.00,
            "cache_read": 1.50,
            "cache_write": 18.75,
        },
        "claude-sonnet-4-6": {
            "input": 3.00,
            "output": 15.00,
            "cache_read": 0.30,
            "cache_write": 3.75,
        },
        "claude-sonnet-4-5": {
            "input": 3.00,
            "output": 15.00,
            "cache_read": 0.30,
            "cache_write": 3.75,
        },
        "claude-haiku-4-5": {
            "input": 1.00,
            "output": 5.00,
            "cache_read": 0.10,
            "cache_write": 1.25,
        },
    },
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_config_dir() -> None:
    # #40: ~/.claudewatch contains session metadata, conversation log paths,
    # and (via state.db, also written here) cost/usage history. We chmod the
    # directory 0700 so other local users on shared macOS hosts can't enumerate
    # or read its children — this protects state.db too, which aiosqlite opens
    # outside this code path.
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    os.chmod(LOGS_DIR, 0o700)


def load_config() -> dict[str, Any]:
    ensure_config_dir()
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)
    return _deep_merge(DEFAULT_CONFIG, data)


def save_config(cfg: dict[str, Any]) -> None:
    # #42: write atomically. A SIGKILL or disk-full mid-write must NOT leave a
    # truncated config.toml that breaks startup, so we dump to <path>.tmp first
    # and then os.replace into place (atomic on POSIX within the same dir).
    ensure_config_dir()
    merged = _deep_merge(DEFAULT_CONFIG, cfg)
    tmp_path = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
    try:
        with open(tmp_path, "wb") as f:
            tomli_w.dump(merged, f)
    except Exception:
        # Don't leave .tmp turds behind on failure.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    os.replace(tmp_path, CONFIG_PATH)
    # #40: config.toml itself is also 0600 so even if the dir mode is loosened
    # later (manual chmod, restore-from-backup, etc.) the file stays private.
    os.chmod(CONFIG_PATH, 0o600)


def update_config(updates: dict[str, Any]) -> dict[str, Any]:
    current = load_config()
    merged = _deep_merge(current, updates)
    save_config(merged)
    return merged
