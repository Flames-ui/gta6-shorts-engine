import os
from datetime import datetime, timezone
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

from ....schemas import OperatorOverviewResponse
from ....services.marketplaces.content_rewards_operator import content_rewards_operator
from ....services.operator_action_queue import operator_action_queue
from ....services.earnings_tracker import earnings_tracker
from datetime import datetime, timezone

@router.get("/operator-overview", response_model=OperatorOverviewResponse)
async def get_operator_overview(auth: AuthContext = Depends(verify_api_key)):
    """
    Returns real-time dashboard metrics required for Milestone 2:
    - Live marketplace session state.
    - Discovered, eligible, approved, awaiting review, awaiting brand approval, exhausted/expired campaigns.
    - Candidate moments analyzed/rejected, clips selected, rendered, QC passed, compliance passed.
    - Eligible views, pending rewards, approved earnings, withdrawable balance, remaining to $500 target.
    - Daily production counter (3-5 clips target).
    - Pending operator action queue.
    """
    user_id = None if auth.is_admin else auth.user_id

    # 1. Marketplace session state
    session_status = content_rewards_operator.check_session_status()

    # 2. Campaigns breakdown
    all_campaigns_raw = db.list_campaigns(limit=500)
    all_campaigns = all_campaigns_raw[0] if isinstance(all_campaigns_raw, tuple) else (all_campaigns_raw or [])
    campaign_counts = {
        "discovered": len(all_campaigns),
        "eligible": sum(1 for c in all_campaigns if c.get("status") == "eligible"),
        "approved": sum(1 for c in all_campaigns if c.get("operator_approved") is True),
        "awaiting_review": sum(1 for c in all_campaigns if c.get("status") == "needs_review"),
        "awaiting_brand_approval": sum(1 for c in all_campaigns if c.get("application_status") == "awaiting_brand_approval"),
        "exhausted_or_expired": sum(1 for c in all_campaigns if c.get("status") in ("exhausted", "expired")),
    }

    # 3. Production pipeline counts
    clips, _ = db.list_clips(user_id=user_id, limit=500)
    submissions = db.list_clip_submissions()

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_clips = sum(1 for c in clips if (c.get("created_at") or "").startswith(today_str))

    analyzed_candidates_count = db.count_candidate_evaluations()
    rejected_candidates_count = db.count_candidate_evaluations(verdict="reject")

    pipeline_counts = {
        "candidate_moments_analyzed": analyzed_candidates_count,
        "candidate_moments_rejected": rejected_candidates_count,
        "clips_selected": len(clips),
        "clips_rendered": sum(1 for c in clips if (c.get("video_path") and os.path.isfile(c.get("video_path"))) or (c.get("storage_path") and os.path.isfile(c.get("storage_path")))),
        "clips_passing_quality_control": sum(1 for c in clips if c.get("status") in ("approved", "completed", "ready")),
        "clips_passing_compliance": sum(1 for c in clips if c.get("compliance_status") in ("compliant", "approved") or c.get("is_compliant")),
        "clips_awaiting_operator_approval": sum(1 for c in clips if c.get("publishing_status") in ("awaiting_approval", "pending_review")),
        "clips_published": sum(1 for c in clips if c.get("publishing_status") == "published"),
        "clips_submitted": len(submissions),
        "clips_approved": sum(1 for s in submissions if s.get("status") == "approved" or float(s.get("approved_rewards") or 0.0) > 0),
        "clips_rejected": sum(1 for s in submissions if s.get("status") == "rejected" or float(s.get("rejected_rewards") or 0.0) > 0),
        "daily_production_counter": {
            "current_today": today_clips,
            "target_min": 3,
            "target_max": 5,
            "target_met": (3 <= today_clips <= 5),
        },
    }

    # 4. Financial metrics & $500 target
    earnings = earnings_tracker.get_summary(target_amount=500.0)
    remaining_to_target = max(0.0, round(500.0 - earnings.total_approved_rewards, 2))

    financial_metrics = {
        "eligible_views": earnings.total_eligible_views,
        "pending_rewards": earnings.total_pending_rewards,
        "approved_earnings": earnings.total_approved_rewards,
        "withdrawable_balance": earnings.total_withdrawable_earnings,
        "target_amount": 500.00,
        "remaining_to_target": remaining_to_target,
        "progress_percent": earnings.progress_percent,
        "days_remaining": earnings.days_remaining,
    }

    # 5. Operator action queue
    actions = operator_action_queue.list_pending_actions()

    return OperatorOverviewResponse(
        marketplace_session=session_status,
        campaigns=campaign_counts,
        production_pipeline=pipeline_counts,
        financial_metrics=financial_metrics,
        operator_action_queue={
            "pending_count": len(actions),
            "pending_actions": actions,
        },
    )
