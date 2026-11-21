"""Clip inspection, approval, and publishing endpoints."""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status

from ....auth import verify_api_key
from ....schemas import ClipApproveRequest, ClipResponse, PublishingStatus
from ....services.supabase_client import db
from ....services.youtube_publisher import youtube_publisher

logger = logging.getLogger("gta6_shorts.clips")
router = APIRouter()


@router.get("/clips/{clip_id}", response_model=ClipResponse, tags=["Clips"])
async def get_clip(
    clip_id: str,
    token: str = Depends(verify_api_key),
):
    """Retrieve generated clip metadata, video URL, virality score, hook, and hashtags."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Clip {clip_id} not found",
        )
    return clip


@router.post("/clips/{clip_id}/approve", response_model=ClipResponse, tags=["Clips"])
async def approve_clip(
    clip_id: str,
    payload: ClipApproveRequest,
    token: str = Depends(verify_api_key),
):
    """Approve a clip for YouTube publishing and optionally customize titles/tags."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Clip {clip_id} not found")

    updates = {
        "publishing_status": PublishingStatus.APPROVED.value if payload.approved_for_publishing else PublishingStatus.DRAFT.value
    }
    if payload.custom_title:
        updates["title"] = payload.custom_title.strip()
    if payload.custom_description:
        updates["description"] = payload.custom_description.strip()
    if payload.custom_hashtags is not None:
        updates["hashtags"] = payload.custom_hashtags

    db.update_clip(clip_id, updates)
    return db.get_clip(clip_id)


@router.post("/clips/{clip_id}/publish", tags=["Clips"])
async def publish_clip(
    clip_id: str,
    token: str = Depends(verify_api_key),
):
    """
    Publish an approved clip to YouTube as a private Short.
    Requires prior manual approval and configured YouTube OAuth credentials.
    """
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Clip {clip_id} not found")

    if clip.get("publishing_status") != PublishingStatus.APPROVED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clip must be approved before publishing. Call POST /api/v1/clips/{clip_id}/approve first.",
        )

    if not youtube_publisher.is_configured():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="YouTube Data API OAuth is not configured on this engine. Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in environment.",
        )

    return {
        "status": "pending_oauth_token",
        "clip_id": clip_id,
        "message": "Clip is approved. Connect channel via OAuth authorization URL to initiate upload.",
        "oauth_url": youtube_publisher.get_auth_url(state=clip_id)["authorization_url"],
    }
