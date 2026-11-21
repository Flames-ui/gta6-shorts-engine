"""Automated scheduling and daily production quotas endpoints."""
import logging
from fastapi import APIRouter, Depends, HTTPException, status

from ....auth import AuthContext, verify_api_key
from ....schemas import ProductionScheduleUpdateRequest
from ....services.scheduler import ProductionScheduleConfig, schedule_manager

logger = logging.getLogger("gta6_shorts.schedules")
router = APIRouter(prefix="/schedules", tags=["Schedules"])


@router.get("")
async def get_production_schedule(auth: AuthContext = Depends(verify_api_key)):
    """Retrieve current daily production targets, active publishing windows, and pause status."""
    return schedule_manager.get_config()


@router.patch("")
async def update_production_schedule(
    payload: ProductionScheduleUpdateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Update daily production limits, publishing windows, or pause/resume engine output."""
    if not auth.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only administrators can adjust production schedules.")

    current = schedule_manager.get_config()
    data = current.model_dump()

    if payload.daily_target_min is not None:
        data["daily_target_min"] = payload.daily_target_min
    if payload.daily_target_max is not None:
        data["daily_target_max"] = payload.daily_target_max
    if payload.timezone is not None:
        data["timezone"] = payload.timezone
    if payload.is_paused is not None:
        data["is_paused"] = payload.is_paused
    if payload.category_distribution is not None:
        data["category_distribution"] = payload.category_distribution

    updated = schedule_manager.update_config(ProductionScheduleConfig(**data))
    return updated
