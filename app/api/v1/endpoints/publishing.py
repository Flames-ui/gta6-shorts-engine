"""Buffer & YouTube publishing accounts, channels, scheduler, and reconciliation endpoints."""
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ....auth import AuthContext, require_admin, verify_api_key
from ....schemas import BufferCallbackRequest, BufferConnectResponse
from ....services.buffer_publisher import buffer_publisher
from ....services.internal_scheduler import internal_scheduler
from ....services.reconciliation import reconciliation_service
from ....services.supabase_client import db
from ....services.youtube_publisher import youtube_publisher

logger = logging.getLogger("gta6_shorts.publishing")
router = APIRouter(prefix="/publishing", tags=["Publishing"])


class ScheduleClipRequest(BaseModel):
    clip_id: str
    platform: str  # "tiktok" or "youtube"
    scheduled_for: Optional[str] = None
    priority_score: Optional[float] = None
    caption: Optional[str] = None
    privacy_status: Optional[str] = "private"


class PublishNowRequest(BaseModel):
    clip_id: str
    platform: str  # "tiktok" or "youtube"
    privacy_status: Optional[str] = "private"


class YouTubeCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None


@router.get("/buffer/connect", response_model=BufferConnectResponse)
async def get_buffer_connect_url(auth: AuthContext = Depends(verify_api_key)):
    """Generate secure Buffer OAuth authorization URL with single-use persisted state."""
    try:
        auth_url, state = buffer_publisher.get_authorization_url(user_id=auth.user_id)
        return BufferConnectResponse(auth_url=auth_url, state=state)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )


@router.post("/buffer/callback")
async def handle_buffer_callback(payload: BufferCallbackRequest):
    """
    Exchange Buffer OAuth authorization code for an API access token.
    Validates cryptographically random OAuth state without requiring internal API key.
    """
    if not payload.state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing OAuth state parameter. Cannot verify authenticity of callback.",
        )

    # Validate state is valid, not expired, and not yet consumed
    state_record = db.validate_and_consume_oauth_state(payload.state, service="buffer")
    if not state_record:
        logger.warning("Rejected invalid, expired, or already used OAuth state: %s", payload.state)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, expired, or previously consumed OAuth state.",
        )

    # Exchange code for access token
    res = buffer_publisher.exchange_code_for_token(payload.code)
    if not res.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Buffer authorization exchange failed: {res.get('error')}",
        )

    # Persist access token associated with the user
    user_id = state_record.get("user_id")
    access_token = res.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Buffer authorization exchange did not return a valid access token.",
        )

    try:
        saved = db.save_publishing_account({
            "id": f"buffer_{user_id or 'admin'}",
            "user_id": user_id,
            "service": "buffer",
            "access_token": access_token,
            "is_active": True,
        })
        if not saved:
            raise RuntimeError("Database returned empty result upon saving publishing account")
    except Exception as exc:
        logger.error("Failed to persist publishing account: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist authenticated Buffer publishing credentials.",
        ) from exc

    return {"status": "connected", "service": "buffer"}


@router.get("/youtube/connect")
async def get_youtube_connect_url(auth: AuthContext = Depends(verify_api_key)):
    """Generate Google OAuth 2.0 authorization URL for YouTube Data API publishing with single-use state."""
    try:
        return youtube_publisher.get_auth_url(user_id=auth.user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post("/youtube/callback")
async def handle_youtube_callback(payload: YouTubeCallbackRequest):
    """
    Exchange Google OAuth authorization code and save YouTube upload credentials.
    Validates single-use OAuth state without requiring internal API key on public callback.
    """
    if not payload.state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing OAuth state parameter. Cannot verify authenticity of YouTube callback.",
        )

    state_record = db.validate_and_consume_oauth_state(payload.state, service="youtube")
    if not state_record:
        logger.warning("Rejected invalid, expired, or already used YouTube OAuth state: %s", payload.state)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, expired, or previously consumed OAuth state.",
        )

    try:
        token_data = youtube_publisher.exchange_code(payload.code)
        user_id = state_record.get("user_id") or "admin"
        saved = db.save_publishing_account({
            "id": f"youtube_{user_id}",
            "user_id": user_id,
            "service": "youtube",
            "access_token": token_data.get("token"),
            "refresh_token": token_data.get("refresh_token"),
            "is_active": True,
        })
        return {"status": "connected", "service": "youtube", "account_id": saved.get("id")}
    except Exception as exc:
        logger.error("YouTube authorization exchange failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"YouTube OAuth exchange failed: {exc}") from exc


@router.post("/publish-now")
async def publish_clip_now(payload: PublishNowRequest, auth: AuthContext = Depends(verify_api_key)):
    """Immediately publish a rendered clip to TikTok (via Buffer Post Now) or YouTube Shorts."""
    # First, schedule the clip
    try:
        slot = internal_scheduler.schedule_clip(
            clip_id=payload.clip_id,
            platform=payload.platform,
            user_id=auth.user_id,
            privacy_status=payload.privacy_status,
        )
        # Then immediately dispatch it
        res = internal_scheduler.dispatch_now(slot["id"])
        return res
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_err)) from val_err
    except Exception as exc:
        logger.error("Failed to publish clip now: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post("/schedule")
async def schedule_clip_publication(payload: ScheduleClipRequest, auth: AuthContext = Depends(verify_api_key)):
    """Add an approved clip to the internal publishing schedule."""
    try:
        slot = internal_scheduler.schedule_clip(
            clip_id=payload.clip_id,
            platform=payload.platform,
            scheduled_for=payload.scheduled_for,
            priority_score=payload.priority_score,
            caption=payload.caption,
            user_id=auth.user_id,
            privacy_status=payload.privacy_status,
        )
        return slot
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_err)) from val_err


