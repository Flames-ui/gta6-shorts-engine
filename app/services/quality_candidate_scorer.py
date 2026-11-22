"""
Quality-First Clipping Candidate Scorer.
Implements the 100-point multi-factor quality scoring model and selective candidate ranking
for high-performance short-form content.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("gta6_shorts.services.quality_candidate_scorer")


class QualityCandidateScorer:
    """
    Initial 100-point scoring model:
    - Hook strength: 20
    - Entertainment value: 20
    - Story clarity: 15
    - Reaction/payoff: 15
    - Visual/audio quality: 10
    - Caption potential: 10
    - Replay potential: 5
    - Campaign fit: 5
    Total: 100 points

    Selection thresholds:
    - 85–100: Premium candidate.
    - 75–84: Strong candidate.
    - 65–74: Hold for reconsideration.
    - Below 65: Reject.

    Target: Select top 3–5 high-quality clips. If only 2 pass, select 2. If 0 pass, select 0.
    Never lower quality thresholds to fill target.
    """

    MIN_PASSING_SCORE = 75.0  # Only Strong (75-84) and Premium (85-100) pass to production
    PREMIUM_THRESHOLD = 85.0
    HOLD_THRESHOLD = 65.0

    def score_candidate(self, candidate: Dict[str, Any], campaign_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Calculates the 100-point quality score across all 8 factors.
        """
        # Hook strength (0-20)
        hook_score = float(candidate.get("hook_score", 0.0))
        # Normalize if given as 0-10
        if hook_score <= 10.0 and hook_score > 0:
            hook_strength = min(20.0, hook_score * 2.0)
        else:
            hook_strength = min(20.0, max(0.0, hook_score))

        # Entertainment value (0-20)
        ent_score = float(candidate.get("entertainment_value", candidate.get("excitement_score", 0.0)))
        if ent_score <= 10.0 and ent_score > 0:
            entertainment_value = min(20.0, ent_score * 2.0)
        else:
            entertainment_value = min(20.0, max(0.0, ent_score))

        # Story clarity (0-15)
        story_clarity = min(15.0, max(0.0, float(candidate.get("story_clarity", 10.0))))

        # Reaction / payoff (0-15)
        reaction_payoff = min(15.0, max(0.0, float(candidate.get("reaction_payoff", candidate.get("climax_score", 10.0)))))

        # Visual and audio quality (0-10)
        visual_audio_quality = min(10.0, max(0.0, float(candidate.get("visual_audio_quality", 8.0))))

        # Caption potential (0-10)
        caption_potential = min(10.0, max(0.0, float(candidate.get("caption_potential", 8.0))))

        # Replay potential (0-5)
        replay_potential = min(5.0, max(0.0, float(candidate.get("replay_potential", 3.0))))

        # Campaign fit (0-5)
        campaign_fit = 5.0
        if campaign_context:
            # Verify game/topic alignment
            camp_games = [g.lower() for g in campaign_context.get("eligible_games", [])]
            cand_game = candidate.get("game", "gta6").lower()
            if camp_games and not any(cg in cand_game or cand_game in cg for cg in camp_games):
                campaign_fit = 1.0

        total_score = round(
            hook_strength + entertainment_value + story_clarity +
            reaction_payoff + visual_audio_quality + caption_potential +
            replay_potential + campaign_fit,
            1
        )

        if total_score >= self.PREMIUM_THRESHOLD:
            tier = "premium"
            verdict = "select"
        elif total_score >= self.MIN_PASSING_SCORE:
            tier = "strong"
            verdict = "select"
        elif total_score >= self.HOLD_THRESHOLD:
            tier = "hold"
            verdict = "hold"
        else:
            tier = "reject"
            verdict = "reject"

        return {
            "total_score": total_score,
            "tier": tier,
            "verdict": verdict,
            "factor_breakdown": {
                "hook_strength": hook_strength,
                "entertainment_value": entertainment_value,
                "story_clarity": story_clarity,
                "reaction_payoff": reaction_payoff,
                "visual_audio_quality": visual_audio_quality,
                "caption_potential": caption_potential,
                "replay_potential": replay_potential,
                "campaign_fit": campaign_fit,
            },
            "candidate_id": candidate.get("id") or candidate.get("candidate_id"),
            "start_time": candidate.get("start_time"),
            "end_time": candidate.get("end_time"),
            "duration": candidate.get("duration"),
            "reasoning": f"Total score {total_score}/100 categorized as {tier.upper()} ({verdict.upper()})."
        }

    def rank_and_select_candidates(
        self,
        candidates: List[Dict[str, Any]],
        campaign_context: Optional[Dict[str, Any]] = None,
        min_select: int = 3,
        max_select: int = 5,
    ) -> Dict[str, Any]:
        """
        Scores up to 20 candidate moments, ranks them, and selects strictly the top 3–5
        passing candidates (score >= 75.0).
        If fewer than min_select pass, selects only those that passed. If none pass, selects 0.
        Never lowers quality thresholds to meet quota.
        """
        scored_candidates = []
        for cand in candidates:
            score_res = self.score_candidate(cand, campaign_context)
            scored_candidates.append({**cand, **score_res})

        # Rank descending by total_score
        ranked = sorted(scored_candidates, key=lambda c: c["total_score"], reverse=True)

        # Select only those meeting MIN_PASSING_SCORE
        passing = [c for c in ranked if c["total_score"] >= self.MIN_PASSING_SCORE]
        held = [c for c in ranked if self.HOLD_THRESHOLD <= c["total_score"] < self.MIN_PASSING_SCORE]
        rejected = [c for c in ranked if c["total_score"] < self.HOLD_THRESHOLD]

        selected = passing[:max_select]

        logger.info(
            "Candidate selection: %d analyzed, %d passed, %d held, %d rejected, %d selected",
            len(candidates), len(passing), len(held), len(rejected), len(selected)
        )

        return {
            "total_analyzed": len(candidates),
            "selected_candidates": selected,
            "held_candidates": held,
            "rejected_candidates": rejected,
            "passing_count": len(passing),
            "selected_count": len(selected),
            "held_count": len(held),
            "rejected_count": len(rejected),
            "top_score": ranked[0]["total_score"] if ranked else 0.0,
            "selection_notes": (
                f"Selected {len(selected)} clips meeting >= {self.MIN_PASSING_SCORE} pts "
                f"(max {max_select}). Quality threshold was strictly enforced."
            ),
        }


quality_candidate_scorer = QualityCandidateScorer()
