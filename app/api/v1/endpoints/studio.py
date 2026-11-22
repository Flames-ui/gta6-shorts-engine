"""Clip Studio REST and Real-Time SSE Endpoints."""
import asyncio
import json
import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ....services.clip_studio import MultitrackTimeline, clip_studio, studio_hub
from ....services.outro_service import outro_service
from ....auth import AuthContext, enforce_resource_ownership, require_admin, verify_api_key
from ....services.supabase_client import db
from ....queue import job_queue
import uuid
import logging

logger = logging.getLogger("gta6_shorts.studio.api")


def render_studio_clip_job(job_id: str, clip_id: str):
    """RQ-compatible wrapper that records actual render completion or failure."""
    try:
        result = clip_studio.render_timeline(clip_id)
        db.update_clip(clip_id, {"status": "completed"})
        return result
    except Exception:
        logger.exception("Studio render job %s failed", job_id)
        db.update_clip(clip_id, {"status": "render_failed"})
        raise

router = APIRouter(prefix="/studio", tags=["Clip Studio"])


@router.get("/stream/{job_id}")
async def stream_studio_events(job_id: str, request: Request, auth: AuthContext = Depends(verify_api_key)):
    """Real-time SSE event stream for live rendering and AI decisions in the Clip Studio."""
    require_admin(auth)
    q = studio_hub.subscribe(job_id)

    async def event_generator():
        # Yield initial connected event
        yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'job_id': job_id})}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat ping
                    yield ": ping\n\n"
        finally:
            studio_hub.unsubscribe(job_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/clips/{clip_id}/timeline", response_model=MultitrackTimeline)
async def get_clip_timeline(clip_id: str, auth: AuthContext = Depends(verify_api_key)):
    """Fetch 6-track multitrack timeline for a clip."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    enforce_resource_ownership(clip.get("user_id"), auth, "clip")
    timeline = clip_studio.get_or_create_timeline(clip_id)
    return timeline


@router.post("/clips/{clip_id}/timeline", response_model=MultitrackTimeline)
async def save_clip_timeline(
    clip_id: str,
    payload: MultitrackTimeline,
    auth: AuthContext = Depends(verify_api_key),
):
    """Save updated multitrack timeline data."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    enforce_resource_ownership(clip.get("user_id"), auth, "clip")
    saved = clip_studio.save_timeline(clip_id, payload.model_dump())
    return saved


@router.post("/clips/{clip_id}/render")
async def render_studio_clip(
    clip_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """Trigger multitrack timeline rendering with outro injection."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    enforce_resource_ownership(clip.get("user_id"), auth, "clip")
    timeline = clip_studio.get_or_create_timeline(clip_id)
    if timeline.outro_track.get("enabled"):
        configured_outro = outro_service.get_config().outro_path
        if not configured_outro or not os.path.isfile(configured_outro):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Outro is enabled but no outro video is configured")
    source_path = clip.get("file_path")
    if not source_path or not os.path.isfile(source_path):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A real local source video must be available before rendering")
    job_id = f"studio_render_{uuid.uuid4().hex[:12]}"
    db.update_clip(clip_id, {"status": "rendering"})
    try:
        queued = job_queue.enqueue_job(render_studio_clip_job, job_id, clip_id=clip_id)
    except Exception as exc:
        db.update_clip(clip_id, {"status": "render_failed"})
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Render job could not be queued: {exc}") from exc
    if not queued:
        db.update_clip(clip_id, {"status": "render_failed"})
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Render worker queue is unavailable")
    return {"status": "queued", "job_id": job_id, "clip_id": clip_id}


@router.get("/clips/{clip_id}/video")
async def preview_studio_video(clip_id: str, auth: AuthContext = Depends(verify_api_key)):
    """Stream rendered video for the Clip Studio player."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Clip {clip_id} not found")

    enforce_resource_ownership(clip.get("user_id"), auth, "clip")
    path = clip.get("storage_url") or clip.get("file_path")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No rendered video is available for this clip")
    return FileResponse(path, media_type="video/mp4")


@router.get("/events")
async def get_studio_events(
    clip_id: str = "global",
    auth: AuthContext = Depends(verify_api_key),
):
    """Fetch recent studio events history for hydration and live log refresh."""
    events = studio_hub.get_history(clip_id or "global")
    return {"events": events, "total": len(events)}
