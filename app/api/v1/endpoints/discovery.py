"""Content discovery and source licensing management endpoints."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....auth import AuthContext, enforce_resource_ownership, require_admin, verify_api_key
from ....schemas import (
    DiscoveredItemResponse,
    SourceCreateRequest,
    SourceRightsUpdateRequest,
    SourceUpdateRequest,
)
from ....services.discovery.base import AUTHORIZED_RIGHTS_STATUSES, RightsStatus
from ....services.discovery.rights_manager import rights_manager
from ....services.discovery.poll_eligibility import PollDecision, as_flag, evaluate_poll_eligibility
from ....services.discovery.rss_adapter import RSSDiscoveryAdapter
from ....services.discovery.youtube_channel_adapter import (
    YOUTUBE_SOURCE_TYPES,
    YouTubeChannelAdapter,
    YouTubeDiscoveryError,
)
from ....services.supabase_client import db

logger = logging.getLogger("gta6_shorts.discovery")
router = APIRouter(prefix="/discovery", tags=["Discovery"])


def _as_uuid(value) -> Optional[str]:
    """Return value as a canonical UUID string, or None when it is not one."""
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _find_existing_source(payload: SourceCreateRequest, owner_id: Optional[str]) -> Optional[dict]:
    """Return the owner's source already registered for the same feed or channel, if any."""
    locators = [
        (field, value.strip())
        for field, value in (("feed_url", payload.feed_url), ("channel_url", payload.channel_url))
        if value and value.strip()
    ]
    if not locators:
        return None
    for candidate in db.list_sources(user_id=owner_id, category_id="gta6", active_only=False):
        if candidate.get("user_id") != owner_id or candidate.get("source_type") != payload.source_type:
            continue
        if any((candidate.get(field) or "").strip() == value for field, value in locators):
            return candidate
    return None


