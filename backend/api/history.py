from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


def _state(request: Request):
    return request.app.state.s


@router.get("/history")
async def list_history(request: Request, hours: int = 24):
    s = _state(request)
    if not s.state:
        return []
    return await s.state.list_history(hours=hours)


@router.get("/stats")
async def stats(request: Request):
    s = _state(request)
    active = len(s.sessions)
    active_tokens = sum(
        (sess.usage.total_tokens if sess.usage else 0) for sess in s.sessions.values()
    )
    active_cost = sum(
        (sess.usage.cost_estimate_usd or 0.0) if sess.usage else 0.0
        for sess in s.sessions.values()
    )
    stats_24h = await s.state.stats_today() if s.state else {}
    return {
        "active": active,
        "active_tokens": active_tokens,
        "active_cost": active_cost,
        **stats_24h,
    }
