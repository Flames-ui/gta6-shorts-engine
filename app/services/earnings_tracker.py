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
    Prevents negative or inconsistent financial values.
    """

    def __init__(self, target_start_date: Optional[str] = None):
        # Explicit target start date for 30-day cycle
        self.target_start_date = target_start_date or datetime.now(timezone.utc).isoformat()

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
            "estimated_rewards": 0.0,
            "pending_rewards": 0.0,
            "approved_rewards": 0.0,
            "rejected_rewards": 0.0,
            "withdrawable_earnings": 0.0,
            "evidence_source": "operator_submission",
            "evidence_url": submission_url,
            "verified_by": None,
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
        evidence_source: str,
        verified_by: str,
        eligible_views: Optional[int] = None,
        estimated_rewards: Optional[float] = None,
        pending_rewards: Optional[float] = None,
        approved_rewards: Optional[float] = None,
        rejected_rewards: Optional[float] = None,
        withdrawable_earnings: Optional[float] = None,
        evidence_url: Optional[str] = None,
        status: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        operator_notes: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Update verified performance and reward metrics for a submission.
        Strict Financial Validation:
        - Prevents negative values.
        - Requires evidence_source and verified_by.
        - Enforces withdrawable_earnings <= approved_rewards.
        - Keeps estimated, pending, and approved rewards strictly separated.
        """
        if not evidence_source or not str(evidence_source).strip():
            raise ValueError("Evidence source is required for metrics updates.")
        if not verified_by or not str(verified_by).strip():
            raise ValueError("Verified by operator/system identifier is required.")

        now_str = datetime.now(timezone.utc).isoformat()
        updates: Dict[str, Any] = {
            "updated_at": now_str,
            "last_verified_at": now_str,
            "evidence_source": str(evidence_source).strip(),
            "verified_by": str(verified_by).strip(),
        }
        if evidence_url:
            updates["evidence_url"] = str(evidence_url).strip()

        # Validation: No negative values
        if eligible_views is not None:
            if eligible_views < 0:
                raise ValueError("eligible_views cannot be negative.")
            updates["eligible_views"] = int(eligible_views)

        if estimated_rewards is not None:
            if estimated_rewards < 0:
                raise ValueError("estimated_rewards cannot be negative.")
            updates["estimated_rewards"] = round(float(estimated_rewards), 2)

        if pending_rewards is not None:
            if pending_rewards < 0:
                raise ValueError("pending_rewards cannot be negative.")
            updates["pending_rewards"] = round(float(pending_rewards), 2)

        if approved_rewards is not None:
            if approved_rewards < 0:
                raise ValueError("approved_rewards cannot be negative.")
            updates["approved_rewards"] = round(float(approved_rewards), 2)

        if rejected_rewards is not None:
            if rejected_rewards < 0:
                raise ValueError("rejected_rewards cannot be negative.")
            updates["rejected_rewards"] = round(float(rejected_rewards), 2)

        if withdrawable_earnings is not None:
            if withdrawable_earnings < 0:
                raise ValueError("withdrawable_earnings cannot be negative.")
            updates["withdrawable_earnings"] = round(float(withdrawable_earnings), 2)

        # Check existing record for consistency
        existing = db.get_clip_submission(submission_id)
        current_approved = updates.get("approved_rewards", float(existing.get("approved_rewards") or 0.0) if existing else 0.0)
        current_withdrawable = updates.get("withdrawable_earnings", float(existing.get("withdrawable_earnings") or 0.0) if existing else 0.0)
        if current_withdrawable > current_approved:
            raise ValueError(f"Inconsistent accounting: withdrawable_earnings (${current_withdrawable:.2f}) cannot exceed approved_rewards (${current_approved:.2f}).")

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
        Strictly never counts pending or estimated rewards as confirmed earnings.
        Calculates explicit 30-day timeline.
        """
        submissions = db.list_clip_submissions()
        now = datetime.now(timezone.utc)
        
        try:
            start_dt = datetime.fromisoformat(str(self.target_start_date).replace("Z", "+00:00"))
        except Exception:
            start_dt = now

        period_start = start_dt.isoformat()
        period_end = (start_dt + timedelta(days=30)).isoformat()
        elapsed_days = (now - start_dt).days
        days_remaining = max(0, 30 - elapsed_days)

        total_submitted = len(submissions)
        total_views = sum(int(s.get("eligible_views") or 0) for s in submissions)
        total_estimated = sum(float(s.get("estimated_rewards") or 0.0) for s in submissions)
        total_pending = sum(float(s.get("pending_rewards") or 0.0) for s in submissions)
        total_approved = sum(float(s.get("approved_rewards") or 0.0) for s in submissions)
        total_rejected = sum(float(s.get("rejected_rewards") or 0.0) for s in submissions)
        total_withdrawable = sum(float(s.get("withdrawable_earnings") or 0.0) for s in submissions)

        # Progress calculation: strictly based on confirmed/approved rewards ONLY
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
            days_remaining=days_remaining,
        )


earnings_tracker = EarningsTracker()
