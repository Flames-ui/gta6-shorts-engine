"""Editing job endpoints: create, list, inspect, cancel, and read the five clips."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse

from ... import states
from ...auth import Principal
from ...db import repo
from ...errors import Conflict, NotFound, ServiceUnavailable, Unprocessable
from ...queue import get_queue
from ..deps import optional_header, rate_limited
from ..schemas import ClipOut, EventOut, JobCreateIn, JobDetailOut, JobListOut, JobOut
from .sources import _owned_source

logger = logging.getLogger("studio.api.jobs")
router = APIRouter(prefix="/jobs", tags=["jobs"])


def _owned_job(job_id: str, principal: Principal):
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise NotFound("Job was not found.") from exc
    row = repo.get_job(job_id, owner_id=principal.user_id)
    if row is None:
        raise NotFound("Job was not found.")
    return row


@router.post("", status_code=202, response_model=JobOut,
             summary="Create an editing job for a source (returns once it is persisted and queued)",
             responses={200: {"description": "Idempotent replay of an existing job"}})
def create_job(body: JobCreateIn, principal: Principal = Depends(rate_limited),
               idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    key = optional_header(idempotency_key)
    if key is not None and not (8 <= len(key) <= 128):
        raise Unprocessable("Idempotency-Key must be between 8 and 128 characters.", code="invalid_idempotency_key")

    if key:
        existing = repo.find_job_by_idempotency(principal.user_id, key)
        if existing:
            return JSONResponse(status_code=200, content=JobOut.from_row(existing).model_dump(mode="json"))

    source = _owned_source(str(body.source_id), principal)
    if source["status"] not in ("ready", "pending_retrieval"):
        raise Conflict("This source is not ready for editing.", code="source_not_ready",
                       details={"source_status": source["status"]})

    try:
        job = repo.create_job(
            principal.user_id,
            str(body.source_id),
            clip_count=body.clip_count,
            aspect_ratio="9:16",
            caption_preset=body.caption_preset,
            language=body.language,
            min_clip_seconds=body.min_clip_seconds,
            max_clip_seconds=body.max_clip_seconds,
            requested_config={"clip_count": body.clip_count, "caption_preset": body.caption_preset},
            idempotency_key=key,
        )
    except psycopg.errors.UniqueViolation:
        existing = repo.find_job_by_idempotency(principal.user_id, key) if key else None
        if existing:
            return JSONResponse(status_code=200, content=JobOut.from_row(existing).model_dump(mode="json"))
        raise Conflict("A job with this request already exists.", code="duplicate_job")

    job_id = str(job["id"])
    try:
        get_queue().enqueue(job_id)
    except ServiceUnavailable as exc:
        # The job was persisted; record the failure honestly rather than leaving it queued forever.
        repo.transition_job(job_id, states.FAILED, error_code="queue_unavailable",
                            error_message="The processing queue was unavailable when the job was submitted.",
                            message="Enqueue failed")
        raise exc
    logger.info("job queued", extra={"event": "job_created", "job_id": job_id})
    return JobOut.from_row(repo.get_job(job_id))


@router.get("", response_model=JobListOut, summary="List your jobs, newest first")
def list_jobs(principal: Principal = Depends(rate_limited),
              status: Optional[str] = Query(default=None),
              limit: int = Query(default=20, ge=1, le=100),
              offset: int = Query(default=0, ge=0, le=10_000)) -> JobListOut:
    if status is not None and status not in states.ALL:
        raise Unprocessable("Unknown job status filter.", code="invalid_status_filter")
    result = repo.list_jobs(principal.user_id, status=status, limit=limit, offset=offset)
    return JobListOut(items=[JobOut.from_row(r) for r in result["items"]], total=result["total"],
                      limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobDetailOut, summary="Job status, progress, clips and recent events")
def get_job(job_id: str, principal: Principal = Depends(rate_limited)) -> JobDetailOut:
    job = _owned_job(job_id, principal)
    clips = [ClipOut.from_row(c) for c in repo.list_clips(job_id)]
    highlights = repo.list_highlights(job_id)
    events = [EventOut(event_type=e["event_type"], from_status=e["from_status"], to_status=e["to_status"],
                       stage=e["stage"], message=e["message"], created_at=e["created_at"])
              for e in repo.list_events(job_id)[-50:]]
    base = JobOut.from_row(job).model_dump()
    return JobDetailOut(**base, clips=clips, highlight_count=len(highlights), events=events)


@router.post("/{job_id}/cancel", status_code=202, response_model=JobOut,
             summary="Request cancellation of a running or queued job")
def cancel_job(job_id: str, principal: Principal = Depends(rate_limited)) -> JobOut:
    job = _owned_job(job_id, principal)
    if job["status"] in states.TERMINAL:
        raise Conflict("This job has already finished.", code="job_already_finished",
                       details={"status": job["status"]})
    repo.request_cancel(job_id, principal.user_id)
    return JobOut.from_row(repo.get_job(job_id, owner_id=principal.user_id))


@router.get("/{job_id}/clips", response_model=list[ClipOut], summary="The clips produced by a job")
def list_job_clips(job_id: str, principal: Principal = Depends(rate_limited)) -> list[ClipOut]:
    job = _owned_job(job_id, principal)
    return [ClipOut.from_row(c) for c in repo.list_clips(str(job["id"]))]
