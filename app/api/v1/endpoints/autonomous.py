"""Autonomous Engine API Endpoints."""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ....services.autonomous_engine import AutonomousConfig, autonomous_engine
from ....auth import AuthContext, require_admin, verify_api_key

router = APIRouter(prefix="/autonomous", tags=["Autonomous Engine"])


class AutonomousConfigRequest(BaseModel):
    is_active: bool = Field(default=False, description="Enable hands-free 24/7 autonomous production")
    daily_target: int = Field(default=15, ge=1, le=50, description="Target shorts quota per day")
    auto_publish: bool = Field(default=False, description="Automatically schedule clips once approved")
    min_score: float = Field(default=8.0, ge=5.0, le=10.0, description="Minimum viral score threshold")
    outro_enabled: bool = Field(default=True, description="Append promotional WhatsApp stickman outro")


@router.get("/config", response_model=AutonomousConfig)
async def get_autonomous_config(auth: AuthContext = Depends(verify_api_key)):
    """Fetch active autonomous pipeline configuration."""
    require_admin(auth)
    return autonomous_engine.get_config()


@router.post("/config", response_model=AutonomousConfig)
async def update_autonomous_config(
    payload: AutonomousConfigRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Update autonomous pipeline configuration."""
    require_admin(auth)
    updated = autonomous_engine.update_config(payload.model_dump())
    return updated


@router.get("/stats")
async def get_autonomous_stats(auth: AuthContext = Depends(verify_api_key)):
    """Fetch daily production quota progress and viral metrics."""
    require_admin(auth)
    return autonomous_engine.get_daily_stats()


@router.post("/trigger")
async def trigger_autonomous_cycle(auth: AuthContext = Depends(verify_api_key)):
    """Manually trigger an immediate autonomous discovery, clipping, and render cycle."""
    require_admin(auth)
    try:
        return await autonomous_engine.run_autonomous_cycle()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
