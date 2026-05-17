"""Tests for the cost-forecast endpoint (`GET /api/forecast`).

Mirrors ``tests/test_insights.py`` — mounts only the forecast router against
a real (tmp_path) ``State``, then drives the endpoint via FastAPI's
``TestClient``. No external I/O.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import forecast
from backend.server import AppState
from backend.state import State


@pytest.fixture
async def app(tmp_path):
    """A FastAPI app with only the forecast router + a real State."""
    state = State(tmp_path / "state.db")
    await state.connect()
    await state.init_db()
    fastapi_app = FastAPI()
    fastapi_app.include_router(forecast.router)
    fastapi_app.state.s = AppState(config={}, state=state)
    with TestClient(fastapi_app, base_url="http://127.0.0.1") as client:
        yield client, fastapi_app, state
    await state.close()


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
    """Insert a complete (ended) row directly via the underlying connection."""
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


async def test_forecast_empty_db_returns_zeros(app):
    client, _, _ = app
    r = client.get("/api/forecast?window_hours=24")
    assert r.status_code == 200
    data = r.json()
    assert data["window_hours"] == 24
    assert data["observed_cost_usd"] == 0.0
    assert data["observed_session_count"] == 0
    assert data["hourly_rate_usd"] == 0.0
    assert data["projection_24h_usd"] == 0.0
    assert data["projection_7d_usd"] == 0.0
    assert data["projection_30d_usd"] == 0.0
    # as_of must be a UTC-suffixed ISO 8601 timestamp.
    assert isinstance(data["as_of"], str)
    parsed = datetime.fromisoformat(data["as_of"])
    assert parsed.tzinfo is not None


async def test_forecast_sums_and_projects(app):
    client, _, state = app
    now = datetime.now(timezone.utc)
    # Three sessions ended within the trailing 24h window.
    await _insert_ended(
        state,
        pid=1,
        started_at=now - timedelta(hours=2),
        ended_at=now - timedelta(hours=1),
        cost=1.0,
        tokens=1000,
    )
    await _insert_ended(
        state,
        pid=2,
        started_at=now - timedelta(hours=5),
        ended_at=now - timedelta(hours=4),
        cost=2.0,
        tokens=2000,
    )
    await _insert_ended(
        state,
        pid=3,
        started_at=now - timedelta(hours=20),
        ended_at=now - timedelta(hours=18),
        cost=0.4,
        tokens=400,
    )

    r = client.get("/api/forecast?window_hours=24")
    assert r.status_code == 200
    data = r.json()
    assert data["window_hours"] == 24
    assert data["observed_session_count"] == 3
    assert data["observed_cost_usd"] == pytest.approx(3.4, abs=1e-6)
    assert data["hourly_rate_usd"] == pytest.approx(3.4 / 24.0, abs=1e-6)
    assert data["projection_24h_usd"] == pytest.approx(3.4, abs=1e-4)
    assert data["projection_7d_usd"] == pytest.approx(3.4 * 7.0, abs=1e-4)
    assert data["projection_30d_usd"] == pytest.approx(3.4 * 30.0, abs=1e-4)


async def test_forecast_excludes_rows_outside_window(app):
    client, _, state = app
    now = datetime.now(timezone.utc)
    # In window (last 6 hours)
    await _insert_ended(
        state,
        pid=10,
        started_at=now - timedelta(hours=2),
        ended_at=now - timedelta(hours=1),
        cost=0.5,
    )
    # Outside window (ended 10 hours ago)
    await _insert_ended(
        state,
        pid=11,
        started_at=now - timedelta(hours=12),
        ended_at=now - timedelta(hours=10),
        cost=99.0,
    )

    r = client.get("/api/forecast?window_hours=6")
    assert r.status_code == 200
    data = r.json()
    assert data["observed_session_count"] == 1
    assert data["observed_cost_usd"] == pytest.approx(0.5, abs=1e-6)
    assert data["hourly_rate_usd"] == pytest.approx(0.5 / 6.0, abs=1e-6)


async def test_forecast_rejects_out_of_range_window(app):
    client, _, _ = app
    assert client.get("/api/forecast?window_hours=0").status_code == 422
    assert client.get("/api/forecast?window_hours=721").status_code == 422
    assert client.get("/api/forecast?window_hours=-5").status_code == 422


async def test_forecast_handles_state_none():
    """When AppState has no DB the endpoint must return zeros, not 500."""
    fastapi_app = FastAPI()
    fastapi_app.include_router(forecast.router)
    fastapi_app.state.s = AppState(config={}, state=None)
    with TestClient(fastapi_app, base_url="http://127.0.0.1") as client:
        r = client.get("/api/forecast?window_hours=24")
        assert r.status_code == 200
        data = r.json()
        assert data["observed_cost_usd"] == 0.0
        assert data["observed_session_count"] == 0
        assert data["hourly_rate_usd"] == 0.0
        assert data["projection_30d_usd"] == 0.0


async def test_forecast_ignores_active_sessions(app):
    """Active (not-yet-ended) rows must not contribute to the observed window."""
    client, _, state = app
    now = datetime.now(timezone.utc)
    # Insert one row with ended_at=NULL (active session)
    await state.connect()
    assert state._conn is not None
    await state._conn.execute(
        """
        INSERT INTO sessions (pid, started_at, ended_at, last_seen, cwd, model,
                              total_tokens, cost_estimate, summary_json)
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            42,
            (now - timedelta(hours=1)).isoformat(),
            now.isoformat(),
            "/active",
            "claude-opus-4-7",
            5000,
            10.0,  # huge cost — must not contribute
            json.dumps({"pid": 42}),
        ),
    )
    await state._conn.commit()
    # Plus one real ended row in window
    await _insert_ended(
        state,
        pid=43,
        started_at=now - timedelta(hours=3),
        ended_at=now - timedelta(hours=2),
        cost=1.0,
    )

    r = client.get("/api/forecast?window_hours=24")
    assert r.status_code == 200
    data = r.json()
    assert data["observed_session_count"] == 1
    assert data["observed_cost_usd"] == pytest.approx(1.0)


