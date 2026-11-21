"""Generated clips retrieval, review, Buffer scheduling, and direct publishing endpoints."""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....auth import AuthContext, enforce_resource_ownership, verify_api_key
from ....config import settings
from ....schemas import (
    ClipApprovalRequest,
    ClipResponse,
    ClipScheduleRequest,
    PublishingStatus,
)
from ....services.buffer_publisher import buffer_publisher
from ....services.scheduler import schedule_manager
from ....services.supabase_client import db

logger = logging.getLogger("gta6_shorts.clips")
router = APIRouter(tags=["Clips"])


@router.get("/clips/{clip_id}", response_model=ClipResponse)
async def get_clip(
    clip_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """Retrieve detailed clip metadata, storage URL, score, and status with ownership check."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Clip {clip_id} not found")
    enforce_resource_ownership(clip.get("user_id"), auth, "clip")
    return clip


@router.get("/clips", response_model=List[ClipResponse])
async def list_clips(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category_id: Optional[str] = Query(None, description="Filter clips by category"),
    auth: AuthContext = Depends(verify_api_key),
):
    """List generated clips with pagination and optional category filtering."""
    items, _ = db.list_clips(limit=limit, offset=offset, category_id=category_id)
    return items


@router.post("/clips/{clip_id}/approve", response_model=ClipResponse)
async def approve_clip(
    clip_id: str,
    payload: ClipApprovalRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Human review approval or modification of a generated clip before scheduling or publishing."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Clip {clip_id} not found")
    enforce_resource_ownership(clip.get("user_id"), auth, "clip")

    is_approved = True
    if payload.approved is not None:
        is_approved = payload.approved
    elif payload.approved_for_publishing is not None:
        is_approved = payload.approved_for_publishing

    new_status = PublishingStatus.APPROVED.value if is_approved else PublishingStatus.CANCELLED.value
    updates = {"publishing_status": new_status}

    effective_title = payload.title or payload.custom_title
    if effective_title:
        updates["title"] = effective_title
    if payload.hook:
        updates["hook_sentence"] = payload.hook
    if payload.hashtags is not None:
        updates["suggested_tags"] = payload.hashtags

    updated = db.update_clip(clip_id, updates)
    return updated


@router.post("/clips/{clip_id}/publish")
async def publish_clip(
    clip_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Publish an approved clip directly (via configured YouTube OAuth or Buffer).
    Enforces that the clip must be reviewed and approved first.
    """
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Clip {clip_id} not found")
    enforce_resource_ownership(clip.get("user_id"), auth, "clip")

    if clip.get("publishing_status") not in (PublishingStatus.APPROVED.value, "approved"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clip must be approved before publishing. Review and approve the clip first.",
        )

    # Check YouTube credentials if configured
    if not settings.YOUTUBE_CLIENT_ID or not settings.YOUTUBE_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="YouTube Data API OAuth is not configured on this engine. Use Buffer publishing via /api/v1/clips/{clip_id}/schedule or configure YouTube credentials.",
        )

    return {
        "status": "publishing_initiated",
        "clip_id": clip_id,
        "service": "youtube",
    }


@router.post("/clips/{clip_id}/schedule")
async def schedule_clip_publishing(
    clip_id: str,
    payload: ClipScheduleRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Schedule an approved clip for publication via Buffer, verifying daily quotas and duplicate rules."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Clip {clip_id} not found")
    enforce_resource_ownership(clip.get("user_id"), auth, "clip")

    if clip.get("publishing_status") not in (PublishingStatus.APPROVED.value, "approved", PublishingStatus.DRAFT.value, "draft"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Clip cannot be scheduled in current status: {clip.get('publishing_status')}",
        )

    category_id = clip.get("category_id", "gaming")
    can_schedule, reason = schedule_manager.check_scheduling_eligibility(
        clip_id=clip_id,
        channel_id=payload.channel_id,
        category=category_id,
        override_quota=payload.override_quota,
    )
    if not can_schedule:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=reason)

    caption_text = payload.caption or f"{clip.get('title')}\n\n{' '.join(clip.get('suggested_tags', []))}"
    scheduled_iso = payload.scheduled_for.isoformat()

    buffer_res = buffer_publisher.create_scheduled_post(
        profile_id=payload.channel_id,
        text=caption_text,
        media_url=clip.get("storage_url"),
        scheduled_at=scheduled_iso,
    )

    if not buffer_res.get("success"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Buffer scheduling failed: {buffer_res.get('error')}",
        )

    schedule_manager.record_scheduled_clip(clip_id, payload.channel_id)
    db.update_clip(clip_id, {"publishing_status": PublishingStatus.SCHEDULED.value})

    return {
        "status": "scheduled",
        "clip_id": clip_id,
        "channel_id": payload.channel_id,
        "scheduled_for": scheduled_iso,
        "buffer_update_id": buffer_res.get("update_id"),
    }
