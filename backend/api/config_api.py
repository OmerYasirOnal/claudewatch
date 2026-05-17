from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.config import load_config, update_config

router = APIRouter(prefix="/api")


# #41: explicit allow-list for POST /api/config payloads.
#
# We rely on uvicorn's default request body size limit (~1MB) to bound payload
# size — FastAPI itself doesn't ship a built-in size cap, but the dashboard is
# bound to 127.0.0.1 and any sane reverse proxy would impose its own limit.
class PricingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float = Field(ge=0)
    cache_write: float = Field(ge=0)


class RemoteControlUpdate(BaseModel):
    # Sub-block so callers can flip remote_control.enabled without touching
    # other top-level config. extra="forbid" keeps the surface tight.
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None


class BudgetsConfig(BaseModel):
    # #141: PR #139 shipped the [budgets] block in DEFAULT_CONFIG and a
    # Settings-UI card, but ConfigUpdate uses extra="forbid" without a
    # `budgets` field — so POST /api/config rejected the payload and
    # users couldn't actually save budget changes. This sub-model fixes
    # that. All fields nullable so the UI can do partial updates.
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    daily_usd: float | None = Field(default=None, ge=0)
    weekly_usd: float | None = Field(default=None, ge=0)
    monthly_usd: float | None = Field(default=None, ge=0)
    warn_at_percent: int | None = Field(default=None, ge=1, le=100)


class EditorConfig(BaseModel):
    # Sub-block for the opt-in "open in editor" action used by POST
    # /api/files/open. The pattern below is intentionally narrow: a shell-safe
    # subset of characters (no metacharacters like ;|&$<>) so a localhost
    # attacker can't smuggle a command line through the validator. The real
    # invocation also bypasses the shell (subprocess.Popen with a list argv).
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    command: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_/.\- ]+$",
        max_length=128,
    )


class ConfigUpdate(BaseModel):
    # extra="forbid": unknown keys -> 422. Prevents a localhost attacker (or a
    # DNS-rebound page; see #39) from planting random config keys that future
    # code might honor.
    model_config = ConfigDict(extra="forbid")

    port: int | None = Field(default=None, ge=1024, le=65535)
    read_only: bool | None = None
    privacy_mode: bool | None = None
    show_log_text: bool | None = None
    plan: Literal["api", "pro", "max", "max_20x", "team", "free"] | None = None
    file_change_retention_minutes: int | None = Field(default=None, ge=1, le=1440)
    process_scan_interval_seconds: float | None = Field(default=None, gt=0, le=60)
    iterm_refresh_interval_seconds: float | None = Field(default=None, gt=0, le=60)
    ignore_patterns: list[str] | None = None
    pricing: dict[str, PricingEntry] | None = None
    remote_control: RemoteControlUpdate | None = None
    editor: EditorConfig | None = None
    budgets: BudgetsConfig | None = None


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    return load_config()


@router.post("/config")
async def post_config(body: ConfigUpdate, request: Request):
    updates = body.model_dump(exclude_none=True)
    cfg = update_config(updates)
    request.app.state.s.config = cfg
    return cfg


@router.get("/pricing")
async def get_pricing():
    return load_config().get("pricing", {})


@router.post("/pricing")
async def post_pricing(pricing: dict[str, PricingEntry], request: Request):
    # Convert validated models back to plain dicts for the update_config
    # deep-merge (which expects nested dicts, not Pydantic instances).
    pricing_dict = {k: v.model_dump() for k, v in pricing.items()}
    cfg = update_config({"pricing": pricing_dict})
    request.app.state.s.config = cfg
    return cfg.get("pricing", {})
