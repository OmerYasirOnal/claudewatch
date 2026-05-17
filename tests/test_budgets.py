"""Tests for configurable cost budgets — backend scheduler + API endpoint.

Covers:
* ``_maybe_check_budgets`` — 80% / 100% threshold semantics, plan gate,
  enabled flag, once-per-uptime dedupe, rate-limiting between checks,
  notification body/title format.
* ``State.cost_in_window`` — sum is taken from rows whose ``ended_at`` falls
  inside the window only.
* ``GET /api/budgets`` — payload shape, plan gating, zero-cost path.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import budgets as budgets_api
from backend.config import DEFAULT_CONFIG
from backend.server import (
    _BUDGET_CHECK_INTERVAL_SECONDS,
    AppState,
    _format_budget_notification,
    _maybe_check_budgets,
)
from backend.state import State

# --- helpers ---------------------------------------------------------------


async def _insert_ended(
    state: State,
    *,
    pid: int,
    started_at: datetime,
    ended_at: datetime,
    cost: float,
    tokens: int = 0,
    cwd: str = "/x",
    model: str = "claude-opus-4-7",
) -> None:
    """Insert an ended row directly via the underlying connection."""
    await state.connect()
    assert state._conn is not None
    summary = json.dumps({"pid": pid, "cwd": cwd})
    await state._conn.execute(
        """
        INSERT INTO sessions (pid, started_at, ended_at, last_seen, cwd, model,
                              total_tokens, cost_estimate, summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pid,
            started_at.isoformat(),
            ended_at.isoformat(),
            ended_at.isoformat(),
            cwd,
            model,
            tokens,
            cost,
            summary,
        ),
    )
    await state._conn.commit()


def _budgets_cfg(**overrides) -> dict:
    """Build a config dict with budgets enabled (plus daily $5)."""
    cfg = copy.deepcopy(dict(DEFAULT_CONFIG))
    cfg["plan"] = "api"
    cfg["budgets"] = {
        "enabled": True,
        "daily_usd": 5.00,
        "weekly_usd": 30.00,
        "monthly_usd": 100.00,
        "warn_at_percent": 80,
    }
    cfg["budgets"].update(overrides)
    return cfg


@pytest.fixture
async def state(tmp_path):
    st = State(tmp_path / "state.db")
    await st.connect()
    await st.init_db()
    yield st
    await st.close()


@pytest.fixture
async def fresh_app_state(state):
    """A baseline AppState with budgets enabled and the DB attached."""
    s = AppState(config=_budgets_cfg(), state=state)
    # Sentinel that guarantees the rate-limit guard
    # `(time.monotonic() - last_budget_check_at) < 60` passes regardless of
    # how recently the test runner booted. Setting to 0.0 was racy on cold
    # CI workers where monotonic() < 60s (Py3.10 runner repro'd this).
    s.last_budget_check_at = -1_000_000.0
    return s


# --- State.cost_in_window --------------------------------------------------


async def test_cost_in_window_sums_only_rows_inside_window(state):
    """Only ended_at within the trailing window should count toward the sum."""
    now = datetime.now(timezone.utc)
    # Inside the 24h window.
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=1.50
    )
    await _insert_ended(
        state, pid=2, started_at=now - timedelta(hours=5), ended_at=now - timedelta(hours=3), cost=2.50
    )
    # Outside the 24h window.
    await _insert_ended(
        state, pid=3, started_at=now - timedelta(hours=30), ended_at=now - timedelta(hours=26), cost=99.0
    )

    total = await state.cost_in_window(24)
    assert total == pytest.approx(4.0)


async def test_cost_in_window_handles_empty_db(state):
    assert await state.cost_in_window(24) == 0.0


async def test_cost_in_window_clamps_negative_rows(state):
    """A corrupted negative cost row must not drive the sum negative."""
    now = datetime.now(timezone.utc)
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=-5.0
    )
    await _insert_ended(
        state, pid=2, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=2.0
    )
    assert await state.cost_in_window(24) == pytest.approx(2.0)


# --- _maybe_check_budgets — threshold tiers -------------------------------


