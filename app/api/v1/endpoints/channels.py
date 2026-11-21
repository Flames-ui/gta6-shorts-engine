"""Channel discovery and monitoring configuration endpoints."""
import uuid
from typing import List
from fastapi import APIRouter, Depends, status

from ....auth import verify_api_key
from ....schemas import ChannelCreateRequest, ChannelResponse
from ....services.supabase_client import db

router = APIRouter()


@router.get("/channels", response_model=List[ChannelResponse], tags=["Channels"])
async def list_channels(token: str = Depends(verify_api_key)):
    """List monitored YouTube channels for incoming video discovery."""
    return db.list_channels()


@router.post("/channels", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED, tags=["Channels"])
async def add_channel(
    payload: ChannelCreateRequest,
    token: str = Depends(verify_api_key),
):
    """Add a YouTube channel for dashboard monitoring and auto-clipping."""
    chan_id = f"chan_{uuid.uuid4().hex[:10]}"
    chan_data = {
        "id": chan_id,
        "channel_url": payload.channel_url,
        "channel_name": payload.channel_name,
        "auto_clip_new_videos": payload.auto_clip_new_videos,
    }
    return db.create_channel(chan_data)
