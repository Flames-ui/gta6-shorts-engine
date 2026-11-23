"""Source endpoints: validate a URL, register a YouTube source, or upload a file."""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from ...auth import Principal
from ...config import settings
from ...db import repo
from ...errors import MediaValidationError, NotFound
from ...media import probe as probe_mod
from ...media import youtube
from ...media.uploads import check_extension, safe_display_name, stream_to_file
from ...media.urls import parse_youtube_url
from ...pipeline.orchestrator import source_key
from ...storage import get_storage
from ..deps import check_content_length, rate_limited
from ..schemas import SourceOut, SourceValidateIn, SourceValidateOut, YouTubeSourceIn

logger = logging.getLogger("studio.api.sources")
router = APIRouter(prefix="/sources", tags=["sources"])
CONTENT_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
                 ".mkv": "video/x-matroska", ".webm": "video/webm"}


@router.post("/validate", response_model=SourceValidateOut, summary="Validate a YouTube URL (no network access)")
def validate_source(body: SourceValidateIn, _: Principal = Depends(rate_limited)) -> SourceValidateOut:
    ref = parse_youtube_url(body.url)
    return SourceValidateOut(video_id=ref.video_id, normalized_url=ref.normalized_url,
                             retrieval_enabled=settings.YOUTUBE_RETRIEVAL_ENABLED)


@router.post("/youtube", status_code=201, response_model=SourceOut,
             summary="Register a YouTube URL as a source; metadata is fetched and checked now")
def add_youtube_source(body: YouTubeSourceIn, principal: Principal = Depends(rate_limited)):
    youtube.ensure_enabled()
    ref = parse_youtube_url(body.url)
    existing = repo.find_live_youtube_source(principal.user_id, ref.video_id)
    if existing:
        return JSONResponse(status_code=200, content=SourceOut.from_row(existing).model_dump(mode="json"))
    meta = youtube.inspect(ref)  # raises RetrievalError with a clear category when unavailable
    row = repo.create_source(
        principal.user_id,
        source_type="youtube",
        status="pending_retrieval",
        original_url=ref.normalized_url,
        youtube_video_id=ref.video_id,
        title=meta.get("title"),
        channel_title=meta.get("channel_title"),
        duration_seconds=round(meta["duration"], 3),
    )
    logger.info("youtube source registered", extra={"event": "source_registered", "source_id": str(row["id"])})
    return SourceOut.from_row(row)


@router.post("/upload", status_code=201, response_model=SourceOut,
             summary="Upload a video file (MP4, MOV, MKV or WebM)",
             responses={200: {"description": "Identical file already in your library"}})
def upload_source(request: Request, file: UploadFile = File(...),
                  principal: Principal = Depends(rate_limited)):
    check_content_length(request)
    name = safe_display_name(file.filename)
    ext = check_extension(name)
    tmp_dir = os.path.join(settings.TEMP_DIR, "uploads")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = os.path.join(tmp_dir, f"{uuid.uuid4().hex}{ext}")
    source = None
    try:
        streamed = stream_to_file(file.file, tmp, settings.MAX_UPLOAD_BYTES, ext)
        info = probe_mod.probe(tmp)
        probe_mod.check_source_limits(info)

        duplicate = repo.find_live_upload_by_sha(principal.user_id, streamed.sha256)
        if duplicate:
            return JSONResponse(status_code=200, content=SourceOut.from_row(duplicate).model_dump(mode="json"))

        source = repo.create_source(
            principal.user_id,
            source_type="upload",
            status="validating",
            title=os.path.splitext(name)[0][:300] or "Upload",
            file_size_bytes=streamed.size,
            container_format=info.format_name[:100],
            video_codec=info.video_codec,
            audio_codec=info.audio_codec,
            width=info.width,
            height=info.height,
            has_audio=info.has_audio,
            duration_seconds=info.duration,
            sha256=streamed.sha256,
        )
        key = source_key(principal.user_id, str(source["id"]), ext)
        get_storage().put_file(key, tmp, CONTENT_TYPES.get(ext, "application/octet-stream"))
        source = repo.update_source(source["id"], status="ready", file_key=key)
        logger.info("upload stored", extra={"event": "upload_stored", "source_id": str(source["id"]),
                                            "size_bytes": streamed.size})
        return SourceOut.from_row(source)
    except MediaValidationError:
        raise
    except Exception:
        if source is not None and source.get("status") != "ready":
            repo.update_source(source["id"], status="failed", failure_code="storage_failed",
                               failure_message="The upload could not be stored. Please retry.")
        raise
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@router.get("/{source_id}", response_model=SourceOut, summary="Get one of your sources")
def get_source(source_id: str, principal: Principal = Depends(rate_limited)) -> SourceOut:
    row = _owned_source(source_id, principal)
    return SourceOut.from_row(row)


def _owned_source(source_id: str, principal: Principal):
    try:
        uuid.UUID(source_id)
    except ValueError as exc:
        raise NotFound("Source was not found.") from exc
    row = repo.get_source(source_id)
    if row is None or row["owner_id"] != principal.user_id:
        raise NotFound("Source was not found.")
    return row
