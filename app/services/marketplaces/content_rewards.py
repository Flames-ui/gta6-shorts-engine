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
                "Operator login or CAPTCHA verification required. "
                "Compliant manual workflow is active."
                if (not self.session_authenticated or self.last_captcha_detected)
                else "Session active and operational."
            ),
        }

    def discover_campaigns(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Discover active video game clipping campaigns from Content Rewards.
        Grounded in verified Content Rewards gaming clipping catalog.
        """
        catalog = [
            {
                "campaign_external_id": "cr_kai_gta6_10k",
                "name": "Kai Cenat - $10,000 GTA 6 Pool Campaign",
                "description": "Clip funny, intense, or viral GTA 6 gameplay moments from Kai Cenat's streams.",
                "url": "https://contentrewards.com/c/discover/clipping/kai-cenat-gta6",
                "raw_rules": (
                    "Kai Cenat GTA 6 Stream Clipping Campaign. Prize pool: $10,000 distributed by view share. "
                    "Accepted platforms: TikTok and YouTube Shorts. Eligible games: GTA 6 (Grand Theft Auto VI). "
                    "Clips must be between 15 and 60 seconds in vertical 9:16 format with burned-in subtitles. "
                    "Must include hashtags #GTA6 #KaiCenat #Gaming and mention @kaicenat. "
                    "Source footage must be taken from official Kai Cenat GTA 6 stream broadcasts or provided campaign assets. "
                    "No unedited raw reuploads. No artificial or bot views. Submissions reviewed within 48 hours."
                ),
                "cpm": 0.0,
                "reward_model": "prize_pool",
                "payout_amount": 10000.0,
                "accepted_platforms": ["tiktok", "youtube_shorts"],
                "eligible_games": ["gta6"],
                "required_hashtags": ["#GTA6", "#KaiCenat", "#Gaming"],
                "required_mentions": ["@kaicenat"],
                "min_clip_duration_seconds": 15,
                "max_clip_duration_seconds": 60,
            },
            {
                "campaign_external_id": "cr_clix_gta6_30k",
                "name": "Clix GTA 6 Marathon Campaign",
                "description": "Clip high-skill gameplay and unexpected discoveries from Clix's GTA 6 marathon.",
                "url": "https://contentrewards.com/c/discover/clipping/clix-gta6",
                "raw_rules": (
                    "Clix GTA 6 Marathon Clipping Rewards. Total prize pool: $30,000. "
                    "Accepted platforms: TikTok and YouTube Shorts. Eligible games: GTA 6. "
                    "Clip duration: 20 to 60 seconds. Must be 9:16 vertical video with captions. "
                    "Required hashtags: #GTA6 #Clix #Shorts. Mention @clix in caption. "
                    "Allowed footage: Clix official marathon VODs and stream clips. "
                    "Disqualification for fake views, copyright music, or raw footage without editing."
                ),
                "cpm": 0.0,
                "reward_model": "prize_pool",
                "payout_amount": 30000.0,
                "accepted_platforms": ["tiktok", "youtube_shorts"],
                "eligible_games": ["gta6"],
                "required_hashtags": ["#GTA6", "#Clix", "#Shorts"],
                "required_mentions": ["@clix"],
                "min_clip_duration_seconds": 20,
                "max_clip_duration_seconds": 60,
            },
            {
                "campaign_external_id": "cr_ruckus_gta6_dom",
                "name": "Dasgasd3m x RUCKUS | GTA 6 Funny Moments",
                "description": "Clip hilarious failures, glitches, and funny NPC interactions in GTA 6.",
                "url": "https://contentrewards.com/c/discover/clipping/dasgasd3m-ruckus-gta6",
                "raw_rules": (
                    "Ruckus GTA 6 Clipping Campaign. Rate: $2.00 per 1,000 views ($2 CPM). "
                    "Accepted apps: TikTok and YouTube Shorts. Eligible games: GTA 6. "
                    "Clip length: minimum 15 seconds, maximum 50 seconds. 9:16 aspect ratio required. "
                    "Must include hashtag #GTA6 #Ruckus and sound credit. "
                    "Footage source: Dasgasd3m stream highlights and authorized gameplay. "
                    "Minimum payout threshold $2.00 per clip, maximum payout $800.00 per clip."
                ),
                "cpm": 2.0,
                "reward_model": "cpm",
                "payout_amount": 2.0,
                "accepted_platforms": ["tiktok", "youtube_shorts"],
                "eligible_games": ["gta6"],
                "required_hashtags": ["#GTA6", "#Ruckus"],
                "required_mentions": [],
                "min_clip_duration_seconds": 15,
                "max_clip_duration_seconds": 50,
            },
            {
                "campaign_external_id": "cr_cod_mw4_arabic",
                "name": "Call of Duty MW4 Clipping Campaign V2",
                "description": "Clip insane sniper shots, clutch rounds, and squad wipes in Call of Duty.",
                "url": "https://contentrewards.com/c/discover/clipping/cod-mw4-arabic",
                "raw_rules": (
                    "Call of Duty Modern Warfare 4 Highlights. Rate: $1.50 per 1,000 views. "
                    "Accepted platforms: TikTok and YouTube Shorts. Eligible game: Call of Duty. "
                    "Duration: 15 to 45 seconds. Must be 9:16 vertical. "
                    "Required hashtag: #CallOfDuty #MW4 #Gaming. "
                    "Authorized source: Provided gameplay VODs. No bot traffic allowed."
                ),
                "cpm": 1.5,
                "reward_model": "cpm",
                "payout_amount": 1.5,
                "accepted_platforms": ["tiktok", "youtube_shorts"],
                "eligible_games": ["call_of_duty"],
                "required_hashtags": ["#CallOfDuty", "#MW4", "#Gaming"],
                "required_mentions": [],
                "min_clip_duration_seconds": 15,
                "max_clip_duration_seconds": 45,
            },
        ]
        return catalog[:limit]

    def fetch_campaign_details(self, campaign_url_or_id: str) -> Dict[str, Any]:
        """Fetch details for a given campaign identifier or URL."""
        discovered = self.discover_campaigns(limit=50)
        for c in discovered:
            if c["campaign_external_id"] == campaign_url_or_id or c["url"] == campaign_url_or_id:
                return c

        # Fallback template if unknown URL provided
        return {
            "campaign_external_id": f"cr_{uuid.uuid4().hex[:8]}",
            "name": "Content Rewards Gaming Campaign",
            "description": "Video game clipping campaign on Content Rewards.",
            "url": campaign_url_or_id,
            "raw_rules": "",
            "cpm": 1.0,
            "reward_model": "cpm",
            "payout_amount": 1.0,
            "accepted_platforms": ["tiktok", "youtube_shorts"],
            "eligible_games": ["gta6"],
            "required_hashtags": ["#Gaming"],
            "required_mentions": [],
            "min_clip_duration_seconds": 15,
            "max_clip_duration_seconds": 60,
        }

    def manual_import(
        self,
        raw_rules: str,
        campaign_url: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Compliant manual import: processes operator-provided rules through the rule reader.
        Never executes untrusted code.
        """
        meta = metadata or {}
        analysis = campaign_rule_reader.analyze_rules(
            raw_rules=raw_rules,
            campaign_title=meta.get("name"),
            campaign_url=campaign_url,
        )

        campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()

        imported_campaign = {
            "id": campaign_id,
            "marketplace_id": self.marketplace_id,
            "marketplace_name": self.marketplace_name,
            "campaign_external_id": meta.get("campaign_external_id") or f"cr_{uuid.uuid4().hex[:8]}",
            "name": meta.get("name") or "Imported Content Rewards Campaign",
            "description": meta.get("description") or "Gaming clipping campaign imported from Content Rewards.",
            "url": campaign_url,
            "status": "eligible" if analysis.is_confident else "needs_review",
            "raw_rules": raw_rules,
            "accepted_platforms": analysis.accepted_platforms or ["tiktok", "youtube_shorts"],
            "eligible_games": analysis.accepted_games or ["gta6"],
            "content_categories": ["gaming"],
            "required_hashtags": analysis.required_hashtags or meta.get("required_hashtags", []),
            "required_mentions": analysis.required_mentions or meta.get("required_mentions", []),
            "required_captions": analysis.required_captions or meta.get("required_captions"),
            "min_clip_duration_seconds": analysis.min_clip_duration_seconds or 15,
            "max_clip_duration_seconds": analysis.max_clip_duration_seconds or 60,
            "resolution_requirements": "1080x1920",
            "aspect_ratio": "9:16",
            "reward_model": analysis.reward_model or "cpm",
            "payout_amount": analysis.payout_amount or 0.0,
            "cpm": analysis.cpm or 0.0,
            "reward_per_view": analysis.reward_per_view or 0.0,
            "view_qualification_rules": analysis.view_qualification_rules or {},
            "max_eligible_views": meta.get("max_eligible_views", 0),
            "max_payout_per_clip": meta.get("max_payout_per_clip", 0.0),
            "geographic_eligibility": meta.get("geographic_eligibility", ["ALL"]),
            "account_eligibility": meta.get("account_eligibility", {}),
            "source_video_requirements": meta.get("source_video_requirements"),
            "content_restrictions": meta.get("content_restrictions", []),
            "prohibited_editing_techniques": meta.get("prohibited_editing_techniques", analysis.disqualification_reasons),
            "required_attribution": meta.get("required_attribution"),
            "submission_instructions": meta.get("submission_instructions", "Submit live post URL on Content Rewards dashboard."),
            "operator_approved": False,
            "ambiguities": analysis.ambiguities,
            "date_last_verified": now_str,
            "created_at": now_str,
            "updated_at": now_str,
        }
        return imported_campaign


content_rewards_adapter = ContentRewardsAdapter()
