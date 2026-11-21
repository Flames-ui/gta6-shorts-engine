"""Job management endpoints with tenant isolation and strict validation."""
import logging
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....auth import AuthContext, enforce_resource_ownership, verify_api_key
from ....config import settings
from ....queue import job_queue
from ....schemas import (
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobStatus,
)
from ....services.categories import category_service
from ....services.supabase_client import db
from ....services.video_processor import process_video_job

logger = logging.getLogger("gta6_shorts.jobs")
router = APIRouter(tags=["Jobs"])


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Create a new video-processing job.
    Accepts validated URLs, assigns category, verifies duplicate jobs,
    and enqueues processing to the background worker.
    """
    # Verify category exists
    cat = category_service.get_category(payload.category_id)
    if not cat:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Category '{payload.category_id}' is not recognized.",
        )

    # Duplicate check
    active = db.find_active_job_by_url(payload.url)
    if active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active processing job already exists for this source (Job ID: {active['id']}).",
        )

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job_record = {
        "id": job_id,
        "user_id": auth.user_id if auth.user_id != "system_admin" else None,
        "url": payload.url,
        "category_id": payload.category_id,
        "status": JobStatus.QUEUED.value,
        "stage": "pending",
        "progress": 0,
        "num_clips": payload.num_clips,
        "aspect_ratio": payload.aspect_ratio,
        "target_format": payload.target_format,
        "language": payload.language,
        "mode": "local",
        "llm_provider": payload.llm_provider or settings.LLM_PROVIDER,
        "custom_prompt": payload.custom_prompt,
        "video_title": None,
        "video_duration": None,
        "error": None,
        "retry_count": 0,
    }

    db.create_job(job_record)
    db.add_log(job_id, "pending", f"Job registered for category '{payload.category_id}' and enqueued: {payload.url}")

    # Enqueue to Redis RQ background worker
    job_queue.enqueue_job(process_video_job, job_id)

    full_job = db.get_job(job_id)
    return full_job


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """Retrieve detailed job status, progress, clips, and execution logs with ownership verification."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    enforce_resource_ownership(job.get("user_id"), auth, "job")
    return job


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    limit: int = Query(20, ge=1, le=100, description="Max jobs to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    status: Optional[str] = Query(None, description="Filter by status: queued, processing, completed, failed, cancelled"),
    auth: AuthContext = Depends(verify_api_key),
):
    """List jobs with optional status filter and pagination. Non-admin users see only their own jobs."""
    user_filter = None if auth.is_admin else auth.user_id
    items, total = db.list_jobs(limit=limit, offset=offset, status=status, user_id=user_filter)
    return JobListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/jobs/{job_id}/retry", response_model=JobResponse)
async def retry_job(
    job_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """Retry a failed or cancelled job."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    enforce_resource_ownership(job.get("user_id"), auth, "job")

    if job.get("status") == JobStatus.PROCESSING.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot retry an actively processing job.")

    new_retry_count = int(job.get("retry_count", 0)) + 1
    db.update_job_progress(
        job_id,
        stage="pending",
        progress=0,
        status=JobStatus.QUEUED.value,
        error=None,
        retry_count=new_retry_count,
    )
    db.add_log(job_id, "retry", f"Retry #{new_retry_count} initiated.")

    job_queue.enqueue_job(process_video_job, job_id)
    return db.get_job(job_id)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """Cancel an active or queued job."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    enforce_resource_ownership(job.get("user_id"), auth, "job")

    current_status = job.get("status")
    if current_status in (JobStatus.COMPLETED.value, JobStatus.CANCELLED.value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel a job with status '{current_status}'.",
        )

    job_queue.cancel_job(job_id)
    db.update_job_progress(job_id, stage="cancelled", progress=job.get("progress", 0), status=JobStatus.CANCELLED.value)
    db.add_log(job_id, "cancelled", "Job cancelled by user request.")
    return db.get_job(job_id)
