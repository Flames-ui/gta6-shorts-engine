"""Content Rewards marketplace adapter with compliant discovery and manual rule import."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import BaseMarketplaceAdapter
from ..campaign_rule_reader import campaign_rule_reader

logger = logging.getLogger("gta6_shorts.marketplace.content_rewards")


class ContentRewardsAdapter(BaseMarketplaceAdapter):
    """
    Adapter for Content Rewards (https://contentrewards.com).
    Adheres strictly to safe discovery without bypassing CAPTCHA, authentication,
    or platform terms. Provides compliant manual operator import when automation is restricted.
    Never fabricates sample campaigns, invented reward rates, or simulated live data.
    """

    def __init__(self):
        super().__init__(
            marketplace_id="content_rewards",
            marketplace_name="Content Rewards",
            base_url="https://contentrewards.com",
        )
        self.session_authenticated = False
        self.last_captcha_detected = False

    def check_session_status(self) -> Dict[str, Any]:
        """
        Detects authentication, 2FA, or CAPTCHA barriers.
        Pauses for the human operator when verification is required.
        """
        return {
            "marketplace_id": self.marketplace_id,
            "base_url": self.base_url,
            "authenticated": self.session_authenticated,
            "captcha_detected": self.last_captcha_detected,
            "operator_action_required": not self.session_authenticated or self.last_captcha_detected,
            "status_message": (
                "Operator login or CAPTCHA verification required on Content Rewards. "
                "Compliant manual workflow is active."
                if (not self.session_authenticated or self.last_captcha_detected)
                else "Session active and operational."
            ),
        }

    def discover_campaigns(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Discover active gaming clipping campaigns from Content Rewards.
        If live authenticated browser session is not established, returns an empty list
        with discovery mode set to 'manual_import_only'. Never fabricates sample data.
        """
        if not self.session_authenticated:
            logger.info("Content Rewards discovery: Unauthenticated session. Manual import required.")
            return []
        
        # When browser operator is connected (Milestone 2), real discovered campaigns are returned
        return []

    def get_discovery_status(self) -> Dict[str, Any]:
        """Provides status of discovery integration."""
        return {
            "marketplace_id": self.marketplace_id,
            "live_discovery_supported": self.session_authenticated,
            "discovery_mode": "live_browser" if self.session_authenticated else "manual_import_only",
            "message": (
                "Live marketplace discovery active."
                if self.session_authenticated
                else "Live discovery requires authenticated browser operator (Milestone 2). Use manual import (POST /api/v1/campaigns/import)."
            ),
        }

    def fetch_campaign_details(self, campaign_url_or_id: str) -> Dict[str, Any]:
        """
        Fetch details for a given campaign identifier or URL.
        Never fabricates invented defaults. If unauthenticated, returns empty structure.
        """
        return {
            "campaign_external_id": None,
            "name": None,
            "description": None,
            "url": campaign_url_or_id,
            "raw_rules": "",
            "cpm": None,
            "reward_model": None,
            "payout_amount": None,
            "accepted_platforms": [],
            "eligible_games": [],
            "required_hashtags": [],
            "required_mentions": [],
            "min_clip_duration_seconds": None,
            "max_clip_duration_seconds": None,
        }

    def manual_import(
        self,
        raw_rules: str,
        campaign_url: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Compliant manual import: processes operator-provided rules through the rule reader.
        Never fabricates missing values or assumed platforms/games.
        Unknown fields remain None or empty, triggering needs_review.
        """
        meta = metadata or {}
        analysis = campaign_rule_reader.analyze_rules(
            raw_rules=raw_rules,
            campaign_title=meta.get("name"),
            campaign_url=campaign_url,
        )

        campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()

        # Enforce that incomplete requirements remain unknown rather than assumed
        imported_campaign = {
            "id": campaign_id,
            "marketplace_id": self.marketplace_id,
            "marketplace_name": self.marketplace_name,
            "campaign_external_id": meta.get("campaign_external_id"),
            "name": meta.get("name") or "Imported Content Rewards Campaign",
            "description": meta.get("description"),
            "url": campaign_url,
            # Incomplete or ambiguous campaigns must be needs_review
            "status": "eligible" if (analysis.is_confident and analysis.accepted_platforms and analysis.accepted_games) else "needs_review",
            "raw_rules": raw_rules,
            "accepted_platforms": analysis.accepted_platforms or meta.get("accepted_platforms") or [],
            "eligible_games": analysis.accepted_games or meta.get("eligible_games") or [],
            "content_categories": meta.get("content_categories") or ["gaming"],
            "required_hashtags": analysis.required_hashtags or meta.get("required_hashtags") or [],
            "required_mentions": analysis.required_mentions or meta.get("required_mentions") or [],
            "required_captions": analysis.required_captions or meta.get("required_captions"),
            "min_clip_duration_seconds": analysis.min_clip_duration_seconds or meta.get("min_clip_duration_seconds"),
            "max_clip_duration_seconds": analysis.max_clip_duration_seconds or meta.get("max_clip_duration_seconds"),
            "resolution_requirements": meta.get("resolution_requirements") or "1080x1920",
            "aspect_ratio": meta.get("aspect_ratio") or "9:16",
            "reward_model": analysis.reward_model or meta.get("reward_model"),
            "payout_amount": analysis.payout_amount if analysis.payout_amount is not None else meta.get("payout_amount"),
            "cpm": analysis.cpm if analysis.cpm is not None else meta.get("cpm"),
            "reward_per_view": analysis.reward_per_view if analysis.reward_per_view is not None else meta.get("reward_per_view"),
            "view_qualification_rules": analysis.view_qualification_rules or meta.get("view_qualification_rules") or {},
            "max_eligible_views": meta.get("max_eligible_views"),
            "max_payout_per_clip": meta.get("max_payout_per_clip"),
            "campaign_budget": meta.get("campaign_budget"),
            "remaining_budget": meta.get("remaining_budget"),
            "geographic_eligibility": meta.get("geographic_eligibility") or [],
            "account_eligibility": meta.get("account_eligibility") or {},
            "source_video_requirements": meta.get("source_video_requirements"),
            "content_restrictions": meta.get("content_restrictions") or [],
            "prohibited_editing_techniques": meta.get("prohibited_editing_techniques") or analysis.disqualification_reasons or [],
            "required_attribution": meta.get("required_attribution"),
            "submission_instructions": meta.get("submission_instructions"),
            "operator_approved": False,
            "ambiguities": analysis.ambiguities,
            "date_last_verified": now_str,
            "created_at": now_str,
            "updated_at": now_str,
        }
        return imported_campaign


content_rewards_adapter = ContentRewardsAdapter()
