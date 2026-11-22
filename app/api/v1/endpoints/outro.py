"""Promotional Talking Stickman Outro API Endpoints."""
import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ....services.outro_service import OutroConfig, outro_service
from ....auth import AuthContext, require_admin, verify_api_key

router = APIRouter(prefix="/outro", tags=["Outro Studio"])


class OutroUpdateRequest(BaseModel):
    enabled: bool = Field(default=False, description="Enable or disable outro injection")
    promo_text: str = Field(
        default="Join our WhatsApp channel",
        max_length=140,
        description="Call to action copy displayed in speech bubble",
    )
    destination_url: str = Field(default="", description="WhatsApp channel or destination link")
    duration_seconds: float = Field(default=4.0, ge=1.0, le=10.0, description="Outro duration in seconds")
    qr_code_enabled: bool = Field(default=False, description="Display QR code overlay")


class OutroGenerateRequest(BaseModel):
    promo_text: str = Field(default="Join our WhatsApp channel")
    destination_url: str = Field(default="")
    duration_seconds: float = Field(default=4.0, ge=1.0, le=10.0)


@router.get("/config", response_model=OutroConfig)
async def get_outro_config(auth: AuthContext = Depends(verify_api_key)):
    """Fetch current outro configuration."""
    require_admin(auth)
    return outro_service.get_config()


@router.post("/config", response_model=OutroConfig)
async def update_outro_config(
    payload: OutroUpdateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Update active outro configuration."""
    require_admin(auth)
    if payload.enabled:
        current_path = outro_service.get_config().outro_path
        if not current_path or not os.path.isfile(current_path):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Generate or configure an outro video before enabling it")
    updated = outro_service.update_config(payload.model_dump())
    return updated


@router.post("/generate")
async def generate_outro_preview(
    payload: OutroGenerateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Synthesize custom stickman outro video asset."""
    require_admin(auth)
    target_path = outro_service.generate_stickman_outro(
        promo_text=payload.promo_text,
        destination_url=payload.destination_url,
        duration=payload.duration_seconds,
    )
    duration = outro_service.probe_video_duration(target_path)
    current = outro_service.get_config().model_dump()
    current.update({"outro_path": target_path, "promo_text": payload.promo_text, "destination_url": payload.destination_url, "duration_seconds": payload.duration_seconds, "enabled": True, "qr_code_enabled": False})
    outro_service.update_config(current)
    return {
        "status": "generated",
        "path": target_path,
        "duration": duration,
        "preview_url": "/api/v1/outro/preview",
    }


@router.get("/preview")
async def preview_outro_video(auth: AuthContext = Depends(verify_api_key)):
    """Stream or download the current talking stickman outro video."""
    require_admin(auth)
    cfg = outro_service.get_config()
    target_path = cfg.outro_path
    if not target_path or not os.path.isfile(target_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No outro video is configured")

    return FileResponse(
        target_path,
        media_type="video/mp4",
        filename="talking_stickman_outro.mp4",
    )
