"""Insights / analytics + export endpoints.

Powers the dashboard's Insights tab and the per-session/CSV download buttons.
Routes are registered by ``backend/server.py`` via ``include_router``.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api")


def _state(request: Request):
    return request.app.state.s


def _max_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b


def _parse_iso(value: Any) -> datetime | None:
    """Best-effort ISO 8601 -> aware datetime. Returns None on garbage."""
    if not isinstance(value, str) or not value:
        return None
    try:
        # fromisoformat handles "+00:00" suffix; normalize trailing "Z".
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/projects")
async def list_projects(request: Request) -> list[dict]:
    """Per-cwd roll-up combining live active sessions + ended sessions in 24h."""
    s = _state(request)
    # cwd -> aggregated stats
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "active_sessions": 0,
            "sessions_24h": 0,
            "total_tokens_24h": 0,
            "total_cost_24h": 0.0,
            "last_active_at": None,
        }
    )

    # Active sessions — count, add tokens/cost, track last_activity_at.
    for sess in s.sessions.values():
        cwd = sess.cwd or ""
        bucket = agg[cwd]
        bucket["active_sessions"] += 1
        bucket["sessions_24h"] += 1
        if sess.usage:
            bucket["total_tokens_24h"] += int(sess.usage.total_tokens or 0)
            bucket["total_cost_24h"] += float(sess.usage.cost_estimate_usd or 0.0)
        last = sess.last_activity_at or sess.started_at
        bucket["last_active_at"] = _max_dt(bucket["last_active_at"], last)

    # Ended sessions in last 24h.
    if s.state is not None:
        try:
            historical = await s.state.list_history(hours=24)
        except Exception:  # noqa: BLE001
            historical = []
        for row in historical:
            cwd = row.get("cwd") or ""
            bucket = agg[cwd]
            bucket["sessions_24h"] += 1
            bucket["total_tokens_24h"] += int(row.get("total_tokens") or 0)
            bucket["total_cost_24h"] += float(row.get("cost_estimate") or 0.0)
            last = _parse_iso(row.get("last_seen")) or _parse_iso(row.get("ended_at"))
            bucket["last_active_at"] = _max_dt(bucket["last_active_at"], last)

    out: list[dict] = []
    for cwd, bucket in agg.items():
        last = bucket["last_active_at"]
        out.append(
            {
                "cwd": cwd,
                "active_sessions": bucket["active_sessions"],
                "sessions_24h": bucket["sessions_24h"],
                "total_tokens_24h": int(bucket["total_tokens_24h"]),
                "total_cost_24h": round(float(bucket["total_cost_24h"]), 6),
                "last_active_at": (
                    last.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    if isinstance(last, datetime)
                    else None
                ),
            }
        )
    out.sort(key=lambda p: p["total_cost_24h"], reverse=True)
    return out


@router.get("/history/hourly")
async def hourly_history(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
) -> dict:
    """Time-series bins (one per hour) for the trailing ``hours`` window."""
    s = _state(request)
    if s.state is None:
        return {"bins": []}
    bins = await s.state.hourly_history(hours=hours)
    return {"bins": bins}


@router.get("/history/hourly-cost")
async def hourly_cost(
    request: Request,
    hours: int = Query(168, ge=1, le=720),
) -> dict:
    """Per-hour cost trend over the trailing ``hours`` window (default 7 days).

    Returns a continuous time axis — empty hours appear as zero-cost bins so
    the frontend can render a stable x-axis without gap handling.
    """
    s = _state(request)
    if s.state is None:
        return {"hours": hours, "bins": [], "total_cost_usd": 0.0}
    bins = await s.state.hourly_cost(hours=hours)
    total = round(sum(float(b.get("cost_usd") or 0.0) for b in bins), 6)
    return {"hours": hours, "bins": bins, "total_cost_usd": total}


@router.get("/sessions/{pid}/export")
async def export_session(pid: int, request: Request):
    """Download the full active-session snapshot as a JSON attachment."""
    s = _state(request)
    sess = s.sessions.get(pid)
    if not sess:
        raise HTTPException(404, "session not found")
    payload = json.dumps(sess.model_dump(mode="json"), indent=2, default=str)
    fname = f"session-{pid}-{sess.started_at.strftime('%Y%m%dT%H%M%S')}.json"
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/export.csv")
async def export_csv(
    request: Request,
    days: int = Query(7, ge=1, le=30),
):
    """Dump historical sessions over the last ``days`` as CSV for spreadsheets."""
    s = _state(request)
    rows: list[dict] = []
    if s.state is not None:
        try:
            rows = await s.state.list_history(hours=days * 24)
        except Exception:  # noqa: BLE001
            rows = []

    columns = [
        "pid",
        "started_at",
        "ended_at",
        "cwd",
        "model",
        "total_tokens",
        "cost_estimate_usd",
        "last_seen",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(
            [
                row.get("pid", ""),
                row.get("started_at", "") or "",
                row.get("ended_at", "") or "",
                row.get("cwd", "") or "",
                row.get("model", "") or "",
                row.get("total_tokens", 0) or 0,
                row.get("cost_estimate", "") if row.get("cost_estimate") is not None else "",
                row.get("last_seen", "") or "",
            ]
        )
    body = buf.getvalue()
    fname = f"claudewatch-sessions-{days}d.csv"
    return StreamingResponse(
        iter([body]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
