"""Content discovery and source licensing management endpoints."""
import logging
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....auth import AuthContext, verify_api_key
from ....schemas import (
    DiscoveredItemResponse,
    SourceCreateRequest,
    SourceRightsUpdateRequest,
)
from ....services.discovery.base import RightsStatus
from ....services.discovery.rights_manager import rights_manager
from ....services.discovery.rss_adapter import RSSDiscoveryAdapter
from ....services.supabase_client import db

logger = logging.getLogger("gta6_shorts.discovery")
router = APIRouter(prefix="/discovery", tags=["Discovery"])


@router.post("/sources", status_code=status.HTTP_201_CREATED)
async def register_content_source(
    payload: SourceCreateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Register a new content source (RSS feed, creator submission portal, or authorized direct upload)."""
    source_id = f"src_{uuid.uuid4().hex[:10]}"
    source_record = {
        "id": source_id,
        "user_id": auth.user_id if auth.user_id != "system_admin" else None,
        "source_type": payload.source_type,
        "name": payload.name,
        "feed_url": payload.feed_url,
        "channel_url": payload.channel_url,
        "category_id": payload.category_id,
        "is_active": True,
        "auto_clip_enabled": False,
    }

    # Register initial permissions
    rights_manager.set_permission(
        source_id=source_id,
        status=RightsStatus(payload.rights_status),
        allowed_platforms=payload.allowed_platforms,
        license_type=payload.license_type,
    )

    return {"status": "created", "source": source_record}


@router.post("/sources/{source_id}/rights")
async def update_source_rights(
    source_id: str,
    payload: SourceRightsUpdateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Update or verify the legal reuse rights for a content source."""
    if not auth.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only administrators can verify rights.")

    rights_manager.set_permission(
        source_id=source_id,
        status=RightsStatus(payload.rights_status),
        allowed_platforms=payload.allowed_platforms,
        license_type=payload.license_type,
        expires_at=payload.expires_at,
        notes=payload.notes,
    )

    return {
        "source_id": source_id,
        "rights_status": payload.rights_status,
        "allowed_platforms": payload.allowed_platforms,
        "status": "updated",
    }


@router.get("/feed", response_model=List[DiscoveredItemResponse])
async def get_discovery_feed(
    category_id: Optional[str] = Query(None, description="Filter feed by category"),
    auth: AuthContext = Depends(verify_api_key),
):
    """Retrieve verified content opportunities ready for processing."""
    # Return discovered content items
    return []
