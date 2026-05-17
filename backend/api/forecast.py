"""Cost-forecast endpoint.

Extrapolates spend from the last ``window_hours`` of ended sessions in the
SQLite history. Mirrors the style of ``backend/api/insights.py`` — the
router is registered by ``backend/server.py`` via ``include_router``.

The projections use a flat rolling-window average (``observed / hours``)
rather than an exponential moving average or a per-hour bucketed model.
For a dashboard's "what will this cost me" sticker, the simpler model is
easier to explain and matches what a user would compute on a napkin; the
window itself is the tuning knob (default 24h smooths bursty hours).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")


class ForecastResponse(BaseModel):
    """Cost extrapolation derived from a rolling window of ended sessions."""

    window_hours: int = Field(..., description="Width of the observation window in hours.")
    observed_cost_usd: float = Field(..., description="Sum of cost_estimate over the window.")
    observed_session_count: int = Field(..., description="Number of ended sessions in the window.")
    hourly_rate_usd: float = Field(..., description="observed_cost_usd / window_hours.")
    projection_24h_usd: float = Field(..., description="hourly_rate * 24.")
    projection_7d_usd: float = Field(..., description="hourly_rate * 168.")
    projection_30d_usd: float = Field(..., description="hourly_rate * 720.")
    as_of: str = Field(..., description="UTC ISO 8601 timestamp the projection was computed.")


def _state(request: Request):
    return request.app.state.s


def _empty(window_hours: int) -> ForecastResponse:
    """Build a zeroed-out response so the UI degrades gracefully when there's no DB."""
    return ForecastResponse(
        window_hours=window_hours,
        observed_cost_usd=0.0,
        observed_session_count=0,
        hourly_rate_usd=0.0,
        projection_24h_usd=0.0,
        projection_7d_usd=0.0,
        projection_30d_usd=0.0,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/forecast", response_model=ForecastResponse)
async def forecast(
    request: Request,
    window_hours: int = Query(24, ge=1, le=720),
) -> ForecastResponse:
    """Project spend over the next 24h / 7d / 30d using the trailing window.

    Negative ``cost_estimate`` rows (e.g. corrupted historical entries, refund
    adjustments, or out-of-tree DB writes) are clamped to zero at the SQL
    layer via ``CASE WHEN cost_estimate > 0 ...``. ``observed_cost_usd`` and
    all projection fields are therefore guaranteed to be ``>= 0`` regardless
    of the underlying row contents — see issue #125. If the clamped sum is
    zero, ``hourly_rate_usd`` and every projection collapse to ``0.0``.
    """
    s = _state(request)
    if s.state is None or s.state._conn is None:
        return _empty(window_hours)

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    # Issue #125: clamp negative cost_estimate rows to 0 inside the aggregation
    # so a single bad row can't drive the entire projection negative. We also
    # belt-and-suspenders with `max(0.0, ...)` below in case a future SQL
    # rewrite drops the CASE.
    rows = await s.state._conn.execute_fetchall(
        """
        SELECT COALESCE(SUM(CASE WHEN cost_estimate > 0 THEN cost_estimate ELSE 0 END), 0) AS cost,
               COUNT(*) AS n
        FROM sessions
        WHERE ended_at IS NOT NULL AND ended_at >= ?
        """,
        (cutoff,),
    )
    if not rows:
        return _empty(window_hours)

    row = dict(rows[0])
    observed_cost = max(0.0, float(row.get("cost") or 0.0))
    observed_count = int(row.get("n") or 0)
    hourly_rate = observed_cost / float(window_hours) if window_hours > 0 else 0.0

    return ForecastResponse(
        window_hours=window_hours,
        observed_cost_usd=round(observed_cost, 6),
        observed_session_count=observed_count,
        hourly_rate_usd=round(hourly_rate, 6),
        projection_24h_usd=round(hourly_rate * 24.0, 6),
        projection_7d_usd=round(hourly_rate * 24.0 * 7.0, 6),
        projection_30d_usd=round(hourly_rate * 24.0 * 30.0, 6),
        as_of=datetime.now(timezone.utc).isoformat(),
    )