async def test_budget_approaching_fires_at_80_percent(fresh_app_state, monkeypatch):
    """Mocks `state.cost_in_window` directly so the test exercises only the
    threshold/notification logic (no aiosqlite-loop dependency that differs
    between Python 3.10 and 3.12)."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    # Daily budget = $5, spend = $4 → 80%. cost_in_window is called per
    # window (24/168/720); only the 24h call returns 4.0 so daily fires.
    fresh_app_state.state.cost_in_window = AsyncMock(
        side_effect=lambda hours: {24: 4.0, 168: 0.0, 720: 0.0}.get(hours, 0.0)
    )

    await _maybe_check_budgets(fresh_app_state)

    assert mock_notify.await_count >= 1
    titles = [call.kwargs.get("title", "") for call in mock_notify.await_args_list]
    assert any("Daily budget at" in t and "exceeded" not in t for t in titles)
    assert "daily" in fresh_app_state.notified_budget_approaching


async def test_budget_exceeded_fires_at_100_percent(fresh_app_state, monkeypatch):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    # Daily budget = $5, spend = $5 → 100% exactly.
    fresh_app_state.state.cost_in_window = AsyncMock(
        side_effect=lambda hours: {24: 5.0, 168: 0.0, 720: 0.0}.get(hours, 0.0)
    )

    await _maybe_check_budgets(fresh_app_state)

    titles = [call.kwargs.get("title", "") for call in mock_notify.await_args_list]
    assert any("Daily budget exceeded" in t for t in titles)
    assert "daily" in fresh_app_state.notified_budget_exceeded
    # Once exceeded, the "approaching" gate is also marked to avoid a stale
    # 80% warning being sent after the 100% one.
    assert "daily" in fresh_app_state.notified_budget_approaching


async def test_budget_below_warn_threshold_does_not_notify(fresh_app_state, monkeypatch, state):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    # Spend = $1.00 → 20% of $5 daily.
    now = datetime.now(timezone.utc)
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=1.00
    )

    await _maybe_check_budgets(fresh_app_state)
    assert mock_notify.await_count == 0
    assert not fresh_app_state.notified_budget_approaching
    assert not fresh_app_state.notified_budget_exceeded


# --- _maybe_check_budgets — dedupe / rate-limit ---------------------------


async def test_budget_fires_only_once_per_uptime(fresh_app_state, monkeypatch, state):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    now = datetime.now(timezone.utc)
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=4.50
    )

    await _maybe_check_budgets(fresh_app_state)
    # Force the rate-limit cooldown to elapse so the second call evaluates
    # (otherwise this test would just be re-asserting the rate limit).
    fresh_app_state.last_budget_check_at -= _BUDGET_CHECK_INTERVAL_SECONDS + 1

    await _maybe_check_budgets(fresh_app_state)
    import asyncio as _aio

    for _ in range(3):
        await _aio.sleep(0)
    pending = [t for t in _aio.all_tasks() if t is not _aio.current_task() and not t.done()]
    if pending:
        await _aio.wait(pending, timeout=2.0)

    # First call fired daily approaching; second must not re-fire because
    # "daily" is already in notified_budget_approaching.
    daily_alerts = [c for c in mock_notify.await_args_list if "Daily" in c.kwargs.get("title", "")]
    assert len(daily_alerts) == 1


async def test_budget_check_rate_limited_within_interval(fresh_app_state, monkeypatch, state):
    """Two calls inside the cooldown window: only the first does any work."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)

    now = datetime.now(timezone.utc)
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=4.50
    )

    await _maybe_check_budgets(fresh_app_state)
    first_check_at = fresh_app_state.last_budget_check_at
    # Second call right away — must skip.
    await _maybe_check_budgets(fresh_app_state)
    assert fresh_app_state.last_budget_check_at == first_check_at


# --- _maybe_check_budgets — gating ----------------------------------------


async def test_budget_disabled_flag_suppresses_notifications(fresh_app_state, monkeypatch, state):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    fresh_app_state.config["budgets"]["enabled"] = False

    now = datetime.now(timezone.utc)
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=100.0
    )

    await _maybe_check_budgets(fresh_app_state)
    assert mock_notify.await_count == 0


