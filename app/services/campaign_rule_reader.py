"""Campaign rule reader service for converting untrusted campaign instructions into structured rules."""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ..schemas import CampaignRuleAnalysis

logger = logging.getLogger("gta6_shorts.rule_reader")

# Known prompt injection markers to neutralize in untrusted content
INJECTION_MARKERS = [
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions",
    r"system\s*prompt",
    r"you\s+are\s+now",
    r"admin\s*override",
    r"bypass\s*security",
    r"drop\s+table",
    r"grant\s+all",
    r"<script",
    r"bash\s+-i",
]


class CampaignRuleReader:
    """
    Parses and converts untrusted third-party campaign guidelines into structured requirements.
    Never executes or evaluates embedded commands or instructions from campaign text.
    """

    def analyze_rules(
        self,
        raw_rules: str,
        campaign_title: Optional[str] = None,
        campaign_url: Optional[str] = None,
    ) -> CampaignRuleAnalysis:
        """
        Analyze untrusted campaign text and extract structured criteria.
        Marks status as needs_review if rules are ambiguous or missing essential data.
        """
        if not raw_rules or not raw_rules.strip():
            return CampaignRuleAnalysis(
                is_confident=False,
                ambiguities=["No campaign rules text provided for analysis."],
            )

        # 1. Security Sanitization & Prompt Injection Defense
        security_warnings = []
        lower_rules = raw_rules.lower()
        for marker in INJECTION_MARKERS:
            if re.search(marker, lower_rules):
                security_warnings.append(f"Untrusted content contains potential control sequence: '{marker}'")

        full_context = f"{campaign_title or ''} {raw_rules}".lower()

        # 2. Extract Accepted Games
        accepted_games = []
        if re.search(r"\b(?:gta\s*6|gta\s*vi|grand\s*theft\s*auto\s*(?:6|vi)|leonida|vice\s*city)\b", full_context):
            accepted_games.append("gta6")
        if re.search(r"\b(?:call\s*of\s*duty|cod|modern\s*warfare|warzone)\b", full_context):
            accepted_games.append("call_of_duty")
        if re.search(r"\b(?:metaphor|sega|football\s*manager|warcraft|fortnite|minecraft)\b", full_context):
            matched = re.findall(r"\b(metaphor|sega|football\s*manager|warcraft|fortnite|minecraft)\b", full_context)
            for m in set(matched):
                accepted_games.append(m.replace(" ", "_"))
        if not accepted_games and re.search(r"\b(?:gameplay|gaming|game|streamer|stream|vod|esports)\b", full_context):
            accepted_games.append("gaming_general")

        # 3. Extract Platforms (Strict operator focus on TikTok and YouTube Shorts)
        accepted_platforms = []
        if re.search(r"\b(?:tiktok|tt)\b", full_context):
            accepted_platforms.append("tiktok")
        if re.search(r"\b(?:youtube|shorts|yt\s*shorts)\b", full_context):
            accepted_platforms.append("youtube_shorts")

        # 4. Extract Required Hashtags & Mentions
        hashtags = list(dict.fromkeys(re.findall(r"#[a-zA-Z0-9_]+", raw_rules)))
        mentions = list(dict.fromkeys(re.findall(r"@[a-zA-Z0-9_]+", raw_rules)))

        # 5. Extract Duration Requirements
        min_duration = None
        max_duration = None

        # Pattern: "15-60s", "15 to 60 seconds", "15 - 60 sec"
        range_match = re.search(r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:s|sec|seconds)?\b", lower_rules)
        if range_match:
            d1, d2 = int(range_match.group(1)), int(range_match.group(2))
            if 5 <= d1 <= 180 and 5 <= d2 <= 300:
                min_duration = min(d1, d2)
                max_duration = max(d1, d2)

        if min_duration is None:
            min_match = re.search(r"(?:min(?:imum)?|at\s*least)\s*(\d+)\s*(?:s|sec|seconds)?\b", lower_rules)
            if min_match:
                val = int(min_match.group(1))
                if 5 <= val <= 180:
                    min_duration = val

        if max_duration is None:
            max_match = re.search(r"(?:max(?:imum)?|under|up\s*to)\s*(\d+)\s*(?:s|sec|seconds)?\b", lower_rules)
            if max_match:
                val = int(max_match.group(1))
                if 10 <= val <= 300:
                    max_duration = val

        # 6. Extract Reward Model and Amounts
        reward_model = "cpm"
        cpm = None
        payout_amount = None
        reward_per_view = None

        # Check for prize pool
        pool_match = re.search(
            r"(?:(?:prize\s*pool|total\s*pool|pool)\s*(?:of|is|:)?\s*\$?([\d,]+(?:\.\d+)?)|(?:\$?([\d,]+(?:\.\d+)?)\s*(?:k\s*)?(?:cash\s*)?pool\b))",
            lower_rules,
            re.IGNORECASE,
        )
        if pool_match:
            reward_model = "prize_pool"
            num_str = pool_match.group(1) or pool_match.group(2)
            if num_str:
                val_str = num_str.replace(",", "")
                try:
                    base_val = float(val_str)
                    matched_str = pool_match.group(0).lower()
                    if "k" in matched_str and base_val < 1000 and "1000" not in val_str:
                        base_val *= 1000
                    payout_amount = base_val
                except ValueError:
                    pass

        # Check for CPM: "$2/1K", "$1.50 CPM", "$2.00 per 1,000 views"
        cpm_match = re.search(r"\$?([\d\.]+)\s*(?:cpm|\/\s*1k|\/\s*1000|per\s*1[,.]?000\s*views|per\s*thousand\s*views)\b", lower_rules)
        if cpm_match:
            try:
                cpm = float(cpm_match.group(1))
                reward_per_view = cpm / 1000.0
                if reward_model != "prize_pool":
                    reward_model = "cpm"
                    payout_amount = cpm
            except ValueError:
                pass

        # Check for fixed per clip: "$35 per clip", "$50 fixed"
        fixed_match = re.search(r"\$?([\d\.]+)\s*(?:per\s*clip|fixed|bounty)\b", lower_rules)
        if fixed_match and not cpm_match and not pool_match:
            try:
                payout_amount = float(fixed_match.group(1))
                reward_model = "fixed_per_clip"
            except ValueError:
                pass

        # 7. Permitted Content & Footages
        allowed_footage = []
        if re.search(r"\b(?:trailer|official\s*trailer)\b", lower_rules):
            allowed_footage.append("official_trailers")
        if re.search(r"\b(?:stream|vod|streamer|broadcast)\b", lower_rules):
            allowed_footage.append("streamer_vods")
        if re.search(r"\b(?:gameplay|in-game|recorded\s*gameplay)\b", lower_rules):
            allowed_footage.append("gameplay_footage")
        if re.search(r"\b(?:campaign\s*link|provided\s*assets?|provided\s*footage)\b", lower_rules):
            allowed_footage.append("campaign_provided_assets")

        # 8. Permitted & Prohibited Editing
        permitted_editing = ["vertical_9_16", "audio_normalization"]
        if re.search(r"\b(?:caption|subtitle|subtitles)\b", lower_rules):
            permitted_editing.append("burned_in_subtitles")
        if re.search(r"\b(?:hook|text\s*overlay)\b", lower_rules):
            permitted_editing.append("hook_overlays")

        disqualifications = []
        if re.search(r"\b(?:no\s*unedited|no\s*raw|must\s*be\s*edited)\b", lower_rules):
            disqualifications.append("unedited_raw_reuploads")
        if re.search(r"\b(?:bot|fake\s*views|view\s*bot)\b", lower_rules):
            disqualifications.append("artificial_or_bot_views")
        if re.search(r"\b(?:copyright|unlicensed\s*music)\b", lower_rules):
            disqualifications.append("unlicensed_audio")
        if re.search(r"\b(?:wrong\s*hashtag|missing\s*hashtag)\b", lower_rules):
            disqualifications.append("missing_required_hashtags")

        # 9. Confidence and Ambiguity Evaluation
        ambiguities = []
        if not accepted_games:
            ambiguities.append("No recognized gaming title or category identified in rules.")
        if not accepted_platforms:
            ambiguities.append("No supported video platform (TikTok or YouTube Shorts) found in rules.")
        if min_duration is None or max_duration is None:
            ambiguities.append("Clip duration boundaries (min/max seconds) are not explicitly specified.")
        if reward_model == "cpm" and cpm is None and payout_amount is None:
            ambiguities.append("Reward payout rate or CPM calculation is missing or ambiguous.")

        is_confident = len(ambiguities) == 0 and len(security_warnings) == 0

        return CampaignRuleAnalysis(
            is_confident=is_confident,
            permitted_content=["gaming_highlights", "viral_moments"] if accepted_games else [],
            accepted_games=accepted_games,
            accepted_platforms=accepted_platforms,
            allowed_footage=allowed_footage,
            permitted_editing=permitted_editing,
            min_clip_duration_seconds=min_duration,
            max_clip_duration_seconds=max_duration,
            required_hashtags=hashtags,
            required_mentions=mentions,
            required_captions=None,
            reward_model=reward_model,
            cpm=cpm,
            payout_amount=payout_amount,
            reward_per_view=reward_per_view,
            view_qualification_rules={
                "min_views_threshold": 1000 if cpm else 0,
                "genuine_engagement_required": True,
            },
            disqualification_reasons=disqualifications,
            ambiguities=ambiguities,
            security_warnings=security_warnings,
        )


campaign_rule_reader = CampaignRuleReader()