@router.post("/dispatch-due")
async def dispatch_due_publications(auth: AuthContext = Depends(verify_api_key)):
    """Scan internal publishing queue and dispatch items whose scheduled time has arrived."""
    require_admin(auth)
    return internal_scheduler.process_due_publications()


@router.post("/reconcile-daily")
async def trigger_daily_reconciliation(
    lookback_hours: int = Query(24, ge=1, le=72),
    auth: AuthContext = Depends(verify_api_key),
):
    """Execute 24-hour batch reconciliation cycle for TikTok (via Buffer) and YouTube Shorts."""
    require_admin(auth)
    report = reconciliation_service.run_daily_reconciliation(lookback_hours=lookback_hours)
    return report.model_dump()


@router.get("/reconciliation-reports")
async def list_reconciliation_reports(
    limit: int = Query(10, ge=1, le=50),
    auth: AuthContext = Depends(verify_api_key),
):
    """Retrieve historical 24-hour publication reconciliation reports."""
    require_admin(auth)
    return {"reports": reconciliation_service.list_reports(limit=limit)}


@router.get("/channels")
async def list_connected_publishing_channels(auth: AuthContext = Depends(verify_api_key)):
    """List connected social channels (TikTok, YouTube Shorts, etc.) configured in Buffer."""
    profiles = buffer_publisher.get_user_profiles(user_id=auth.user_id)
    return {"channels": profiles, "total": len(profiles)}


@router.get("/queue")
async def get_publishing_queue(auth: AuthContext = Depends(verify_api_key)):
    """View the currently active internal publishing queue and pending schedules."""
    user_id = None if auth.is_admin else auth.user_id
    items = db.list_publishing_queue(user_id=user_id)
    return {"items": items, "total": len(items)}


@router.get("/accounts")
async def list_connected_publishing_accounts(auth: AuthContext = Depends(verify_api_key)):
    """List connected publishing provider accounts for authenticated user."""
    user_id = None if auth.is_admin else auth.user_id
    accounts = db.list_publishing_accounts(user_id=user_id)
    safe_accounts = []
    for acc in accounts:
        safe_accounts.append({
            "id": acc.get("id"),
            "service": acc.get("service"),
            "account_identifier": acc.get("account_identifier"),
            "is_active": acc.get("is_active", True),
            "created_at": acc.get("created_at"),
            "has_token": bool(acc.get("access_token")),
        })
    return {"accounts": safe_accounts, "total": len(safe_accounts)}


@router.post("/sync")
async def sync_publishing_queue(auth: AuthContext = Depends(verify_api_key)):
    """Synchronize publishing status with Buffer for all active queue items."""
    user_id = None if auth.is_admin else auth.user_id
    items = db.list_publishing_queue(user_id=user_id)
    synced_items = []
    for it in items:
        if it.get("status") in ("scheduled", "publishing") and it.get("id"):
            sync_res = buffer_publisher.sync_publishing_item_status(it["id"], user_id=auth.user_id)
            synced_items.append({"id": it["id"], "result": sync_res})
    return {"synced_count": len(synced_items), "items": synced_items}
