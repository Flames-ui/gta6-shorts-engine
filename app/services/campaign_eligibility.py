"""Central campaign eligibility gate service enforcing strict production verification."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..schemas import CampaignStatus

logger = logging.getLogger("gta6_shorts.campaign_eligibility")


class CampaignEligibilityService:
    """
    Evaluates campaign eligibility and enforces the production gate:
    No campaign, no clipping. No verified earning opportunity, no production.
    """

    def evaluate_campaign(self, campaign: Dict[str, Any]) -> Tuple[str, List[str], Dict[str, bool]]:
        """
        Evaluate campaign attributes against strict qualification criteria.
        Returns (new_status, reasons, checks_dict).
        """
        checks = {
            "has_supported_platform": False,
            "is_gaming_category": False,
            "has_reward_model": False,
            "not_expired": True,
            "not_exhausted": True,
            "rules_unambiguous": True,
        }
        reasons = []

        # 1. Platform Check (TikTok and YouTube Shorts only)
        platforms = [p.lower() for p in (campaign.get("accepted_platforms") or [])]
        if any(p in ["tiktok", "youtube_shorts"] for p in platforms):
            checks["has_supported_platform"] = True
        else:
            reasons.append("Campaign does not accept supported platforms (TikTok or YouTube Shorts).")

        # 2. Content & Gaming Category Check
        games = [g.lower() for g in (campaign.get("eligible_games") or [])]
        categories = [c.lower() for c in (campaign.get("content_categories") or [])]
        if any("gta" in g or "gaming" in g or "game" in g or "cod" in g or "duty" in g for g in games) or "gaming" in categories:
            checks["is_gaming_category"] = True
        else:
            reasons.append("Campaign is not associated with GTA 6 or an eligible gaming category.")

        # 3. Reward Model Check
        reward_model = campaign.get("reward_model") or "cpm"
        cpm = float(campaign.get("cpm") or 0.0)
        payout = float(campaign.get("payout_amount") or 0.0)
        if reward_model in ["cpm", "prize_pool", "fixed_per_clip", "bounty"] and (cpm > 0 or payout > 0):
            checks["has_reward_model"] = True
        else:
            reasons.append("Campaign lacks a verified, positive earning structure (CPM or Prize Pool).")

        # 4. Deadline / Expiration Check
        now = datetime.now(timezone.utc)
        deadline_str = campaign.get("deadline") or campaign.get("submission_deadline")
        if deadline_str:
            try:
                deadline_dt = datetime.fromisoformat(str(deadline_str).replace("Z", "+00:00"))
                if deadline_dt < now:
                    checks["not_expired"] = False
                    reasons.append(f"Campaign deadline ({deadline_str}) has passed.")
            except Exception:
                pass

        # 5. Budget / Exhaustion Check
        max_views = int(campaign.get("max_eligible_views") or 0)
        if campaign.get("status") == CampaignStatus.EXHAUSTED.value:
            checks["not_exhausted"] = False
            reasons.append("Campaign budget is exhausted.")

        # 6. Ambiguities & Review Check
        ambiguities = campaign.get("ambiguities") or []
        if ambiguities:
            checks["rules_unambiguous"] = False
            for amb in ambiguities:
                reasons.append(f"Rule ambiguity: {amb}")

        # Compute Final Status
        if not checks["not_expired"]:
            final_status = CampaignStatus.EXPIRED.value
        elif not checks["not_exhausted"]:
            final_status = CampaignStatus.EXHAUSTED.value
        elif not checks["has_supported_platform"] or not checks["is_gaming_category"]:
            final_status = CampaignStatus.REJECTED.value
        elif not checks["has_reward_model"] or not checks["rules_unambiguous"]:
            final_status = CampaignStatus.NEEDS_REVIEW.value
        else:
            if campaign.get("operator_approved"):
                final_status = CampaignStatus.APPROVED.value
            else:
                final_status = CampaignStatus.ELIGIBLE.value

        return final_status, reasons, checks

    def validate_clipping_gate(
        self,
        campaign: Optional[Dict[str, Any]],
        source: Optional[Dict[str, Any]],
        target_platform: str = "tiktok",
        planned_duration: Optional[float] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Enforce the central production gate before clipping starts.
        Rejects job if campaign is not active, platform is not accepted,
        or source footage lacks verified permissions.
        """
        details = {
            "campaign_active": False,
            "platform_accepted": False,
            "source_associated": False,
            "source_verified": False,
            "platform_authorized": False,
            "derivative_use_permitted": False,
            "source_not_expired": True,
            "duration_compliant": True,
        }

        if not campaign:
            return False, "Gate Rejected: No valid campaign associated with clipping request.", details

        # 1. Campaign Active Check
        campaign_status = str(campaign.get("status") or "").lower()
        if campaign_status == CampaignStatus.EXPIRED.value:
            return False, "Gate Rejected: Campaign status is 'expired'. Production requires an eligible or approved campaign.", details
        campaign_status = campaign.get("status")
        if campaign_status not in [CampaignStatus.APPROVED.value, CampaignStatus.ELIGIBLE.value]:
            return False, f"Gate Rejected: Campaign status is '{campaign_status}'. Production requires an eligible or approved campaign.", details
        details["campaign_active"] = True

        # 2. Target Platform Accepted
        accepted_platforms = [p.lower() for p in (campaign.get("accepted_platforms") or [])]
        if target_platform.lower() not in accepted_platforms:
            return False, f"Gate Rejected: Target platform '{target_platform}' is not accepted by campaign {campaign.get('id')}.", details
        details["platform_accepted"] = True

        # 3. Source Footage Presence & Verification
        if not source:
            return False, "Gate Rejected: Missing authorized source footage record.", details

        # Verify source is linked to this campaign
        if str(source.get("campaign_id")) != str(campaign.get("id")):
            return False, f"Gate Rejected: Source footage {source.get('id')} is not associated with campaign {campaign.get('id')}.", details
        details["source_associated"] = True

        # Verify source rights status
        verification_status = source.get("source_verification_status")
        if verification_status != "verified":
            return False, f"Gate Rejected: Source footage permission is not verified (status: '{verification_status}').", details
        details["source_verified"] = True

        # Verify target platform is authorized on source
        authorized_platforms = [p.lower() for p in (source.get("authorized_platforms") or [])]
        if target_platform.lower() not in authorized_platforms:
            return False, f"Gate Rejected: Source footage rights do not authorize platform '{target_platform}'.", details
        details["platform_authorized"] = True

        # Verify derivative clipping is allowed
        allowed_uses = source.get("allowed_derivative_uses") or []
        if "short_form_clipping" not in allowed_uses and "clipping" not in allowed_uses:
            return False, "Gate Rejected: Source footage does not permit derivative short-form clipping.", details
        details["derivative_use_permitted"] = True

        # Verify permission expiration
        expiry_str = source.get("permission_expiry")
        if expiry_str:
            try:
                now = datetime.now(timezone.utc)
                expiry_dt = datetime.fromisoformat(str(expiry_str).replace("Z", "+00:00"))
                if expiry_dt < now:
                    details["source_not_expired"] = False
                    return False, f"Gate Rejected: Source permission expired on {expiry_str}.", details
            except Exception:
                pass

        # Verify duration constraints if planned duration is known
        if planned_duration is not None:
            min_dur = int(campaign.get("min_clip_duration_seconds") or 15)
            max_dur = int(campaign.get("max_clip_duration_seconds") or 60)
            if planned_duration < min_dur or planned_duration > max_dur:
                details["duration_compliant"] = False
                return False, f"Gate Rejected: Planned duration ({planned_duration:.1f}s) outside campaign range [{min_dur}s, {max_dur}s].", details

        return True, "Gate Approved: Campaign is active and source footage is authorized for production.", details


campaign_eligibility_service = CampaignEligibilityService()
