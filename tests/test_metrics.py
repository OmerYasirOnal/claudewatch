"""Metrics endpoint tests.

Mirrors ``tests/test_admin.py``: builds a TestClient against ``create_app()``
with the scheduler patched to a no-op so nothing touches real iTerm /
processes / disk.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

# Prometheus line format — comment lines start with '#', sample lines look like
# ``metric{labels} value [timestamp]``. We never emit labels, so the labels
# group is optional and present-but-empty rendering is also accepted.
_PROM_SAMPLE_RE = re.compile(r"^[a-z_][a-z0-9_]*(\{[^}]*\})?\s+[\d.eE+-]+(\s+\d+)?$")


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Build the FastAPI app with a tmp_path state DB and a no-op scheduler."""
    monkeypatch.setattr("backend.config.STATE_DB", tmp_path / "state.db")
    monkeypatch.setattr("backend.server.STATE_DB", tmp_path / "state.db")

    async def _no_scheduler(s):
        return None

    monkeypatch.setattr("backend.server._scheduler_loop", _no_scheduler)
    monkeypatch.setattr("backend.server._iterm_refresh_loop", _no_scheduler)

    from backend.server import create_app

    fastapi_app = create_app()

    with TestClient(fastapi_app, base_url="http://127.0.0.1") as client:
        yield client, fastapi_app


def test_metrics_json_shape_with_zero_counters(app):
    """Fresh daemon — every counter is 0, every gauge is 0, uptime is non-negative."""
    client, _ = app
    r = client.get("/api/metrics")
    assert r.status_code == 200, r.text
    d = r.json()

    expected_keys = {
        "scheduler_ticks_total",
        "scheduler_tick_duration_ms_sum",
        "scheduler_tick_duration_ms_max",
        "scheduler_tick_duration_ms_avg",
        "iterm_refresh_total",
        "iterm_refresh_duration_ms_sum",
        "iterm_refresh_duration_ms_avg",
        "iterm_refresh_failures_total",
        "broadcasts_total",
        "sse_subscribers",
        "detector_failures_total",
        "process_scan_last_count",
        "started_at",
        "uptime_seconds",
    }
    for k in expected_keys:
        assert k in d, f"missing key: {k}"

    # Counters / gauges start at zero on a freshly-created AppState.
    assert d["scheduler_ticks_total"] == 0
    assert d["scheduler_tick_duration_ms_sum"] == 0.0
    assert d["scheduler_tick_duration_ms_max"] == 0.0
    # Div-by-zero guard: avg = 0.0 when no ticks have run.
    assert d["scheduler_tick_duration_ms_avg"] == 0.0
    assert d["iterm_refresh_duration_ms_avg"] == 0.0
    assert d["iterm_refresh_failures_total"] == 0
    assert d["broadcasts_total"] == 0
    assert d["sse_subscribers"] == 0
    assert d["detector_failures_total"] == 0
    assert d["process_scan_last_count"] == 0
    assert isinstance(d["uptime_seconds"], int)
    assert d["uptime_seconds"] >= 0
    assert d["started_at"].endswith("Z")


def test_metrics_counters_increment_after_broadcast(app):
    """Firing ``AppState.broadcast`` bumps ``broadcasts_total`` — which is the
    same code path every session.{started,updated,ended} event flows through."""
    import asyncio

    client, fastapi_app = app
    s = fastapi_app.state.s

    async def _drive() -> None:
        # Three direct broadcasts — same call _emit_diffs makes per event.
        await s.broadcast({"event": "session.started", "pid": 1})
        await s.broadcast({"event": "session.updated", "pid": 1})
        await s.broadcast({"event": "session.ended", "pid": 1})

    asyncio.run(_drive())

    r = client.get("/api/metrics")
    d = r.json()
    assert d["broadcasts_total"] == 3


def test_metrics_avg_divide_by_zero_guarded(app):
    """avg fields stay 0.0 when *_total counters are 0 — never NaN/inf, no crash."""
    client, _ = app
    r = client.get("/api/metrics")
    d = r.json()
    assert d["scheduler_ticks_total"] == 0
    assert d["scheduler_tick_duration_ms_avg"] == 0.0
    assert d["iterm_refresh_total"] == 0
    assert d["iterm_refresh_duration_ms_avg"] == 0.0


def test_metrics_avg_computed_when_ticks_present(app):
    """When ticks have run, avg = sum/ticks (computed server-side, not by the client)."""
    client, fastapi_app = app
    s = fastapi_app.state.s
    s.metrics.scheduler_ticks_total = 4
    s.metrics.scheduler_tick_duration_ms_sum = 10.0

    r = client.get("/api/metrics")
    d = r.json()
    assert d["scheduler_tick_duration_ms_avg"] == pytest.approx(2.5)


def test_metrics_prom_format_is_well_formed(app):
    """Each non-comment line in /api/metrics.prom matches the Prometheus sample regex."""
    client, fastapi_app = app
    # Seed a couple of non-zero values so the regex actually validates numbers
    # other than 0/0.0.
    s = fastapi_app.state.s
    s.metrics.scheduler_ticks_total = 12345
    s.metrics.scheduler_tick_duration_ms_sum = 678.9
    s.metrics.sse_subscribers = 2

    r = client.get("/api/metrics.prom")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert body.endswith("\n")

    # Must contain HELP + TYPE + sample lines for at least one known metric.
    assert "# HELP claudewatch_scheduler_ticks_total" in body
    assert "# TYPE claudewatch_scheduler_ticks_total counter" in body
    assert "claudewatch_scheduler_ticks_total 12345" in body
    # Gauges don't end in _total.
    assert "# TYPE claudewatch_sse_subscribers gauge" in body
    assert "claudewatch_sse_subscribers 2" in body

    for raw in body.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("#"):
            assert line.startswith("# HELP ") or line.startswith("# TYPE "), line
            continue
        assert _PROM_SAMPLE_RE.match(line), f"line not well-formed: {line!r}"


def test_metrics_uptime_increases_monotonically(app, monkeypatch):
    """uptime_seconds derives from ``datetime.now() - started_at``; nudge the
    started_at into the past to confirm uptime grows accordingly."""
    from datetime import datetime, timedelta, timezone

    client, fastapi_app = app
    s = fastapi_app.state.s

    # Initial: uptime is tiny.
    r1 = client.get("/api/metrics").json()
    first_uptime = r1["uptime_seconds"]
    assert first_uptime >= 0

    # Backdate started_at by 60 seconds; next call's uptime should jump.
    s.metrics.started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    r2 = client.get("/api/metrics").json()
    assert r2["uptime_seconds"] >= 60
    assert r2["uptime_seconds"] > first_uptime
