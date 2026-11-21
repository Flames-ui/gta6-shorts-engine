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
    profiles = buffer_publisher.get_user_profiles()
    return {"channels": profiles, "total": len(profiles)}


@router.get("/queue")
async def get_publishing_queue(auth: AuthContext = Depends(verify_api_key)):
    """View the currently active social publishing queue and pending schedules."""
    user_id = None if auth.is_admin else auth.user_id
    items = db.list_publishing_queue(user_id=user_id)
    return {"items": items, "total": len(items)}
