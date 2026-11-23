"""Liveness (/health) and readiness (/ready). Readiness checks dependencies without leaking details."""
from __future__ import annotations

import logging
import os
import shutil

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import settings
from ..db import repo
from ..media.ffmpeg import FFMPEG, FFPROBE

logger = logging.getLogger("studio.health")
router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness: the process is running")
def health() -> dict:
    return {"status": "ok", "version": __version__}


def _check(name: str, fn) -> tuple[str, bool, str | None]:
    try:
        fn()
        return name, True, None
    except Exception as exc:  # report the failure type only
        logger.warning("readiness check failed", extra={"check": name, "error_type": type(exc).__name__})
        return name, False, type(exc).__name__


@router.get("/ready", summary="Readiness: database, queue, storage and media tools are usable")
def ready():
    from ..queue import get_queue

    def database():
        repo.ping()

    def queue():
        if not settings.REDIS_URL:
            raise RuntimeError("REDIS_URL not set")
        if not get_queue().ping():
            raise RuntimeError("redis unreachable")

    def storage():
        if settings.STORAGE_BACKEND == "local":
            os.makedirs(settings.STORAGE_LOCAL_ROOT, exist_ok=True)
            if not os.access(settings.STORAGE_LOCAL_ROOT, os.W_OK):
                raise RuntimeError("storage not writable")
        elif not settings.SUPABASE_URL:
            raise RuntimeError("supabase not configured")

    def media_tools():
        for binary in (FFMPEG, FFPROBE):
            if shutil.which(binary) is None:
                raise RuntimeError(f"{binary} not found")

    checks = {}
    healthy = True
    for name, fn in (("database", database), ("queue", queue), ("storage", storage), ("media_tools", media_tools)):
        key, ok, err = _check(name, fn)
        checks[key] = {"ok": ok, **({"error": err} if err else {})}
        healthy = healthy and ok
    body = {"status": "ok" if healthy else "degraded", "version": __version__, "checks": checks}
    return JSONResponse(status_code=200 if healthy else 503, content=body)
