"""Clip detail, signed download links and authorised downloads."""
from __future__ import annotations

import logging
import os
import re
import tempfile
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from ...auth import Principal
from ...config import settings
from ...db import connection, repo
from ...errors import Conflict, NotFound, Unprocessable
from ...storage import get_storage
from ..deps import rate_limited
from ..schemas import CaptionInfo, ClipDetailOut, ClipOut, DownloadUrlOut

logger = logging.getLogger("studio.api.clips")
router = APIRouter(prefix="/clips", tags=["clips"])
CAPTION_TYPES = {"srt": "application/x-subrip", "vtt": "text/vtt", "ass": "text/plain"}


def _owned_clip(clip_id: str, principal: Principal):
    try:
        uuid.UUID(clip_id)
    except ValueError as exc:
        raise NotFound("Clip was not found.") from exc
    row = repo.get_clip(clip_id, owner_id=principal.user_id)
    if row is None:
        raise NotFound("Clip was not found.")
    return row


def _filename(clip: dict) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", clip["title"]).strip("-").lower()[:60] or "clip"
    return f"{slug}-{clip['clip_number']}.mp4"


def _ready(clip: dict) -> None:
    if clip["validation_status"] != "passed" or not clip.get("storage_key"):
        raise Conflict("This clip is not ready for download.", code="clip_not_ready",
                       details={"validation_status": clip["validation_status"]})


@router.get("/{clip_id}", response_model=ClipDetailOut, summary="A clip with its caption formats and validation")
def get_clip(clip_id: str, principal: Principal = Depends(rate_limited)) -> ClipDetailOut:
    clip = _owned_clip(clip_id, principal)
    captions = []
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT format, cue_count, word_timestamps, sync_validation FROM clip_captions "
                    "WHERE clip_id = %s ORDER BY format", (clip["id"],))
        for r in cur.fetchall():
            captions.append(CaptionInfo(format=r["format"], cue_count=r["cue_count"],
                                        word_timestamps=r["word_timestamps"],
                                        sync_ok=bool((r["sync_validation"] or {}).get("ok"))))
    base = ClipOut.from_row(clip).model_dump()
    validation = dict(clip.get("validation_details") or {})
    validation.pop("caption_sync", None)
    return ClipDetailOut(**base, captions=captions, validation=validation)


@router.get("/{clip_id}/download-url", response_model=DownloadUrlOut,
            summary="A short-lived signed URL for this clip's MP4")
def download_url(clip_id: str, request: Request, principal: Principal = Depends(rate_limited)) -> DownloadUrlOut:
    clip = _owned_clip(clip_id, principal)
    _ready(clip)
    storage = get_storage()
    url, expires_at = storage.signed_url(clip["storage_key"], settings.SIGNED_URL_TTL_SECONDS, _filename(clip))
    if url.startswith("/"):
        url = str(request.base_url).rstrip("/") + url
    logger.info("download link issued", extra={"event": "download_link_issued", "clip_id": clip_id})
    return DownloadUrlOut(url=url, expires_at=expires_at, filename=_filename(clip))


@router.get("/{clip_id}/download", summary="Download the clip MP4 (authenticated)",
            responses={302: {"description": "Redirect to a signed storage URL"}})
def download_clip(clip_id: str, principal: Principal = Depends(rate_limited)):
    clip = _owned_clip(clip_id, principal)
    _ready(clip)
    storage = get_storage()
    if storage.name == "local":
        return FileResponse(storage.local_path(clip["storage_key"]), media_type="video/mp4",
                            filename=_filename(clip))
    url, _ = storage.signed_url(clip["storage_key"], settings.SIGNED_URL_TTL_SECONDS, _filename(clip))
    return RedirectResponse(url, status_code=302)


@router.get("/{clip_id}/captions/{fmt}", response_class=PlainTextResponse,
            summary="Download the caption file for this clip (srt, vtt or ass)")
def download_captions(clip_id: str, fmt: str, principal: Principal = Depends(rate_limited)):
    if fmt not in CAPTION_TYPES:
        raise Unprocessable("Caption format must be srt, vtt or ass.", code="invalid_caption_format")
    clip = _owned_clip(clip_id, principal)
    _ready(clip)
    storage = get_storage()
    key = f"jobs/{clip['job_id']}/clips/{clip['clip_number']}/captions.{fmt}"
    if storage.name == "local":
        path = storage.local_path(key)
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
    else:
        fd, tmp = tempfile.mkstemp(suffix=f".{fmt}")
        os.close(fd)
        try:
            storage.download_to(key, tmp)
            with open(tmp, "r", encoding="utf-8") as fh:
                body = fh.read()
        finally:
            os.remove(tmp)
    return PlainTextResponse(body, media_type=CAPTION_TYPES[fmt])
