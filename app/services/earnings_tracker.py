"""Earnings tracking foundations service maintaining verified rewards and target progression."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..schemas import EarningsSummaryResponse
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.earnings_tracker")


class EarningsTracker:
    """
    Maintains verified campaign submission records, view qualification,
    and reward accounting. Strictly avoids counting pending or estimated
    rewards as confirmed earnings. Tracks progress toward $500 30-day target.
    """

    def record_submission(
        self,
        clip_id: str,
        campaign_id: str,
        platform: str,
        submission_url: str,
        submission_post_id: Optional[str] = None,
        submission_evidence: Optional[Dict[str, Any]] = None,
        operator_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a live clip submission with marketplace verification evidence."""
        sub_id = f"sub_{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()

        submission_record = {
            "id": sub_id,
            "clip_id": clip_id,
            "campaign_id": campaign_id,
            "platform": platform,
            "submitted_at": now_str,
            "submission_url": submission_url,
            "submission_post_id": submission_post_id,
            "submission_evidence": submission_evidence or {},
            "status": "submitted",
            "eligible_views": 0,
            "pending_rewards": 0.0,
            "approved_rewards": 0.0,
            "rejected_rewards": 0.0,
            "withdrawable_earnings": 0.0,
            "rejection_reason": None,
            "last_verified_at": now_str,
            "operator_notes": operator_notes,
            "created_at": now_str,
            "updated_at": now_str,
        }

        created = db.create_clip_submission(submission_record)
        logger.info("Recorded submission %s for clip %s on campaign %s", sub_id, clip_id, campaign_id)
        return created

    def update_metrics(
        self,
        submission_id: str,
        eligible_views: Optional[int] = None,
        pending_rewards: Optional[float] = None,
        approved_rewards: Optional[float] = None,
        rejected_rewards: Optional[float] = None,
        withdrawable_earnings: Optional[float] = None,
        status: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        operator_notes: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update verified performance and reward metrics for a submission."""
        now_str = datetime.now(timezone.utc).isoformat()
        updates: Dict[str, Any] = {"updated_at": now_str, "last_verified_at": now_str}

        if eligible_views is not None:
            updates["eligible_views"] = int(eligible_views)
        if pending_rewards is not None:
            updates["pending_rewards"] = float(pending_rewards)
        if approved_rewards is not None:
            updates["approved_rewards"] = float(approved_rewards)
        if rejected_rewards is not None:
            updates["rejected_rewards"] = float(rejected_rewards)
        if withdrawable_earnings is not None:
            updates["withdrawable_earnings"] = float(withdrawable_earnings)
        if status is not None:
            updates["status"] = status
        if rejection_reason is not None:
            updates["rejection_reason"] = rejection_reason
        if operator_notes is not None:
            updates["operator_notes"] = operator_notes

        return db.update_clip_submission(submission_id, updates)

    def get_summary(self, target_amount: float = 500.00) -> EarningsSummaryResponse:
        """
        Compute aggregate earnings statistics and progress toward 30-day target.
        Strictly never counts pending rewards as confirmed earnings.
        """
        submissions = db.list_clip_submissions()
        now = datetime.now(timezone.utc)
        period_start = (now - timedelta(days=1)).isoformat()
        period_end = (now + timedelta(days=29)).isoformat()

        total_submitted = len(submissions)
        total_views = sum(int(s.get("eligible_views") or 0) for s in submissions)
        total_pending = sum(float(s.get("pending_rewards") or 0.0) for s in submissions)
        total_approved = sum(float(s.get("approved_rewards") or 0.0) for s in submissions)
        total_rejected = sum(float(s.get("rejected_rewards") or 0.0) for s in submissions)
        total_withdrawable = sum(float(s.get("withdrawable_earnings") or 0.0) for s in submissions)

        # Progress calculation: strictly based on confirmed/approved rewards
        progress_pct = round((total_approved / target_amount) * 100.0, 2) if target_amount > 0 else 0.0

        return EarningsSummaryResponse(
            target_amount=target_amount,
            period_start=period_start,
            period_end=period_end,
            total_submitted_clips=total_submitted,
            total_eligible_views=total_views,
            total_pending_rewards=round(total_pending, 2),
            total_approved_rewards=round(total_approved, 2),
            total_rejected_rewards=round(total_rejected, 2),
            total_withdrawable_earnings=round(total_withdrawable, 2),
            progress_percent=progress_pct,
            days_remaining=29,
        )


earnings_tracker = EarningsTracker()
