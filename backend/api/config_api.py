from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from backend.config import load_config, update_config

router = APIRouter(prefix="/api")


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    return load_config()


@router.post("/config")
async def post_config(updates: dict[str, Any], request: Request):
    cfg = update_config(updates)
    request.app.state.s.config = cfg
    return cfg


@router.get("/pricing")
async def get_pricing():
    return load_config().get("pricing", {})


@router.post("/pricing")
async def post_pricing(pricing: dict[str, Any], request: Request):
    cfg = update_config({"pricing": pricing})
    request.app.state.s.config = cfg
    return cfg.get("pricing", {})
