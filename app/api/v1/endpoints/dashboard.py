"""Dashboard overview statistics endpoint for Lovable frontend integration."""
from fastapi import APIRouter, Depends
from ....auth import AuthContext, verify_api_key
from ....queue import job_queue
from ....schemas import DashboardStatsResponse
from ....services.categories import category_service
from ....services.supabase_client import db

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(auth: AuthContext = Depends(verify_api_key)):
    """Retrieve comprehensive platform metrics for Lovable dashboard cards."""
    user_id = None if auth.is_admin else auth.user_id

    # Jobs counts
    jobs, _ = db.list_jobs(user_id=user_id, limit=500)
    total_jobs = len(jobs)
    active_jobs = sum(1 for j in jobs if j.get("status") in ("queued", "processing"))
    completed_jobs = sum(1 for j in jobs if j.get("status") == "completed")
    failed_jobs = sum(1 for j in jobs if j.get("status") == "failed")

    # Clips counts
    clips, _ = db.list_clips(user_id=user_id, limit=500)
    total_clips = len(clips)
    approved_clips = sum(1 for c in clips if c.get("publishing_status") in ("approved", "awaiting_approval"))
    scheduled_clips = sum(1 for c in clips if c.get("publishing_status") == "scheduled")
    published_clips = sum(1 for c in clips if c.get("publishing_status") == "published")

    # Sources and Categories
    sources = db.list_sources(user_id=user_id)
    active_categories = len(category_service.list_categories(active_only=True))

    # Worker queue health
    q_stats = job_queue.get_stats()
    workers_online = q_stats.get("workers_online", 0)

    return DashboardStatsResponse(
        total_jobs=total_jobs,
        active_jobs=active_jobs,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
        total_clips=total_clips,
        approved_clips=approved_clips,
        scheduled_clips=scheduled_clips,
        published_clips=published_clips,
        total_sources=len(sources),
        active_categories=active_categories,
        workers_online=workers_online,
    )