@router.post("/sources", status_code=status.HTTP_201_CREATED)
async def register_content_source(
    payload: SourceCreateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Register a content source (RSS feed, creator submission portal, or authorized direct upload).

    Fail-closed rules:
    - A source is created inactive and is activated only after its rights permission has been
      written, and only when an administrator records an authorized status.
    - A non-admin cannot self-certify rights; their registration stays pending_verification.
    - Registering the same feed or channel again reuses the existing source and its permission.
      A non-admin repeat never overwrites a permission that is already recorded.
    - If the permission write fails, the source stays inactive and the caller receives 503.
    """
    is_admin = bool(auth.is_admin)
    owner_id = auth.user_id if auth.user_id != "system_admin" else None
    requested_status = RightsStatus(payload.rights_status) if is_admin else RightsStatus.PENDING_VERIFICATION

    existing = _find_existing_source(payload, owner_id)
    if existing is not None:
        source = existing
        source_id = existing["id"]
        result_status = "exists"
    else:
        source_id = f"src_{uuid.uuid4().hex[:10]}"
        source = db.create_source({
            "id": source_id,
            "user_id": owner_id,
            "source_type": payload.source_type,
            "name": payload.name,
            "feed_url": payload.feed_url,
            "channel_url": payload.channel_url,
            "category_id": "gta6",
            "is_active": False,  # activated only after rights are durably recorded below
            "auto_clip_enabled": False,
        })
        result_status = "created"

    preserve_existing_permission = (
        result_status == "exists"
        and not is_admin
        and rights_manager.get_permission(source_id) is not None
    )
    if not preserve_existing_permission:
        try:
            rights_manager.set_permission(
                source_id=source_id,
                status=requested_status,
                allowed_platforms=payload.allowed_platforms if is_admin else [],
                license_type=payload.license_type if is_admin else None,
                # verified_by is a UUID reference to auth.users. API-key administrators ("system_admin")
                # have no UUID, so they are recorded in the audit log instead of this column.
                verified_by=_as_uuid(auth.user_id) if is_admin else None,
            )
        except Exception:
            logger.exception("Rights permission not recorded for source %s; source stays inactive", source_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rights could not be recorded, so the source is not active. Retry registration.",
            )

        should_activate = is_admin and requested_status in (
            RightsStatus.AUTHORIZED_EDITING,
            RightsStatus.AUTHORIZED_PLATFORMS,
        )
        if bool(source.get("is_active")) != should_activate:
            source = db.update_source(source_id, {"is_active": should_activate}) or {**source, "is_active": should_activate}

    db.create_audit_log(
        action="source_registered" if result_status == "created" else "source_reregistered",
        resource_type="source",
        user_id=auth.user_id,
        resource_id=source_id,
        details={"name": payload.name, "source_type": payload.source_type, "category_id": "gta6"},
    )
    return {"status": result_status, "source": source}


@router.get("/sources")
async def list_content_sources(
    category_id: Optional[str] = Query(None, description="Filter by category"),
    auth: AuthContext = Depends(verify_api_key),
):
    """List registered content sources scoped to the authenticated user."""
    user_id = None if auth.is_admin else auth.user_id
    sources = db.list_sources(user_id=user_id, category_id=category_id)
    for source in sources:
        permission = rights_manager.get_permission(source["id"])
        state = permission.get("rights_status") if permission else RightsStatus.PENDING_VERIFICATION
        source["rights_status"] = state.value if isinstance(state, RightsStatus) else str(state)
        source["allowed_platforms"] = permission.get("allowed_platforms", []) if permission else []
    return {"sources": sources, "total": len(sources)}


@router.patch("/sources/{source_id}")
async def update_content_source(
    source_id: str,
    payload: SourceUpdateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    source = db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    enforce_resource_ownership(source.get("user_id"), auth, "source")
    updates = payload.model_dump(exclude_unset=True)
    if "is_active" in updates and updates["is_active"]:
        permission = rights_manager.get_permission(source_id)
        if not auth.is_admin or not permission or permission.get("rights_status") not in (RightsStatus.AUTHORIZED_EDITING, RightsStatus.AUTHORIZED_PLATFORMS):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an administrator can enable a rights-verified source")
    updated = db.update_source(source_id, updates)
    return {"status": "updated", "source": updated}


@router.delete("/sources/{source_id}")
async def deactivate_content_source(
    source_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    source = db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    enforce_resource_ownership(source.get("user_id"), auth, "source")
    updated = db.update_source(source_id, {"is_active": False, "auto_clip_enabled": False})
    return {"status": "deactivated", "source": updated}


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
    """Record the legal reuse rights for a content source. Admin only.

    Fail-closed write order:
    1. The source is deactivated first, so no run uses the previous grant while this update is in progress.
    2. The rights permission is persisted (status, allowed platforms, license, expiry, notes, verified_by).
       derivative_rights_allowed is true only for an authorized status.
    3. The audit entry is written. If it cannot be written, the update fails.
    4. The source is activated only when an authorized status has passed every step above.
    Any failure returns 503 and this request never activates the source.
    """
    require_admin(auth)

    source = db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Source {source_id} not found")

    rights_status = RightsStatus(payload.rights_status)  # already validated to a RightsStatus value (422 otherwise)
    is_authorized = rights_status in AUTHORIZED_RIGHTS_STATUSES
    # verified_by references auth.users (UUID). API-key administrators ("system_admin") have no UUID;
    # they are recorded as the actor in the audit entry instead.
    verified_by = _as_uuid(auth.user_id)
    expires_iso = payload.expires_at.isoformat() if payload.expires_at else None

    try:
        db.update_source(source_id, {"is_active": False})
        rights_manager.set_permission(
            source_id=source_id,
            status=rights_status,
            allowed_platforms=payload.allowed_platforms,
            license_type=payload.license_type,
            expires_at=payload.expires_at,
            notes=payload.notes,
            verified_by=verified_by,
            derivative_rights_allowed=is_authorized,
        )
        db.create_audit_log(
            action="source_rights_updated",
            resource_type="source",
            user_id=auth.user_id,
            resource_id=source_id,
            details={
                "actor": auth.user_id,
                "rights_status": rights_status.value,
                "allowed_platforms": payload.allowed_platforms,
                "license_type": payload.license_type,
                "expires_at": expires_iso,
                "derivative_rights_allowed": is_authorized,
                "notes": payload.notes,
            },
            required=True,
        )
        if is_authorized:
            db.update_source(source_id, {"is_active": True})
    except Exception:
        logger.exception("Rights update failed for source %s; source left inactive", source_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rights could not be recorded and the source was not activated. Retry the update.",
        )

    return {
        "source_id": source_id,
        "rights_status": rights_status.value,
        "allowed_platforms": payload.allowed_platforms,
        "license_type": payload.license_type,
        "expires_at": expires_iso,
        "derivative_rights_allowed": is_authorized,
        "is_active": is_authorized,
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
        permission = rights_manager.get_permission(src["id"])
        if not permission or permission.get("rights_status") not in (RightsStatus.AUTHORIZED_EDITING, RightsStatus.AUTHORIZED_PLATFORMS):
            continue
        if src.get("source_type") in ("approved_rss", "rss") and src.get("feed_url"):
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


@router.post("/poll")
async def poll_content_sources(
    source_id: Optional[str] = Query(None, description="Optional specific source ID to poll"),
    category_id: Optional[str] = Query(None, description="Optional category filter"),
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Poll discovery sources that are active and have usable authorized rights.

    Every candidate is checked against is_active, rights_status, revocation, expiry and its source
    configuration. Excluded sources are returned with a reason code and are never fetched. A source
    counts as polled only when a fetch was attempted; failures are recorded on the source and counted
    as errors. auto_clip_enabled does not gate polling.
    """
    user_id = None if auth.is_admin else auth.user_id
    now = datetime.now(timezone.utc)
    if source_id:
        src = db.get_source(source_id)
        if not src:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Source {source_id} not found")
        enforce_resource_ownership(src.get("user_id"), auth, "source")
        sources = [src]
    else:
        sources = db.list_sources(user_id=user_id, category_id=category_id, active_only=True)

    # Selection: one decision per candidate, each with a reason when excluded.
    eligible: List[Dict] = []
    excluded: List[PollDecision] = []
    for src in sources:
        decision = evaluate_poll_eligibility(src, rights_manager.get_permission(str(src.get("id"))), now)
        if decision.eligible:
            eligible.append(src)
        else:
            excluded.append(decision)

    active_count = sum(1 for s in sources if as_flag(s.get("is_active")))
    auto_clip_count = sum(1 for s in sources if as_flag(s.get("auto_clip_enabled")))
    logger.info("discovery.poll.selection %s", json.dumps({
        "source_id_filter": source_id,
        "category_id_filter": category_id,
        "user_scope": user_id or "all",
        "candidate_sources": len(sources),
        "active_sources": active_count,
        "auto_clip_enabled_sources": auto_clip_count,
        "eligible_sources": len(eligible),
        "eligible_source_ids": [str(s.get("id")) for s in eligible],
        "excluded_sources": [d.as_dict() for d in excluded],
    }, default=str))

    rss_adapter = RSSDiscoveryAdapter()
    youtube_adapter = YouTubeChannelAdapter()
    from ....services.discovery.duplicate_detector import duplicate_detector

    total_polled = 0
    new_items_count = 0
    duplicates_count = 0
    errors_count = 0
    source_results: List[Dict] = []

    existing_content = db.list_discovered_content(user_id=user_id, limit=200)
    seen_urls = {duplicate_detector.normalize_url(it.get("original_url") or "") for it in existing_content if it.get("original_url")}

    for src in eligible:
        sid = str(src["id"])
        source_type = str(src.get("source_type") or "").strip().lower()
        use_channel = source_type in YOUTUBE_SOURCE_TYPES and bool(str(src.get("channel_url") or "").strip())
        permission = rights_manager.get_permission(sid) or {}
        rights_status = RightsStatus(permission.get("rights_status"))

        total_polled += 1
        attempted_at = datetime.now(timezone.utc).isoformat()
        new_for_source = 0
        dup_for_source = 0
        result = {"source_id": sid, "route": "youtube_channel" if use_channel else "rss", "last_polled_at": attempted_at}
        try:
            if use_channel:
                discovered = youtube_adapter.discover_content(src, limit=10, rights_status=rights_status)
                success_at = datetime.now(timezone.utc).isoformat()
                db.update_source_health(sid, status="healthy", last_polled_at=attempted_at,
                                        last_success_at=success_at, error="", reset_failures=True)
                result.update({"health_written_by": "poll", "last_success_at": success_at, "last_error": None})
            else:
                # The RSS adapter writes its own health update on success or failure.
                discovered = rss_adapter.discover_content(src, limit=10)
                result.update({"health_written_by": "rss_adapter"})

            for item in discovered:
                norm_url = duplicate_detector.normalize_url(item.original_url or "")
                if norm_url and norm_url in seen_urls:
                    duplicates_count += 1
                    dup_for_source += 1
                    continue

                db.save_discovered_content({
                    "id": item.id,
                    "source_id": sid,
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
                if norm_url:
                    seen_urls.add(norm_url)
                new_items_count += 1
                new_for_source += 1
            result.update({"outcome": "polled", "new_items": new_for_source, "duplicates_skipped": dup_for_source})
        except YouTubeDiscoveryError as exc:
            errors_count += 1
            message = f"{exc.code}: {exc}"
            db.update_source_health(sid, status="error", last_polled_at=attempted_at, error=message, increment_failures=True)
            result.update({"outcome": "error", "last_success_at": None, "last_error": message})
            logger.warning("discovery.poll.source_error %s", json.dumps({"source_id": sid, "code": exc.code, "error": str(exc)}))
        except Exception as exc:
            errors_count += 1
            message = f"unexpected_error: {type(exc).__name__}: {exc}"[:500]
            db.update_source_health(sid, status="error", last_polled_at=attempted_at, error=message, increment_failures=True)
            result.update({"outcome": "error", "last_success_at": None, "last_error": message})
            logger.warning("discovery.poll.source_error %s", json.dumps({"source_id": sid, "error": message}))

        source_results.append(result)
        logger.info("discovery.poll.source_result %s", json.dumps(result, default=str))

    response = {
        "polled_sources": total_polled,
        "new_items_discovered": new_items_count,
        "duplicates_skipped": duplicates_count,
        "errors": errors_count,
        "candidate_sources": len(sources),
        "active_sources": active_count,
        "auto_clip_enabled_sources": auto_clip_count,
        "eligible_sources": len(eligible),
        "excluded_sources": [d.as_dict() for d in excluded],
        "source_results": source_results,
    }
    logger.info("discovery.poll.result %s", json.dumps(response, default=str))
    return response


# -------------------------------------------------------------------------
# GTA 6 High-Yield Discovered Moments Endpoints
# -------------------------------------------------------------------------
from pydantic import BaseModel, Field

class MomentIngestRequest(BaseModel):
    source_id: str
    video_url: Optional[str] = None
    title: str
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    score: float = Field(ge=0.0, le=10.0)
    hook_text: Optional[str] = None
    event_type: str = "gameplay"


@router.get("/moments")
async def list_gta6_moments(
    status: Optional[str] = Query(None, description="Filter by status: detected, approved, produced, rejected"),
    limit: int = Query(50, ge=1, le=100),
    auth: AuthContext = Depends(verify_api_key),
):
    """List discovered GTA 6 viral moments ordered by score."""
    require_admin(auth)
    from ....services.discovery.gta_registry import gta_registry
    moments = gta_registry.list_moments(status=status, limit=limit)
    return {"moments": moments, "total": len(moments)}


@router.post("/moments", status_code=status.HTTP_201_CREATED)
async def ingest_gta6_moment(
    payload: MomentIngestRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Ingest a detected GTA 6 high-yield moment into the pool."""
    require_admin(auth)
    source = db.get_source(payload.source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    enforce_resource_ownership(source.get("user_id"), auth, "source")
    from ....services.discovery.gta_registry import gta_registry
    try:
        moment = gta_registry.ingest_moment(
            source_id=payload.source_id,
            video_url=payload.video_url,
            title=payload.title,
            start_time=payload.start_time,
            end_time=payload.end_time,
            score=payload.score,
            hook_text=payload.hook_text,
            event_type=payload.event_type,
        )
        return {"status": "ingested", "moment": moment}
    except (PermissionError, ValueError) as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))


@router.post("/moments/{moment_id}/approve")
async def approve_gta6_moment(
    moment_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """Approve a discovered GTA 6 moment for production."""
    require_admin(auth)
    from ....services.discovery.gta_registry import gta_registry
    moment = gta_registry.get_moment(moment_id)
    if not moment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Moment {moment_id} not found")

    gta_registry.approve_moment(moment_id)
    return {"status": "candidate_approved", "moment_id": moment_id, "produced": False}