async def test_budget_non_api_plan_suppresses_notifications(fresh_app_state, monkeypatch, state):
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    fresh_app_state.config["plan"] = "max"

    now = datetime.now(timezone.utc)
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=100.0
    )

    await _maybe_check_budgets(fresh_app_state)
    assert mock_notify.await_count == 0


async def test_budget_global_notifications_disabled_suppresses(fresh_app_state, monkeypatch, state):
    """If the user has globally muted notifications, budget alerts also stay quiet."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    fresh_app_state.config["notifications"]["enabled"] = False

    now = datetime.now(timezone.utc)
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=100.0
    )

    await _maybe_check_budgets(fresh_app_state)
    assert mock_notify.await_count == 0


async def test_budget_zero_dollar_window_skipped(fresh_app_state, monkeypatch, state):
    """Setting weekly_usd=0 means 'not configured' — no division, no alert."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    fresh_app_state.config["budgets"]["weekly_usd"] = 0
    # Disable daily/monthly so the only window evaluated is weekly.
    fresh_app_state.config["budgets"]["daily_usd"] = 0
    fresh_app_state.config["budgets"]["monthly_usd"] = 0

    now = datetime.now(timezone.utc)
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=100.0
    )

    await _maybe_check_budgets(fresh_app_state)
    assert mock_notify.await_count == 0
    assert "weekly" not in fresh_app_state.notified_budget_approaching


async def test_budget_check_noop_when_state_is_none(monkeypatch):
    """Bare AppState (no DB attached) must not raise — just exit cleanly."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    s = AppState(config=_budgets_cfg(), state=None)
    # Should not raise.
    await _maybe_check_budgets(s)
    assert mock_notify.await_count == 0


# --- daily window scope ----------------------------------------------------


async def test_daily_budget_computed_from_last_24h_only(fresh_app_state, monkeypatch, state):
    """Old rows (>24h) must not contribute to the daily budget calculation."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    # Only the daily window is configured.
    fresh_app_state.config["budgets"]["weekly_usd"] = 0
    fresh_app_state.config["budgets"]["monthly_usd"] = 0

    now = datetime.now(timezone.utc)
    # Inside 24h: $1 → 20% of $5.
    await _insert_ended(
        state, pid=1, started_at=now - timedelta(hours=3), ended_at=now - timedelta(hours=2), cost=1.00
    )
    # Older than 24h: $20 — must NOT count.
    await _insert_ended(
        state, pid=2, started_at=now - timedelta(hours=48), ended_at=now - timedelta(hours=40), cost=20.00
    )

    await _maybe_check_budgets(fresh_app_state)
    assert mock_notify.await_count == 0
    assert "daily" not in fresh_app_state.notified_budget_approaching


# --- notification formatting ----------------------------------------------


def test_format_budget_notification_approaching_title():
    title, subtitle, message = _format_budget_notification(
        "daily", spent=4.00, budget=5.00, pct=80.0, tier="approaching"
    )
    assert "Daily budget at 80%" in title
    assert "Spent $4.00 of $5.00 budget" == subtitle
    assert "80%" in message and "daily" in message


def test_format_budget_notification_exceeded_title():
    title, subtitle, message = _format_budget_notification(
        "weekly", spent=35.50, budget=30.00, pct=118.0, tier="exceeded"
    )
    assert "Weekly budget exceeded" in title
    assert "Spent $35.50 of $30.00 budget" == subtitle


def test_format_budget_notification_handles_monthly():
    title, _, _ = _format_budget_notification(
        "monthly", spent=120.0, budget=100.0, pct=120.0, tier="exceeded"
    )
    assert "Monthly" in title


# --- /api/budgets endpoint -------------------------------------------------


@pytest.fixture
async def api_app(tmp_path):
    """FastAPI app with only the budgets router + a real State + real config."""
    st = State(tmp_path / "state.db")
    await st.connect()
    await st.init_db()
    fastapi_app = FastAPI()
    fastapi_app.include_router(budgets_api.router)
    fastapi_app.state.s = AppState(config=_budgets_cfg(), state=st)
    with TestClient(fastapi_app, base_url="http://127.0.0.1") as client:
        yield client, fastapi_app, st
    await st.close()


