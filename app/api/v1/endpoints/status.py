"""Engine status and capabilities endpoint."""
from fastapi import APIRouter, Depends
from ....auth import AuthContext, verify_api_key
from ....config import settings
from ....queue import queue_manager
from ....schemas import StatusResponse
from ....services.supabase_client import db
from ....services.youtube_publisher import youtube_publisher

router = APIRouter()


@router.get("/status", response_model=StatusResponse, tags=["Engine Status"])
async def get_engine_status(auth: AuthContext = Depends(verify_api_key)):
    """Retrieve detailed engine metrics, worker count, and capabilities."""
    queue_stats = queue_manager.get_stats()

    # Query counts from database
    active_jobs = 0
    queued_jobs = queue_stats.get("queued_jobs", 0)
    completed_jobs = 0
    failed_jobs = 0

    try:
        # Get count summaries
        items, total = db.list_jobs(limit=100, offset=0)
        for it in items:
            st = it.get("status")
            if st == "processing":
                active_jobs += 1
            elif st == "completed":
                completed_jobs += 1
            elif st == "failed":
                failed_jobs += 1
    except Exception:
        pass

    storage_provider = "supabase_storage" if db.has_supabase else "local_disk"

    return StatusResponse(
        status="operational",
        version=settings.APP_VERSION,
        active_jobs=active_jobs,
        queued_jobs=queued_jobs,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
        workers_online=queue_stats.get("workers_online", 0),
        supported_modes=["local", "api"],
        default_llm_provider=settings.LLM_PROVIDER,
        storage_provider=storage_provider,
        youtube_oauth_configured=youtube_publisher.is_configured(),
    )
