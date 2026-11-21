"""Production quotas and usage inspection endpoints."""
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Query
from ....auth import AuthContext, verify_api_key
from ....services.scheduler import schedule_manager

router = APIRouter(prefix="/quotas", tags=["Quotas"])


@router.get("")
async def get_daily_quotas(
    date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format (defaults to today)"),
    auth: AuthContext = Depends(verify_api_key),
) -> Dict[str, Any]:
    """Retrieve daily quota ceilings, target ranges, and real-time consumption across categories."""
    return schedule_manager.get_daily_usage(date_str=date)