async def test_budgets_endpoint_returns_three_windows(api_app):
    client, _, _ = api_app
    r = client.get("/api/budgets")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is True
    assert data["warn_at_percent"] == 80.0
    names = [w["window"] for w in data["windows"]]
    assert names == ["daily", "weekly", "monthly"]


async def test_budgets_endpoint_reports_spend_and_percent(api_app):
    client, _, st = api_app
    now = datetime.now(timezone.utc)
    await _insert_ended(
        st, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=2.50
    )
    r = client.get("/api/budgets")
    data = r.json()
    daily = next(w for w in data["windows"] if w["window"] == "daily")
    assert daily["spent_usd"] == pytest.approx(2.50)
    assert daily["budget_usd"] == pytest.approx(5.00)
    assert daily["percent"] == pytest.approx(50.0)


async def test_budgets_endpoint_zeroes_out_on_non_api_plan(api_app):
    client, app, st = api_app
    app.state.s.config["plan"] = "max"
    now = datetime.now(timezone.utc)
    await _insert_ended(
        st, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=100.00
    )
    r = client.get("/api/budgets")
    data = r.json()
    for w in data["windows"]:
        assert w["spent_usd"] == 0.0
        assert w["percent"] == 0.0


# ---------------------------------------------------------------------------
# #143: plan gate must be case-insensitive in /api/budgets too.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plan_value", ["API", "Api", "ApI", "  api  "])
async def test_budgets_endpoint_plan_case_insensitive(api_app, plan_value):
    """Mixed-case ``plan`` values must be treated as 'api' (real cost shown)."""
    client, app, st = api_app
    app.state.s.config["plan"] = plan_value
    now = datetime.now(timezone.utc)
    await _insert_ended(
        st, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=2.50
    )
    r = client.get("/api/budgets")
    assert r.status_code == 200
    data = r.json()
    daily = next(w for w in data["windows"] if w["window"] == "daily")
    assert daily["spent_usd"] == pytest.approx(2.50)
    assert daily["percent"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# #145: _maybe_check_budgets must not advance the rate-limit clock when the
# gates (budgets disabled, non-api plan, notifications muted, no DB) fire.
# Otherwise the user who just enabled budgets has to wait up to 60s for the
# next evaluation.
# ---------------------------------------------------------------------------


async def test_maybe_check_budgets_does_not_advance_clock_when_disabled(monkeypatch, state):
    """Two quick calls with budgets disabled must NOT update last_budget_check_at."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    cfg = _budgets_cfg()
    cfg["budgets"]["enabled"] = False
    s = AppState(config=cfg, state=state)
    s.last_budget_check_at = -1_000_000.0
    sentinel = s.last_budget_check_at

    await _maybe_check_budgets(s)
    assert s.last_budget_check_at == sentinel, "clock advanced even though budgets are disabled"

    await _maybe_check_budgets(s)
    assert s.last_budget_check_at == sentinel, "clock advanced on 2nd call with budgets disabled"
    assert mock_notify.await_count == 0


async def test_maybe_check_budgets_does_not_advance_clock_when_non_api_plan(monkeypatch, state):
    """Non-api plan must short-circuit BEFORE the rate-limit clock update."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    cfg = _budgets_cfg()
    cfg["plan"] = "max"
    s = AppState(config=cfg, state=state)
    s.last_budget_check_at = -1_000_000.0
    sentinel = s.last_budget_check_at

    await _maybe_check_budgets(s)
    assert s.last_budget_check_at == sentinel
    assert mock_notify.await_count == 0


async def test_maybe_check_budgets_does_not_advance_clock_when_notifications_muted(
    monkeypatch, state
):
    """Globally muted notifications must short-circuit BEFORE the clock update."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    cfg = _budgets_cfg()
    cfg["notifications"]["enabled"] = False
    s = AppState(config=cfg, state=state)
    s.last_budget_check_at = -1_000_000.0
    sentinel = s.last_budget_check_at

    await _maybe_check_budgets(s)
    assert s.last_budget_check_at == sentinel
    assert mock_notify.await_count == 0


async def test_maybe_check_budgets_advances_clock_when_work_runs(monkeypatch, state):
    """Sanity-check: when all gates pass, the clock IS advanced (cooldown active)."""
    mock_notify = AsyncMock()
    monkeypatch.setattr("backend.server.notify", mock_notify)
    s = AppState(config=_budgets_cfg(), state=state)
    s.last_budget_check_at = -1_000_000.0
    sentinel = s.last_budget_check_at

    await _maybe_check_budgets(s)
    assert s.last_budget_check_at != sentinel, "clock should advance when gates pass"


# ---------------------------------------------------------------------------
# #146: /api/budgets must run the three cost_in_window calls concurrently via
# asyncio.gather, not serially.
# ---------------------------------------------------------------------------


async def test_budgets_endpoint_uses_gather_for_concurrency(api_app, monkeypatch):
    """All three cost_in_window calls should be scheduled concurrently.

    We assert this indirectly by tracking how many calls were in-flight at
    once: with a serial `await` chain, every call resolves before the next
    one starts (max concurrency = 1). With `asyncio.gather`, all three are
    pending at once (max concurrency = 3).
    """
    import asyncio as _aio

    client, app, st = api_app
    in_flight = 0
    max_in_flight = 0
    call_lock = _aio.Lock()

    async def tracked_cost_in_window(hours):
        nonlocal in_flight, max_in_flight
        async with call_lock:
            in_flight += 1
            if in_flight > max_in_flight:
                max_in_flight = in_flight
        # Yield enough so the other gather()-scheduled calls have a chance to
        # enter this function before any returns.
        await _aio.sleep(0.01)
        async with call_lock:
            in_flight -= 1
        return 1.0 + hours  # any non-zero number — the response is just shape-checked

    app.state.s.state.cost_in_window = tracked_cost_in_window

    r = client.get("/api/budgets")
    assert r.status_code == 200
    data = r.json()
    # 3 windows × non-zero spend → all 3 should report the mocked spend.
    spend_values = sorted(w["spent_usd"] for w in data["windows"])
    assert spend_values == sorted([1.0 + 24, 1.0 + 168, 1.0 + 720])
    assert max_in_flight >= 2, (
        f"expected concurrent cost_in_window calls, max_in_flight={max_in_flight}"
    )


async def test_budgets_endpoint_payload_unchanged_after_gather(api_app):
    """Regression: refactor to asyncio.gather must not alter the JSON shape."""
    client, _, st = api_app
    now = datetime.now(timezone.utc)
    await _insert_ended(
        st, pid=1, started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1), cost=2.50
    )
    r = client.get("/api/budgets")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is True
    assert data["warn_at_percent"] == 80.0
    assert [w["window"] for w in data["windows"]] == ["daily", "weekly", "monthly"]
    assert [w["hours"] for w in data["windows"]] == [24, 168, 720]
    daily = next(w for w in data["windows"] if w["window"] == "daily")
    assert daily["spent_usd"] == pytest.approx(2.50)
    assert daily["budget_usd"] == pytest.approx(5.00)
    assert daily["percent"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# #148: state.cost_in_window — empty DB returns 0.0 via COALESCE, not via a
# dead `if not rows` branch. Lock in #148's removal of the dead branch.
# ---------------------------------------------------------------------------


async def test_cost_in_window_empty_db_returns_zero_via_coalesce(state):
    """COALESCE(SUM(...), 0) returns one row with cost=0 on empty input.

    Locks in #148's removal of the dead `if not rows` branch — the function
    must still return 0.0 cleanly when the sessions table is empty.
    """
    # Sanity: no rows inserted.
    rows = await state._conn.execute_fetchall("SELECT COUNT(*) AS n FROM sessions")
    assert dict(rows[0])["n"] == 0
    assert await state.cost_in_window(24) == 0.0
    assert await state.cost_in_window(720) == 0.0
