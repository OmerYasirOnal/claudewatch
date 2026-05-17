"""Budget progress endpoint — current spend over the daily/weekly/monthly windows.

Powers the "Budget progress" widget on the Insights tab. The widget could
make three calls to ``/api/forecast?window_hours=24|168|720`` instead, but
collapsing those into a single endpoint keeps the dashboard polling cheap
and the JSON small (one trip per refresh).

Plan gating (#126): identical to ``/api/forecast`` — dollar amounts only
correspond to a real bill when ``plan == "api"``. On any other plan we
return zeroed-out windows (same shape so the UI degrades cleanly).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")

log = logging.getLogger(__name__)


class BudgetWindow(BaseModel):
    """Current spend + configured cap for one budget window."""

    window: str = Field(..., description="One of 'daily', 'weekly', 'monthly'.")
    hours: int = Field(..., description="Width of the rolling window in hours.")
    budget_usd: float = Field(..., description="Configured cap for the window; 0 = not set.")
    spent_usd: float = Field(..., description="Sum of ended-session cost over the window.")
    percent: float = Field(..., description="spent_usd / budget_usd * 100; 0.0 when budget is 0.")


class BudgetsResponse(BaseModel):
    """Snapshot of all budget windows + the configuration that produced it."""

    enabled: bool = Field(..., description="True iff budgets.enabled is set in config.")
    warn_at_percent: float = Field(..., description="Threshold for the 'approaching' alert tier.")
    windows: list[BudgetWindow] = Field(
        ..., description="One entry per window, in (daily, weekly, monthly) order."
    )


# Mirrors the constant in backend/server.py — keep in sync if you add a window.
_WINDOWS: tuple[tuple[str, str, int], ...] = (
    ("daily", "daily_usd", 24),
    ("weekly", "weekly_usd", 168),
    ("monthly", "monthly_usd", 720),
)


def _state(request: Request):
    return request.app.state.s


def _empty_response(cfg: dict[str, Any]) -> BudgetsResponse:
    """Build an all-zero response when plan-gated or DB unavailable."""
    try:
        warn = float(cfg.get("warn_at_percent", 80))
    except (TypeError, ValueError):
        warn = 80.0
    return BudgetsResponse(
        enabled=bool(cfg.get("enabled")),
        warn_at_percent=warn,
        windows=[
            BudgetWindow(
                window=name,
                hours=hours,
                budget_usd=float(cfg.get(key, 0) or 0),
                spent_usd=0.0,
                percent=0.0,
            )
            for name, key, hours in _WINDOWS
        ],
    )


@router.get("/budgets", response_model=BudgetsResponse)
async def budgets(request: Request) -> BudgetsResponse:
    """Return current spend vs. configured cap for each budget window."""
    s = _state(request)
    cfg = (s.config or {}).get("budgets", {}) or {}
    # #143: lowercase on read so hand-edited config.toml entries like
    # ``plan = "API"`` (which bypass the Pydantic Literal validator) don't
    # silently zero out cost data.
    plan = str((s.config or {}).get("plan", "api") or "api").strip().lower()
    if plan != "api" or s.state is None or s.state._conn is None:
        return _empty_response(cfg)

    try:
        warn = float(cfg.get("warn_at_percent", 80))
    except (TypeError, ValueError):
        warn = 80.0

    # #146: fire all three cost_in_window queries concurrently — they're
    # independent reads against the same aiosqlite connection and serializing
    # them just adds wall-clock latency per poll (the 720h scan in particular
    # is non-trivial on a populated DB). aiosqlite's executor pool handles
    # the parallelism via its internal queue.
    # #149: ``return_exceptions=True`` gives per-window error isolation — a
    # single failing query downgrades that window to 0 instead of 500ing the
    # entire widget, mirroring the scheduler's per-window try/except.
    spent_results = await asyncio.gather(
        *(s.state.cost_in_window(hours) for _, _, hours in _WINDOWS),
        return_exceptions=True,
    )

    windows: list[BudgetWindow] = []
    for (name, key, hours), spent_or_err in zip(_WINDOWS, spent_results, strict=True):
        try:
            budget = float(cfg.get(key, 0) or 0)
        except (TypeError, ValueError):
            budget = 0.0
        if isinstance(spent_or_err, BaseException):
            # #149: a transient DB hiccup on one window must not take down the
            # widget. Log + zero out that window so the others still render.
            log.warning("budgets: cost_in_window(%d) failed: %s", hours, spent_or_err)
            spent = 0.0
        else:
            spent = float(spent_or_err)
        pct = (spent / budget * 100.0) if budget > 0 else 0.0
        windows.append(
            BudgetWindow(
                window=name,
                hours=hours,
                budget_usd=round(budget, 6),
                spent_usd=round(spent, 6),
                percent=round(pct, 4),
            )
        )

    return BudgetsResponse(
        enabled=bool(cfg.get("enabled")),
        warn_at_percent=warn,
        windows=windows,
    )
