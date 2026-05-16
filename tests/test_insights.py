"""Tests for the insights / analytics + export endpoints.

These build a minimal FastAPI app that mounts only the insights router and
attaches a real (tmp_path) ``State`` so we can exercise the SQLite path.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import insights
from backend.models import ClaudeSession, TokenUsage
from backend.server import AppState
from backend.state import State


def _make_session(pid: int, cwd: str, *, tokens: int = 1500, cost: float = 0.5) -> ClaudeSession:
    now = datetime.now(timezone.utc)
    return ClaudeSession(
        pid=pid,
        cwd=cwd,
        started_at=now,
        last_activity_at=now,
        model="claude-opus-4-7",
        usage=TokenUsage(
            input_tokens=tokens // 2,
            output_tokens=tokens // 2,
            cost_estimate_usd=cost,
        ),
    )


@pytest.fixture
async def app(tmp_path):
    """A FastAPI app with only the insights router + a real State."""
    state = State(tmp_path / "state.db")
    await state.connect()
    await state.init_db()
    app = FastAPI()
    app.include_router(insights.router)
    app.state.s = AppState(config={}, state=state)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app, state
    await state.close()


async def _insert_ended(
    state: State,
    *,
    pid: int,
    cwd: str,
    started_at: datetime,
    ended_at: datetime,
    tokens: int,
    cost: float,
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


async def test_projects_groups_by_cwd(app):
    client, fastapi_app, _ = app
    a = _make_session(pid=1, cwd="/x", tokens=1000, cost=1.0)
    b = _make_session(pid=2, cwd="/y", tokens=4000, cost=2.5)
    fastapi_app.state.s.sessions = {a.pid: a, b.pid: b}

    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    by_cwd = {p["cwd"]: p for p in data}
    assert by_cwd["/x"]["active_sessions"] == 1
    assert by_cwd["/x"]["sessions_24h"] == 1
    assert by_cwd["/x"]["total_tokens_24h"] == 1000
    assert by_cwd["/x"]["total_cost_24h"] == pytest.approx(1.0)
    assert by_cwd["/y"]["total_tokens_24h"] == 4000
    # Sort: higher total_cost_24h first.
    assert data[0]["cwd"] == "/y"


async def test_projects_combines_active_and_historical(app):
    client, fastapi_app, state = app
    cwd = "/Users/me/Projects/combined"
    active = _make_session(pid=100, cwd=cwd, tokens=2000, cost=0.5)
    fastapi_app.state.s.sessions = {active.pid: active}

    now = datetime.now(timezone.utc)
    await _insert_ended(
        state,
        pid=200,
        cwd=cwd,
        started_at=now - timedelta(hours=3),
        ended_at=now - timedelta(hours=2),
        tokens=3000,
        cost=1.0,
    )
    await _insert_ended(
        state,
        pid=201,
        cwd=cwd,
        started_at=now - timedelta(hours=5),
        ended_at=now - timedelta(hours=4),
        tokens=1500,
        cost=0.25,
    )

    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    p = data[0]
    assert p["cwd"] == cwd
    assert p["active_sessions"] == 1
    assert p["sessions_24h"] == 3  # 1 active + 2 ended
    assert p["total_tokens_24h"] == 2000 + 3000 + 1500
    assert p["total_cost_24h"] == pytest.approx(0.5 + 1.0 + 0.25)


async def test_hourly_history_buckets_correctly(app):
    client, _, state = app
    # Anchor relative to the current hour so the bins always fall in-window.
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # Three distinct hour buckets (1h, 3h, 5h ago).
    await _insert_ended(
        state,
        pid=10,
        cwd="/a",
        started_at=now - timedelta(hours=1, minutes=30),
        ended_at=now - timedelta(hours=1, minutes=5),
        tokens=100,
        cost=0.1,
    )
    await _insert_ended(
        state,
        pid=11,
        cwd="/a",
        started_at=now - timedelta(hours=3, minutes=20),
        ended_at=now - timedelta(hours=3, minutes=5),
        tokens=200,
        cost=0.2,
    )
    await _insert_ended(
        state,
        pid=12,
        cwd="/a",
        started_at=now - timedelta(hours=5, minutes=40),
        ended_at=now - timedelta(hours=5, minutes=10),
        tokens=300,
        cost=0.3,
    )

    r = client.get("/api/history/hourly?hours=24")
    assert r.status_code == 200
    bins = r.json()["bins"]
    assert len(bins) == 24
    non_zero = [b for b in bins if b["tokens"] > 0 or b["sessions_started"] > 0]
    assert len(non_zero) == 3
    assert sum(b["tokens"] for b in bins) == 600
    assert sum(b["cost"] for b in bins) == pytest.approx(0.6)
    assert sum(b["sessions_started"] for b in bins) == 3
    # Oldest first.
    parsed = [b["hour"] for b in bins]
    assert parsed == sorted(parsed)


async def test_hourly_history_caps_hours(app):
    client, _, _ = app
    r = client.get("/api/history/hourly?hours=999")
    assert r.status_code == 422


async def test_export_session_returns_json_download(app):
    client, fastapi_app, _ = app
    sess = _make_session(pid=4242, cwd="/exp")
    fastapi_app.state.s.sessions = {sess.pid: sess}

    r = client.get(f"/api/sessions/{sess.pid}/export")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert f"session-{sess.pid}-" in cd
    assert cd.endswith('.json"')
    body = json.loads(r.text)
    assert body["pid"] == sess.pid
    assert body["cwd"] == "/exp"


async def test_export_session_404_when_missing(app):
    client, _, _ = app
    r = client.get("/api/sessions/9999/export")
    assert r.status_code == 404


async def test_export_csv_returns_text_csv(app):
    client, _, state = app
    now = datetime.now(timezone.utc)
    await _insert_ended(
        state,
        pid=900,
        cwd="/csv",
        started_at=now - timedelta(hours=2),
        ended_at=now - timedelta(hours=1),
        tokens=4242,
        cost=0.42,
        model="claude-opus-4-7",
    )

    r = client.get("/api/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers.get("content-disposition", "")

    lines = r.text.strip().splitlines()
    assert lines[0] == "pid,started_at,ended_at,cwd,model,total_tokens,cost_estimate_usd,last_seen"
    assert len(lines) == 2
    row = lines[1].split(",")
    assert row[0] == "900"
    assert row[3] == "/csv"
    assert row[4] == "claude-opus-4-7"
    assert row[5] == "4242"
    assert row[6] == "0.42"


async def test_export_csv_caps_days(app):
    client, _, _ = app
    r = client.get("/api/export.csv?days=999")
    assert r.status_code == 422
