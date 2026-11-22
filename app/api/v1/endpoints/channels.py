"""Channel discovery and monitoring configuration endpoints."""
import uuid
from typing import List
from fastapi import APIRouter, Depends, status

from ....auth import AuthContext, verify_api_key
from ....schemas import ChannelCreateRequest, ChannelResponse
from ....services.supabase_client import db

router = APIRouter()


@router.get("/channels", response_model=List[ChannelResponse], tags=["Channels"])
async def list_channels(auth: AuthContext = Depends(verify_api_key)):
    """List monitored YouTube channels for incoming video discovery with tenant scoping."""
    user_id = None if auth.is_admin else auth.user_id
    return db.list_channels(user_id=user_id)


@router.post("/channels", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED, tags=["Channels"])
async def add_channel(
    payload: ChannelCreateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Add a YouTube channel for dashboard monitoring and auto-clipping with ownership."""
    chan_id = f"chan_{uuid.uuid4().hex[:10]}"
    chan_data = {
        "id": chan_id,
        "user_id": auth.user_id if auth.user_id != "system_admin" else None,
        "channel_url": payload.channel_url,
        "channel_name": payload.channel_name,
        "auto_clip_new_videos": payload.auto_clip_new_videos,
    }
    return db.create_channel(chan_data)
