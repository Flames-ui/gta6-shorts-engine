"""Buffer publishing accounts, channels, and scheduled queue management endpoints."""
import logging
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, status

from ....auth import AuthContext, verify_api_key
from ....schemas import BufferCallbackRequest, BufferConnectResponse
from ....services.buffer_publisher import buffer_publisher
from ....services.scheduler import schedule_manager

logger = logging.getLogger("gta6_shorts.publishing")
router = APIRouter(prefix="/publishing", tags=["Publishing"])


@router.get("/buffer/connect", response_model=BufferConnectResponse)
async def get_buffer_connect_url(auth: AuthContext = Depends(verify_api_key)):
    """Generate secure Buffer OAuth authorization URL."""
    try:
        auth_url, state = buffer_publisher.get_authorization_url()
        return BufferConnectResponse(auth_url=auth_url, state=state)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )


@router.post("/buffer/callback")
async def handle_buffer_callback(
    payload: BufferCallbackRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Exchange Buffer OAuth authorization code for an API access token."""
    res = buffer_publisher.exchange_code_for_token(payload.code)
    if not res.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Buffer authorization failed: {res.get('error')}",
        )
    return {"status": "connected", "service": "buffer"}


@router.get("/channels")
async def list_connected_publishing_channels(auth: AuthContext = Depends(verify_api_key)):
    """List connected social channels (TikTok, YouTube Shorts, etc.) configured in Buffer."""
    profiles = buffer_publisher.get_user_profiles()
    return {"channels": profiles, "total": len(profiles)}


@router.get("/queue")
async def get_publishing_queue(auth: AuthContext = Depends(verify_api_key)):
    """View the currently active social publishing queue and pending schedules."""
    return {"items": [], "total": 0}
