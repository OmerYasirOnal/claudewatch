"""Security-hardening tests for backend.config (issues #40, #42).

These cover:
- #40: ~/.claudewatch directory + logs/ created with mode 0700 so other local
  users cannot enumerate or read session metadata on shared macOS hosts.
- #42: save_config writes via a temp file + os.replace so a crash mid-write
  cannot leave a truncated TOML that breaks startup.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Point CONFIG_DIR / LOGS_DIR / CONFIG_PATH at a fresh tmp HOME."""
    import backend.config as cfg

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(cfg, "CONFIG_DIR", fake_home / ".claudewatch")
    monkeypatch.setattr(cfg, "CONFIG_PATH", fake_home / ".claudewatch" / "config.toml")
    monkeypatch.setattr(cfg, "STATE_DB", fake_home / ".claudewatch" / "state.db")
    monkeypatch.setattr(cfg, "LOGS_DIR", fake_home / ".claudewatch" / "logs")
    monkeypatch.setattr(cfg, "PID_FILE", fake_home / ".claudewatch" / "server.pid")
    return cfg


def test_config_dir_mode_is_0700(tmp_home):
    """#40: ~/.claudewatch must be 0700 so other UIDs can't list/read children."""
    tmp_home.ensure_config_dir()
    mode = tmp_home.CONFIG_DIR.stat().st_mode & 0o777
    assert mode == 0o700, f"CONFIG_DIR mode is {oct(mode)}, want 0o700"


def test_logs_dir_mode_is_0700(tmp_home):
    """#40: logs/ must also be 0700."""
    tmp_home.ensure_config_dir()
    mode = tmp_home.LOGS_DIR.stat().st_mode & 0o777
    assert mode == 0o700, f"LOGS_DIR mode is {oct(mode)}, want 0o700"


def test_config_dir_mode_tightens_existing_dir(tmp_home):
    """If the dir already exists (created earlier with mode 0755), the next
    ensure_config_dir() call should tighten it to 0700."""
    tmp_home.CONFIG_DIR.mkdir(parents=True)
    tmp_home.CONFIG_DIR.chmod(0o755)
    tmp_home.ensure_config_dir()
    assert tmp_home.CONFIG_DIR.stat().st_mode & 0o777 == 0o700


def test_save_config_chmods_to_0600(tmp_home):
    """#40: config.toml must be 0600 after every save."""
    tmp_home.save_config({"port": 7799})
    mode = tmp_home.CONFIG_PATH.stat().st_mode & 0o777
    assert mode == 0o600, f"CONFIG_PATH mode is {oct(mode)}, want 0o600"


def test_save_config_is_atomic(tmp_home, monkeypatch):
    """#42: a crash mid-write must NOT leave a truncated config.toml.

    Strategy: seed a valid file, then patch tomli_w.dump to raise, then attempt
    save_config and assert the original file content survives intact.
    """
    # Seed a known-good config first.
    tmp_home.save_config({"port": 7799})
    original_bytes = tmp_home.CONFIG_PATH.read_bytes()
    assert b"7799" in original_bytes

    # Make the dump explode.
    def boom(*_a, **_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr("backend.config.tomli_w.dump", boom)

    with pytest.raises(RuntimeError):
        tmp_home.save_config({"port": 9999})

    # Original file must be untouched (no truncation, no partial write).
    assert tmp_home.CONFIG_PATH.read_bytes() == original_bytes


def test_save_config_cleans_up_tmp_on_failure(tmp_home, monkeypatch):
    """#42: a failed save_config should not leave .tmp turds in CONFIG_DIR."""
    tmp_home.save_config({"port": 7799})

    def boom(*_a, **_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr("backend.config.tomli_w.dump", boom)

    with pytest.raises(RuntimeError):
        tmp_home.save_config({"port": 9999})

    leftover = list(tmp_home.CONFIG_DIR.glob("*.tmp"))
    assert leftover == [], f"left .tmp files behind: {leftover}"


def test_ensure_config_dir_is_idempotent(tmp_home):
    """Calling ensure_config_dir twice should not raise."""
    tmp_home.ensure_config_dir()
    tmp_home.ensure_config_dir()
    assert stat.S_ISDIR(tmp_home.CONFIG_DIR.stat().st_mode)


def test_load_config_creates_file_with_0600(tmp_home):
    """First-time load_config seeds defaults; the new file must be 0600."""
    assert not tmp_home.CONFIG_PATH.exists()
    tmp_home.load_config()
    mode = tmp_home.CONFIG_PATH.stat().st_mode & 0o777
    assert mode == 0o600


def test_save_config_path_is_pathlib(tmp_home):
    """Defensive: confirm CONFIG_PATH stays a Path after save."""
    tmp_home.save_config({"port": 7788})
    assert isinstance(tmp_home.CONFIG_PATH, Path)


# ---------------------------------------------------------------------------
# #143: plan field must be lowercased on load + save so hand-edited
# ``config.toml`` entries like ``plan = "API"`` don't silently zero out
# cost data downstream (forecast/budgets/insights all compare to "api").
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("API", "api"),
        ("Api", "api"),
        ("ApI", "api"),
        ("  api  ", "api"),
        ("api", "api"),
        ("MAX", "max"),
        ("Pro", "pro"),
        ("", "api"),  # empty string falls back to default
    ],
)
def test_load_config_normalizes_plan_case(tmp_home, raw, expected):
    """tomllib parses ``plan`` raw — load_config must lowercase + strip it."""
    tmp_home.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_home.CONFIG_PATH.write_text(f'plan = "{raw}"\n', encoding="utf-8")
    cfg = tmp_home.load_config()
    assert cfg["plan"] == expected


def test_save_config_normalizes_plan_case(tmp_home):
    """Programmatic save_config('API') must persist as 'api' on disk."""
    tmp_home.save_config({"plan": "API"})
    reloaded = tmp_home.load_config()
    assert reloaded["plan"] == "api"


def test_update_config_normalizes_plan_case(tmp_home):
    """update_config must normalize the plan field before persisting."""
    tmp_home.save_config({"plan": "api"})
    merged = tmp_home.update_config({"plan": "Max"})
    assert merged["plan"] == "max"
    reloaded = tmp_home.load_config()
    assert reloaded["plan"] == "max"


def test_load_config_handles_non_string_plan_field(tmp_home):
    """A bogus non-string ``plan`` value must fall back to the default 'api'
    rather than crash downstream comparisons."""
    tmp_home.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_home.CONFIG_PATH.write_text("plan = 42\n", encoding="utf-8")
    cfg = tmp_home.load_config()
    assert cfg["plan"] == "api"
