"""Performance analytics and AI optimization feedback loop endpoints."""
import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status

from ....auth import AuthContext, enforce_resource_ownership, verify_api_key
from ....services.analytics import analytics_service
from ....services.supabase_client import db

logger = logging.getLogger("gta6_shorts.analytics")
router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/overview")
async def get_analytics_overview(auth: AuthContext = Depends(verify_api_key)):
    """Retrieve macro-level publishing analytics across TikTok, YouTube Shorts, and categories."""
    user_id = None if auth.is_admin else auth.user_id
    overview = db.get_analytics_overview(user_id=user_id)
    return overview


@router.get("/clips/{clip_id}")
async def get_clip_analytics(
    clip_id: str,
    platform: str = "tiktok",
    auth: AuthContext = Depends(verify_api_key),
):
    """Retrieve granular performance metrics for a specific clip with ownership check."""
    clip = db.get_clip(clip_id)
    if clip:
        enforce_resource_ownership(clip.get("user_id"), auth, "clip")

    metrics = analytics_service.get_clip_metrics(clip_id, platform=platform)
    if not metrics:
        return {
            "clip_id": clip_id,
            "platform": platform,
            "status": "pending_sync",
            "message": "Performance metrics have not yet been synchronized from the platform.",
        }
    return metrics


@router.get("/recommendations")
async def get_optimization_recommendations(auth: AuthContext = Depends(verify_api_key)):
    """Retrieve actionable, data-driven production recommendations from the content feedback loop."""
    user_id = None if auth.is_admin else auth.user_id
    return analytics_service.generate_recommendations(user_id=user_id)
