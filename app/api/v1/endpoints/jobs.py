"""Job management endpoints."""
import logging
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....auth import verify_api_key
from ....config import settings
from ....queue import queue_manager
from ....schemas import (
    JobCancelRequest,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobRetryRequest,
    JobStatus,
)
from ....services.supabase_client import db

logger = logging.getLogger("gta6_shorts.jobs")
router = APIRouter()


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED, tags=["Jobs"])
async def create_job(
    payload: JobCreateRequest,
    token: str = Depends(verify_api_key),
):
    """
    Create a new video-processing job.
    Accepts validated YouTube URLs, validates constraints, prevents duplicate processing,
    and enqueues the job for background execution.
    """
    # Duplicate-job check
    if not payload.force:
        active = db.find_active_job_by_url(payload.url)
        if active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An active processing job already exists for this video (Job ID: {active['id']}). Use force=true to override.",
            )

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job_record = {
        "id": job_id,
        "url": payload.url,
        "status": JobStatus.QUEUED.value,
        "stage": "pending",
        "progress": 0,
        "num_clips": payload.num_clips,
        "aspect_ratio": payload.aspect_ratio,
        "target_format": payload.target_format,
        "language": payload.language,
        "mode": payload.mode,
        "llm_provider": payload.llm_provider or settings.LLM_PROVIDER,
        "gta_preset": payload.gta_preset,
        "title_prefix": payload.title_prefix,
        "custom_prompt": payload.custom_prompt,
        "video_title": None,
        "video_duration": None,
        "error": None,
        "retry_count": 0,
    }

    db.create_job(job_record)
    db.add_log(job_id, "pending", f"Job registered and submitted to queue: {payload.url}")

    # Enqueue to background worker
    queue_manager.enqueue_job(job_id)

    full_job = db.get_job(job_id)
    return full_job


@router.get("/jobs/{job_id}", response_model=JobResponse, tags=["Jobs"])
async def get_job(
    job_id: str,
    token: str = Depends(verify_api_key),
):
    """Retrieve detailed job status, progress percentage, error messages, clips, and execution logs."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return job


@router.get("/jobs", response_model=JobListResponse, tags=["Jobs"])
async def list_jobs(
    limit: int = Query(20, ge=1, le=100, description="Max jobs to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    status: Optional[str] = Query(None, description="Filter by status: queued, processing, completed, failed, cancelled"),
    token: str = Depends(verify_api_key),
):
    """List jobs with optional status filter and pagination."""
    items, total = db.list_jobs(limit=limit, offset=offset, status=status)
    return JobListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/jobs/{job_id}/retry", response_model=JobResponse, tags=["Jobs"])
async def retry_job(
    job_id: str,
    payload: JobRetryRequest = JobRetryRequest(),
    token: str = Depends(verify_api_key),
):
    """Retry a failed or cancelled job."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

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
    db.add_log(job_id, "retry", f"Retry #{new_retry_count} initiated. Reason: {payload.reason}")

    queue_manager.enqueue_job(job_id)

    return db.get_job(job_id)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse, tags=["Jobs"])
async def cancel_job(
    job_id: str,
    payload: JobCancelRequest = JobCancelRequest(),
    token: str = Depends(verify_api_key),
):
    """Cancel an eligible (queued or processing) job."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    current_status = job.get("status")
    if current_status in (JobStatus.COMPLETED.value, JobStatus.CANCELLED.value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel a job with status '{current_status}'.",
        )

    queue_manager.cancel_job(job_id)
    db.update_job_progress(job_id, stage="cancelled", progress=job.get("progress", 0), status=JobStatus.CANCELLED.value)
    db.add_log(job_id, "cancelled", f"Job cancelled. Reason: {payload.reason}")

    return db.get_job(job_id)
