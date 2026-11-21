"""Content discovery and source licensing management endpoints."""
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....auth import AuthContext, enforce_resource_ownership, require_admin, verify_api_key
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

    # Persist source record in database
    created_source = db.create_source(source_record)

    # Register and persist initial permissions
    rights_manager.set_permission(
        source_id=source_id,
        status=RightsStatus(payload.rights_status),
        allowed_platforms=payload.allowed_platforms,
        license_type=payload.license_type,
        verified_by=auth.user_id if auth.is_admin else None,
    )

    return {"status": "created", "source": created_source}


@router.get("/sources")
async def list_content_sources(
    category_id: Optional[str] = Query(None, description="Filter by category"),
    auth: AuthContext = Depends(verify_api_key),
):
    """List registered content sources scoped to the authenticated user."""
    user_id = None if auth.is_admin else auth.user_id
    sources = db.list_sources(user_id=user_id, category_id=category_id)
    return {"sources": sources, "total": len(sources)}


@router.get("/sources/{source_id}")
async def get_content_source(
    source_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """Retrieve details for a specific content source with ownership verification."""
    source = db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Source {source_id} not found")
    enforce_resource_ownership(source.get("user_id"), auth, "source")
    permission = rights_manager.get_permission(source_id)
    return {
        "source": source,
        "permission": permission,
    }


@router.post("/sources/{source_id}/rights")
async def update_source_rights(
    source_id: str,
    payload: SourceRightsUpdateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Update or verify the legal reuse rights for a content source. Admin only."""
    require_admin(auth)

    source = db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Source {source_id} not found")

    rights_manager.set_permission(
        source_id=source_id,
        status=RightsStatus(payload.rights_status),
        allowed_platforms=payload.allowed_platforms,
        license_type=payload.license_type,
        expires_at=payload.expires_at,
        notes=payload.notes,
        verified_by=auth.user_id,
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
    """
    Retrieve discovered content opportunities ready for processing.
    Executes actual discovery against registered active sources and returns persisted items.
    """
    user_id = None if auth.is_admin else auth.user_id

    # 1. Check for active sources
    sources = db.list_sources(user_id=user_id, category_id=category_id, active_only=True)
    rss_adapter = RSSDiscoveryAdapter()

    for src in sources:
        if src.get("source_type") == "rss" and src.get("feed_url"):
            try:
                discovered = rss_adapter.discover_content(src, limit=5)
                for item in discovered:
                    db.save_discovered_content({
                        "id": item.id,
                        "source_id": src["id"],
                        "user_id": src.get("user_id"),
                        "title": item.title,
                        "original_url": item.original_url,
                        "media_url": item.media_url,
                        "creator_name": item.creator_name,
                        "category_id": src.get("category_id", "gaming"),
                        "rights_status": item.rights_status.value,
                        "duration_seconds": 0.0,
                        "discovery_time": datetime.now(timezone.utc).isoformat(),
                        "is_processed": False,
                    })
            except Exception as exc:
                logger.warning("Discovery failed for source %s: %s", src["id"], exc)

    # 2. Return discovered items from database
    items = db.list_discovered_content(category_id=category_id, user_id=user_id, limit=30)
    response_items = []
    for it in items:
        response_items.append(DiscoveredItemResponse(
            id=it["id"],
            source_id=it.get("source_id") or "manual",
            title=it["title"],
            original_url=it.get("original_url"),
            media_url=it.get("media_url"),
            creator_name=it.get("creator_name"),
            category_id=it.get("category_id") or "gaming",
            duration_seconds=it.get("duration_seconds"),
            rights_status=it.get("rights_status", "pending_verification"),
            discovery_time=it.get("discovery_time") or datetime.now(timezone.utc).isoformat(),
            is_processed=bool(it.get("is_processed", False)),
        ))

    return response_items
