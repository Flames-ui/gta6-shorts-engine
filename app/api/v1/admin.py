"""Operator diagnostics. Never returns secrets, tokens or stack traces."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends

from ...auth import Principal, require_admin
from ...config import settings
from ...db import repo
from ...queue import get_queue

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/diagnostics", summary="Operator view: job counts, workers, queue, configuration summary")
def diagnostics(_: Principal = Depends(require_admin)) -> dict:
    stale = repo.jobs_with_stale_heartbeat(settings.STALE_HEARTBEAT_SECONDS)
    workers = repo.list_workers(settings.STALE_HEARTBEAT_SECONDS)
    try:
        queue_depth = get_queue().depth()
        queue_ok = True
    except Exception:
        queue_depth, queue_ok = None, False
    return {
        "jobs_by_status": repo.job_status_counts(),
        "stale_active_jobs": [{"job_id": str(j["id"]), "status": j["status"], "stage": j["stage"],
                               "heartbeat_at": j["heartbeat_at"]} for j in stale[:50]],
        "workers": [{"worker_id": w["worker_id"], "status": w["status"], "fresh": w["is_fresh"],
                     "last_seen_at": w["last_seen_at"], "version": w["version"]} for w in workers],
        "queue": {"reachable": queue_ok, "depth": queue_depth},
        "configuration": {
            "environment": settings.ENVIRONMENT,
            "storage_backend": settings.STORAGE_BACKEND,
            "youtube_retrieval_enabled": settings.YOUTUBE_RETRIEVAL_ENABLED,
            "ai_provider": settings.AI_PROVIDER,
            "whisper_model": settings.WHISPER_MODEL,
            "clip_count": settings.CLIP_COUNT,
            "max_source_duration_seconds": settings.MAX_SOURCE_DURATION_SECONDS,
            "max_upload_bytes": settings.MAX_UPLOAD_BYTES,
            "min_clip_score": settings.MIN_CLIP_SCORE,
            "max_parallel_renders": settings.MAX_PARALLEL_RENDERS,
            "ffmpeg_available": shutil.which("ffmpeg") is not None,
        },
    }
