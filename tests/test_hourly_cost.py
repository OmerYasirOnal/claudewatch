"""Tests for the per-hour cost-trend endpoint (`GET /api/history/hourly-cost`).

Mounts only the insights router against a real (tmp_path) ``State``, then
drives the endpoint via FastAPI's ``TestClient``. No external I/O.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import insights
from backend.server import AppState
from backend.state import State


@pytest.fixture
async def app(tmp_path):
    """A FastAPI app with only the insights router + a real State."""
    state = State(tmp_path / "state.db")
    await state.connect()
    await state.init_db()
    fastapi_app = FastAPI()
    fastapi_app.include_router(insights.router)
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


async def test_hourly_cost_empty_db_returns_continuous_bins(app):
    """No rows → still emit the full window of zero-cost bins + zero total."""
    client, _, _ = app
    r = client.get("/api/history/hourly-cost?hours=24")
    assert r.status_code == 200
    data = r.json()
    assert data["hours"] == 24
    assert data["total_cost_usd"] == 0.0
    assert isinstance(data["bins"], list)
    assert len(data["bins"]) == 24
    for b in data["bins"]:
        assert b["cost_usd"] == 0.0
        assert b["session_count"] == 0
        # hour_start must be an aware ISO 8601 timestamp.
        parsed = datetime.fromisoformat(b["hour_start"])
        assert parsed.tzinfo is not None


async def test_hourly_cost_default_window_is_168h(app):
    """No ``hours`` query param → 7-day (168h) window of continuous bins."""
    client, _, _ = app
    r = client.get("/api/history/hourly-cost")
    assert r.status_code == 200
    data = r.json()
    assert data["hours"] == 168
    assert len(data["bins"]) == 168


async def test_hourly_cost_buckets_by_hour(app):
    """Sessions ended in distinct hours bucket separately; total sums match."""
    client, _, state = app
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    await _insert_ended(
        state,
        pid=1,
        started_at=now - timedelta(hours=1, minutes=30),
        ended_at=now - timedelta(hours=1, minutes=5),
        cost=0.10,
    )
    await _insert_ended(
        state,
        pid=2,
        started_at=now - timedelta(hours=1, minutes=40),
        ended_at=now - timedelta(hours=1, minutes=20),
        cost=0.20,
    )
    await _insert_ended(
        state,
        pid=3,
        started_at=now - timedelta(hours=5, minutes=20),
        ended_at=now - timedelta(hours=5, minutes=5),
        cost=0.50,
    )

    r = client.get("/api/history/hourly-cost?hours=24")
    assert r.status_code == 200
    data = r.json()
    assert len(data["bins"]) == 24
    assert data["total_cost_usd"] == pytest.approx(0.80, abs=1e-6)
    non_zero = [b for b in data["bins"] if b["session_count"] > 0]
    assert len(non_zero) == 2
    by_count = {b["session_count"]: b for b in non_zero}
    assert by_count[2]["cost_usd"] == pytest.approx(0.30, abs=1e-6)
    assert by_count[1]["cost_usd"] == pytest.approx(0.50, abs=1e-6)


async def test_hourly_cost_excludes_rows_outside_window(app):
    """Sessions ended before the window starts must not contribute."""
    client, _, state = app
    now = datetime.now(timezone.utc)
    await _insert_ended(
        state,
        pid=10,
        started_at=now - timedelta(hours=2),
        ended_at=now - timedelta(hours=1),
        cost=0.42,
    )
    # Well outside a 6-hour window.
    await _insert_ended(
        state,
        pid=11,
        started_at=now - timedelta(hours=200),
        ended_at=now - timedelta(hours=198),
        cost=999.0,
    )

    r = client.get("/api/history/hourly-cost?hours=6")
    assert r.status_code == 200
    data = r.json()
    assert data["hours"] == 6
    assert len(data["bins"]) == 6
    assert data["total_cost_usd"] == pytest.approx(0.42, abs=1e-6)


async def test_hourly_cost_continuous_bins_ordered_oldest_first(app):
    """Empty hours stay in the response so the chart has a stable x-axis."""
    client, _, state = app
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # Only one bin in the middle of a 24h window has data.
    await _insert_ended(
        state,
        pid=1,
        started_at=now - timedelta(hours=10),
        ended_at=now - timedelta(hours=10) + timedelta(minutes=10),
        cost=1.25,
    )

    r = client.get("/api/history/hourly-cost?hours=24")
    data = r.json()
    bins = data["bins"]
    assert len(bins) == 24
    # Strictly increasing hour_start values.
    starts = [datetime.fromisoformat(b["hour_start"]) for b in bins]
    assert starts == sorted(starts)
    # Exactly one bin has the cost, the rest are zero (continuous axis).
    non_zero = [b for b in bins if b["cost_usd"] > 0]
    assert len(non_zero) == 1
    assert non_zero[0]["cost_usd"] == pytest.approx(1.25, abs=1e-6)


async def test_hourly_cost_rejects_out_of_range_hours(app):
    """``hours`` must satisfy 1 <= hours <= 720, else 422."""
    client, _, _ = app
    assert client.get("/api/history/hourly-cost?hours=0").status_code == 422
    assert client.get("/api/history/hourly-cost?hours=721").status_code == 422
    assert client.get("/api/history/hourly-cost?hours=-1").status_code == 422


async def test_hourly_cost_handles_state_none():
    """When AppState has no DB the endpoint returns an empty response, not 500."""
    fastapi_app = FastAPI()
    fastapi_app.include_router(insights.router)
    fastapi_app.state.s = AppState(config={}, state=None)
    with TestClient(fastapi_app, base_url="http://127.0.0.1") as client:
        r = client.get("/api/history/hourly-cost?hours=168")
        assert r.status_code == 200
        data = r.json()
        assert data == {"hours": 168, "bins": [], "total_cost_usd": 0.0}


async def test_hourly_cost_ignores_active_sessions(app):
    """Active (ended_at IS NULL) rows must not contribute."""
    client, _, state = app
    now = datetime.now(timezone.utc)
    await state.connect()
    assert state._conn is not None
    await state._conn.execute(
        """
        INSERT INTO sessions (pid, started_at, ended_at, last_seen, cwd, model,
                              total_tokens, cost_estimate, summary_json)
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            99,
            (now - timedelta(hours=1)).isoformat(),
            now.isoformat(),
            "/active",
            "claude-opus-4-7",
            5000,
            10.0,  # huge cost — must NOT contribute
            json.dumps({"pid": 99}),
        ),
    )
    await state._conn.commit()

    r = client.get("/api/history/hourly-cost?hours=24")
    assert r.status_code == 200
    data = r.json()
    assert data["total_cost_usd"] == 0.0
    assert all(b["cost_usd"] == 0.0 for b in data["bins"])
