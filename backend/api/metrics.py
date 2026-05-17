"""Metrics endpoints — internal scheduler/detector counters.

Exposes ``AppState.metrics`` (populated by ``backend/server.py``) in two
forms:

* ``GET /api/metrics`` — JSON with derived fields (uptime, averages)
* ``GET /api/metrics.prom`` — Prometheus text exposition format

No auth — same as ``/api/admin/status``. The daemon only binds to 127.0.0.1
and the TrustedHostMiddleware rejects non-loopback Host headers.

The Prometheus output is written by hand (no ``prometheus_client``
dependency) since the metric surface is tiny and the format is trivial.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api")


def _state(request: Request):
    return request.app.state.s


# Mapping of metric name → (HELP text, Prometheus type). Counters end in
# ``_total``, gauges don't — matches the Prometheus naming convention.
_METRIC_META: dict[str, tuple[str, str]] = {
    "scheduler_ticks_total": ("Number of scheduler tick iterations", "counter"),
    "scheduler_tick_duration_ms_sum": (
        "Cumulative scheduler tick duration in milliseconds",
        "counter",
    ),
    "scheduler_tick_duration_ms_max": (
        "Maximum observed scheduler tick duration in milliseconds",
        "gauge",
    ),
    "iterm_refresh_total": ("Number of iTerm refresh iterations", "counter"),
    "iterm_refresh_duration_ms_sum": (
        "Cumulative iTerm refresh duration in milliseconds",
        "counter",
    ),
    "iterm_refresh_failures_total": ("Number of failed iTerm refresh iterations", "counter"),
    "broadcasts_total": ("Number of events broadcast to SSE subscribers", "counter"),
    "sse_subscribers": ("Currently connected SSE subscribers", "gauge"),
    "detector_failures_total": ("Number of failed scheduler/detector iterations", "counter"),
    "process_scan_last_count": ("Sessions observed on the most recent scheduler tick", "gauge"),
}


def _metrics_payload(s) -> dict:
    """Snapshot the Metrics dataclass + derived fields into a plain dict."""
    m = s.metrics
    base = asdict(m)
    # asdict() leaves datetime alone; serialize for JSON.
    started_at: datetime = base.pop("started_at")
    base["started_at"] = started_at.isoformat().replace("+00:00", "Z")

    now = datetime.now(timezone.utc)
    base["uptime_seconds"] = max(0, int((now - started_at).total_seconds()))

    # Guard div-by-zero: if no ticks have run yet, avg is just 0.0.
    ticks = base["scheduler_ticks_total"]
    base["scheduler_tick_duration_ms_avg"] = (
        base["scheduler_tick_duration_ms_sum"] / ticks if ticks > 0 else 0.0
    )
    refreshes = base["iterm_refresh_total"]
    base["iterm_refresh_duration_ms_avg"] = (
        base["iterm_refresh_duration_ms_sum"] / refreshes if refreshes > 0 else 0.0
    )
    return base


@router.get("/metrics")
async def get_metrics(request: Request) -> dict:
    """Return the metrics snapshot as JSON with derived fields."""
    s = _state(request)
    return _metrics_payload(s)


def _format_prom(payload: dict) -> str:
    """Render the metrics dict as Prometheus text exposition format.

    Skips derived/string fields (``started_at``, ``uptime_seconds``, the avg
    helpers — Prometheus prefers to compute averages from ``_sum`` / ``_total``
    pairs at query time via ``rate()``).
    """
    lines: list[str] = []
    for name, (help_text, kind) in _METRIC_META.items():
        if name not in payload:
            continue
        value = payload[name]
        # Ints render without ".0"; floats with full repr. Both are valid.
        if isinstance(value, bool):
            num = 1 if value else 0
        else:
            num = value
        full = f"claudewatch_{name}"
        lines.append(f"# HELP {full} {help_text}")
        lines.append(f"# TYPE {full} {kind}")
        lines.append(f"{full} {num}")
    # Also expose uptime as a gauge — useful for restart detection in Grafana.
    uptime = payload.get("uptime_seconds")
    if uptime is not None:
        lines.append("# HELP claudewatch_uptime_seconds Daemon uptime in seconds")
        lines.append("# TYPE claudewatch_uptime_seconds gauge")
        lines.append(f"claudewatch_uptime_seconds {uptime}")
    # Prometheus parsers require a trailing newline.
    return "\n".join(lines) + "\n"


@router.get("/metrics.prom", response_class=PlainTextResponse)
async def get_metrics_prom(request: Request) -> PlainTextResponse:
    """Prometheus text exposition format. ``text/plain; version=0.0.4``."""
    s = _state(request)
    payload = _metrics_payload(s)
    body = _format_prom(payload)
    return PlainTextResponse(content=body, media_type="text/plain; version=0.0.4")
