"""Usage for the signed-in user over a recent window."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from ...auth import Principal
from ...db import repo
from ..deps import rate_limited
from ..schemas import UsageOut

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=UsageOut, summary="Jobs and processing usage for the last N days")
def usage(days: int = Query(default=30, ge=1, le=365), principal: Principal = Depends(rate_limited)) -> UsageOut:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    jobs = repo.owner_job_counts(principal.user_id, start)
    metrics = repo.usage_summary(principal.user_id, start)
    return UsageOut(period_start=start, period_end=end, jobs=jobs, metrics=metrics)
