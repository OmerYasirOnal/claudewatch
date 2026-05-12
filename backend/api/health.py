from __future__ import annotations

from fastapi import APIRouter, Request

from backend.permissions import health_report

router = APIRouter(prefix="/api")


@router.get("/health")
async def get_health(request: Request):
    h = await health_report()
    return h.model_dump(mode="json")
