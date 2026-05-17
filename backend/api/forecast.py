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
    """Project spend over the next 24h / 7d / 30d using the trailing window."""
    s = _state(request)
    if s.state is None or s.state._conn is None:
        return _empty(window_hours)

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    # SUM/COUNT aggregates always return exactly one row (NULL/0 on an empty
    # input), so ``rows`` is guaranteed length 1. COALESCE(SUM(...), 0) guards
    # against NULL when the table has no matching rows; no need for a fallback
    # branch on ``not rows``.
    rows = await s.state._conn.execute_fetchall(
        """
        SELECT COALESCE(SUM(cost_estimate), 0) AS cost,
               COUNT(*) AS n
        FROM sessions
        WHERE ended_at IS NOT NULL AND ended_at >= ?
        """,
        (cutoff,),
    )
    row = dict(rows[0])
    observed_cost = float(row.get("cost") or 0.0)
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