async def test_forecast_custom_window_hours(app):
    """Window parameter scales the hourly_rate and projections."""
    client, _, state = app
    now = datetime.now(timezone.utc)
    await _insert_ended(
        state,
        pid=1,
        started_at=now - timedelta(hours=1),
        ended_at=now - timedelta(minutes=30),
        cost=10.0,
    )
    r = client.get("/api/forecast?window_hours=1")
    assert r.status_code == 200
    data = r.json()
    assert data["window_hours"] == 1
    assert data["observed_cost_usd"] == pytest.approx(10.0)
    assert data["hourly_rate_usd"] == pytest.approx(10.0)
    assert data["projection_24h_usd"] == pytest.approx(240.0)


async def test_forecast_clamps_negative_cost_rows(app):
    """Issue #125: a corrupted row with cost_estimate < 0 must NOT drag the
    observed cost or any projection field below zero. The SQL CASE clause
    drops negative rows to 0 before summation."""
    client, _, state = app
    now = datetime.now(timezone.utc)
    # One legitimate positive-cost session.
    await _insert_ended(
        state,
        pid=1,
        started_at=now - timedelta(hours=2),
        ended_at=now - timedelta(hours=1),
        cost=1.0,
    )
    # Two pathological rows: a "refund" entry and a glitch entry that would,
    # without the clamp, drive the observed total negative.
    await _insert_ended(
        state,
        pid=2,
        started_at=now - timedelta(hours=5),
        ended_at=now - timedelta(hours=4),
        cost=-99.0,
    )
    await _insert_ended(
        state,
        pid=3,
        started_at=now - timedelta(hours=10),
        ended_at=now - timedelta(hours=9),
        cost=-0.5,
    )

    r = client.get("/api/forecast?window_hours=24")
    assert r.status_code == 200
    data = r.json()

    # observed_cost_usd is the SUM of clamped costs — only the +1.0 row
    # contributes, so the answer is exactly 1.0 regardless of the negatives.
    assert data["observed_cost_usd"] == pytest.approx(1.0)
    # All three rows are still counted in observed_session_count (we only
    # clamp the cost, not the row).
    assert data["observed_session_count"] == 3
    # Every derived field must be >= 0.
    assert data["hourly_rate_usd"] >= 0.0
    assert data["projection_24h_usd"] >= 0.0
    assert data["projection_7d_usd"] >= 0.0
    assert data["projection_30d_usd"] >= 0.0


async def test_forecast_all_negative_costs_collapse_to_zero(app):
    """If EVERY row in the window has cost < 0, the response should be all
    zeros — not negative — and the endpoint should not crash on the
    division."""
    client, _, state = app
    now = datetime.now(timezone.utc)
    for pid, cost in [(10, -1.0), (11, -2.5), (12, -0.01)]:
        await _insert_ended(
            state,
            pid=pid,
            started_at=now - timedelta(hours=3),
            ended_at=now - timedelta(hours=1),
            cost=cost,
        )

    r = client.get("/api/forecast?window_hours=24")
    assert r.status_code == 200
    data = r.json()
    assert data["observed_cost_usd"] == 0.0
    assert data["hourly_rate_usd"] == 0.0
    assert data["projection_24h_usd"] == 0.0
    assert data["projection_7d_usd"] == 0.0
    assert data["projection_30d_usd"] == 0.0
    # The rows are still counted — only the cost was clamped.
    assert data["observed_session_count"] == 3
