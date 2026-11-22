"""
Campaign opportunity scoring service prioritizing genuine earning opportunities.
Evaluates reward structures, platform reach, budget headroom, and production complexity.
Never fabricates view forecasts or guarantees earnings.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("gta6_shorts.opportunity_scorer")


class OpportunityScorer:
    """
    Ranks campaigns on a transparent 0-100 scale to help the operator
    focus on the most profitable and compliant clipping opportunities.
    """

    def score_campaign(self, campaign: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate a campaign and return detailed score, tier, and explanation breakdown.
        """
        explanations: List[str] = []
        score = 0.0

        # 1. Platform Fit Evaluation (Max 25 pts)
        platforms = [str(p).lower() for p in (campaign.get("accepted_platforms") or [])]
        has_tiktok = "tiktok" in platforms
        has_yt = "youtube_shorts" in platforms

        if has_tiktok and has_yt:
            score += 25.0
            explanations.append("Dual platform reach: accepts both TikTok and YouTube Shorts (+25)")
        elif has_tiktok or has_yt:
            score += 15.0
            p_name = "TikTok" if has_tiktok else "YouTube Shorts"
            explanations.append(f"Single supported platform: {p_name} only (+15)")
        else:
            explanations.append("Rejected: No supported short-form platforms (0)")
            return {
                "overall_score": 0.0,
                "tier": "REJECTED",
                "is_viable": False,
                "explanations": ["Campaign lacks TikTok or YouTube Shorts support."],
                "verified_earnings_structure": "None",
            }

        # 2. Reward Structure & Earning Rate Evaluation (Max 35 pts)
        reward_model = str(campaign.get("reward_model") or "").lower()
        cpm = float(campaign.get("cpm") or 0.0)
        payout = float(campaign.get("payout_amount") or 0.0)

        verified_structure = "Unknown"
        if reward_model == "cpm":
            verified_structure = f"${cpm:.2f} CPM"
            if cpm >= 3.0:
                score += 35.0
                explanations.append(f"High CPM tier: ${cpm:.2f} per 1K views (+35)")
            elif cpm >= 1.5:
                score += 25.0
                explanations.append(f"Standard CPM tier: ${cpm:.2f} per 1K views (+25)")
            elif cpm > 0:
                score += 15.0
                explanations.append(f"Entry CPM tier: ${cpm:.2f} per 1K views (+15)")
            else:
                explanations.append("Zero CPM rate (+0)")
        elif reward_model == "prize_pool":
            verified_structure = f"${payout:,.0f} Prize Pool"
            if payout >= 20000.0:
                score += 35.0
                explanations.append(f"Major prize pool: ${payout:,.0f} (+35)")
            elif payout >= 5000.0:
                score += 25.0
                explanations.append(f"Significant prize pool: ${payout:,.0f} (+25)")
            elif payout > 0:
                score += 15.0
                explanations.append(f"Modest prize pool: ${payout:,.0f} (+15)")
        elif reward_model == "fixed_per_clip":
            verified_structure = f"${payout:.2f} per clip"
            score += min(35.0, payout * 2.0)
            explanations.append(f"Fixed reward: ${payout:.2f} per approved clip")
        else:
            explanations.append("Unclear or non-monetary reward structure (+0)")

        # 3. Budget Headroom & Allowance Capacity (Max 20 pts)
        remaining_budget = campaign.get("remaining_budget")
        max_views = campaign.get("max_eligible_views")

        if remaining_budget is not None:
            rem = float(remaining_budget)
            if rem > 1000.0:
                score += 20.0
                explanations.append(f"Substantial remaining budget: ${rem:,.2f} (+20)")
            elif rem > 100.0:
                score += 10.0
                explanations.append(f"Moderate remaining budget: ${rem:,.2f} (+10)")
            elif rem <= 0:
                return {
                    "overall_score": 0.0,
                    "tier": "EXHAUSTED",
                    "is_viable": False,
                    "explanations": ["Campaign budget is fully exhausted."],
                    "verified_earnings_structure": verified_structure,
                }
        else:
            # Budget uncapped or pool-based
            score += 15.0
            explanations.append("Standard campaign pool/budget headroom (+15)")

        # 4. Production Duration & Editing Feasibility (Max 10 pts)
        min_dur = campaign.get("min_clip_duration_seconds")
        max_dur = campaign.get("max_clip_duration_seconds")
        if min_dur and max_dur:
            min_i, max_i = int(min_dur), int(max_dur)
            if 15 <= min_i and max_i <= 60:
                score += 10.0
                explanations.append(f"Optimal Shorts duration window: [{min_i}s - {max_i}s] (+10)")
            else:
                score += 5.0
                explanations.append(f"Acceptable duration window: [{min_i}s - {max_i}s] (+5)")
        else:
            explanations.append("Duration limits not explicitly bounded (+0)")

        # 5. Approval & Workflow Simplicity (Max 10 pts)
        req_brand_approval = bool(campaign.get("requires_brand_approval"))
        if not req_brand_approval:
            score += 10.0
            explanations.append("Instant participation: no pre-application brand approval required (+10)")
        else:
            score += 3.0
            explanations.append("Manual brand approval required prior to submission (+3)")

        final_score = round(min(100.0, max(0.0, score)), 1)

        # Tier assignment
        if final_score >= 75.0:
            tier = "HIGH"
        elif final_score >= 50.0:
            tier = "MEDIUM"
        else:
            tier = "LOW"

        return {
            "overall_score": final_score,
            "tier": tier,
            "is_viable": final_score >= 50.0,
            "verified_earnings_structure": verified_structure,
            "explanations": explanations,
        }


opportunity_scorer = OpportunityScorer()
