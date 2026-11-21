"""Buffer publishing accounts, channels, and scheduled queue management endpoints."""
import logging
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, status

from ....auth import AuthContext, verify_api_key
from ....schemas import BufferCallbackRequest, BufferConnectResponse
from ....services.buffer_publisher import buffer_publisher
from ....services.supabase_client import db

logger = logging.getLogger("gta6_shorts.publishing")
router = APIRouter(prefix="/publishing", tags=["Publishing"])


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
    if access_token:
        try:
            db.save_publishing_account({
                "id": f"buffer_{user_id or 'admin'}",
                "user_id": user_id,
                "service": "buffer",
                "access_token_encrypted": access_token,
                "is_active": True,
            })
        except Exception as exc:
            logger.error("Failed to persist publishing account: %s", exc)

    return {"status": "connected", "service": "buffer"}


@router.get("/channels")
async def list_connected_publishing_channels(auth: AuthContext = Depends(verify_api_key)):
    """List connected social channels (TikTok, YouTube Shorts, etc.) configured in Buffer."""
    profiles = buffer_publisher.get_user_profiles(user_id=auth.user_id)
    return {"channels": profiles, "total": len(profiles)}


@router.get("/queue")
async def get_publishing_queue(auth: AuthContext = Depends(verify_api_key)):
    """View the currently active social publishing queue and pending schedules."""
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
