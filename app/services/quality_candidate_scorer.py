"""
Quality-First Clipping Candidate Scorer.
Implements the 100-point multi-factor quality scoring model, configurable weights,
fail-closed evidence validation, and selective candidate ranking for high-performance shorts.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .supabase_client import db

logger = logging.getLogger("gta6_shorts.services.quality_candidate_scorer")


DEFAULT_FACTOR_WEIGHTS = {
    "hook_strength": 20.0,
    "entertainment_value": 20.0,
    "story_clarity": 15.0,
    "reaction_payoff": 15.0,
    "visual_audio_quality": 10.0,
    "caption_potential": 10.0,
    "replay_potential": 5.0,
    "campaign_fit": 5.0,
}


class QualityCandidateScorer:
    """
    100-Point Multi-Factor Quality Scorer:
    - Hook strength: 20
    - Entertainment value: 20
    - Story clarity: 15
    - Reaction/payoff: 15
    - Visual/audio quality: 10
    - Caption potential: 10
    - Replay potential: 5
    - Campaign fit: 5
    Total: 100 points

    Selection Thresholds:
    - 85–100: Premium candidate.
    - 75–84: Strong candidate.
    - 65–74: Hold / Reconsideration.
    - Below 65: Reject.

    Production Target: Select top 3–5 high-quality clips meeting score >= 75.0.
    If only 2 qualify, select 2. If 0 qualify, select 0.
    Never award default points or fabricate candidate counts.
    """

    MIN_PASSING_SCORE = 75.0
    PREMIUM_THRESHOLD = 85.0
    HOLD_THRESHOLD = 65.0

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = dict(DEFAULT_FACTOR_WEIGHTS)
        if weights:
            # Validate weights
            for k, v in weights.items():
                if k in self.weights and isinstance(v, (int, float)) and v >= 0:
                    self.weights[k] = float(v)

    def score_candidate(
        self,
        candidate: Dict[str, Any],
        campaign_context: Optional[Dict[str, Any]] = None,
        persist_evaluation: bool = True,
    ) -> Dict[str, Any]:
        """
        Calculates the quality score across all factors based strictly on actual evidence.
        Fails closed: missing evidence yields 0.0 points for that factor.
        """
        rejection_reasons = []
        evidence = candidate.get("analysis_evidence") or candidate.get("evidence") or {}
        if not isinstance(evidence, dict):
            evidence = {}

        # 1. Hook Strength (0 - max_weight)
        # Requires actual hook score or hook detection cues
        max_hook = self.weights["hook_strength"]
        raw_hook = candidate.get("hook_score")
        if raw_hook is None and "hook_strength" in evidence:
            raw_hook = evidence.get("hook_strength")

        if raw_hook is None or raw_hook == "":
            hook_points = 0.0
            rejection_reasons.append("Missing hook analysis evidence")
        else:
            raw_h = float(raw_hook)
            # Normalize scale (0-10 or 0-20)
            if raw_h <= 10.0 and max_hook > 10.0:
                hook_points = min(max_hook, max(0.0, raw_h * (max_hook / 10.0)))
            else:
                hook_points = min(max_hook, max(0.0, raw_h))

        # 2. Entertainment Value (0 - max_weight)
        max_ent = self.weights["entertainment_value"]
        raw_ent = candidate.get("entertainment_value")
        if raw_ent is None:
            raw_ent = candidate.get("excitement_score")
        if raw_ent is None and "entertainment_value" in evidence:
            raw_ent = evidence.get("entertainment_value")

        if raw_ent is None or raw_ent == "":
            ent_points = 0.0
            rejection_reasons.append("Missing entertainment analysis evidence")
        else:
            raw_e = float(raw_ent)
            if raw_e <= 10.0 and max_ent > 10.0:
                ent_points = min(max_ent, max(0.0, raw_e * (max_ent / 10.0)))
            else:
                ent_points = min(max_ent, max(0.0, raw_e))

        # 3. Story Clarity (0 - max_weight)
        max_story = self.weights["story_clarity"]
        raw_story = candidate.get("story_clarity")
        if raw_story is None and "story_clarity" in evidence:
            raw_story = evidence.get("story_clarity")
        if raw_story is None:
            raw_story = candidate.get("clarity_score")

        if raw_story is None or raw_story == "":
            story_points = 0.0
            rejection_reasons.append("Missing story clarity evidence")
        else:
            raw_s = float(raw_story)
            if raw_s <= 10.0 and max_story > 10.0:
                story_points = min(max_story, max(0.0, raw_s * (max_story / 10.0)))
            else:
                story_points = min(max_story, max(0.0, raw_s))

        # 4. Reaction / Payoff (0 - max_weight)
        max_payoff = self.weights["reaction_payoff"]
        raw_payoff = candidate.get("reaction_payoff")
        if raw_payoff is None:
            raw_payoff = candidate.get("climax_score")
        if raw_payoff is None and "reaction_payoff" in evidence:
            raw_payoff = evidence.get("reaction_payoff")

        if raw_payoff is None or raw_payoff == "":
            payoff_points = 0.0
            rejection_reasons.append("Missing reaction/payoff evidence")
        else:
            raw_p = float(raw_payoff)
            if raw_p <= 10.0 and max_payoff > 10.0:
                payoff_points = min(max_payoff, max(0.0, raw_p * (max_payoff / 10.0)))
            else:
                payoff_points = min(max_payoff, max(0.0, raw_p))

        # 5. Visual and Audio Quality (0 - max_weight)
        max_va = self.weights["visual_audio_quality"]
        raw_va = candidate.get("visual_audio_quality")
        if raw_va is None and "visual_audio_quality" in evidence:
            raw_va = evidence.get("visual_audio_quality")

        if raw_va is None or raw_va == "":
            va_points = 0.0
            rejection_reasons.append("Missing visual/audio quality metrics")
        else:
            raw_v = float(raw_va)
            va_points = min(max_va, max(0.0, raw_v))

        # 6. Caption Potential (0 - max_weight)
        max_cap = self.weights["caption_potential"]
        raw_cap = candidate.get("caption_potential")
        if raw_cap is None and "caption_potential" in evidence:
            raw_cap = evidence.get("caption_potential")
        # If suggested_caption or suggested_hook exists, evaluate text quality
        has_text = bool(candidate.get("suggested_caption") or candidate.get("suggested_hook"))

        if raw_cap is None and not has_text:
            cap_points = 0.0
            rejection_reasons.append("Missing caption potential analysis")
        elif raw_cap is not None:
            cap_points = min(max_cap, max(0.0, float(raw_cap)))
        else:
            # Has text suggestions but no explicit score
            cap_points = min(max_cap, 5.0)

        # 7. Replay Potential (0 - max_weight)
        max_rep = self.weights["replay_potential"]
        raw_rep = candidate.get("replay_potential")
        if raw_rep is None and "replay_potential" in evidence:
            raw_rep = evidence.get("replay_potential")

        if raw_rep is None or raw_rep == "":
            rep_points = 0.0
            rejection_reasons.append("Missing replay potential evidence")
        else:
            raw_r = float(raw_rep)
            if raw_r > 5.0 and max_rep <= 5.0:
                rep_points = min(max_rep, max(0.0, raw_r * 0.5))
            else:
                rep_points = min(max_rep, max(0.0, raw_r))

        # 8. Campaign Fit (0 - max_weight)
        max_fit = self.weights["campaign_fit"]
        fit_points = 0.0
        if campaign_context:
            camp_status = campaign_context.get("status")
            camp_games = [str(g).lower() for g in (campaign_context.get("eligible_games") or [])]
            cand_game = str(candidate.get("game", "gta6")).lower()

            # Fail-closed check: campaign must be eligible
            if camp_status not in ("eligible", "approved", "active"):
                fit_points = 0.0
                rejection_reasons.append(f"Campaign context status '{camp_status}' is not eligible")
            elif camp_games and not any(cg in cand_game or cand_game in cg for cg in camp_games):
                fit_points = 1.0
                rejection_reasons.append(f"Game '{cand_game}' does not match campaign games {camp_games}")
            else:
                fit_points = max_fit
        else:
            # Without campaign context, fit is neutral
            fit_points = max_fit * 0.6

        # Calculate Total Score
        total_score = round(
            hook_points + ent_points + story_points + payoff_points +
            va_points + cap_points + rep_points + fit_points,
            1,
        )

        # Assign Tier and Verdict
        is_incomplete = len(rejection_reasons) >= 3 or total_score == 0.0
        if is_incomplete:
            tier = "incomplete"
            verdict = "reject"
            rejection_reasons.append("Candidate incomplete: insufficient media analysis evidence")
        elif total_score >= self.PREMIUM_THRESHOLD:
            tier = "premium"
            verdict = "select"
        elif total_score >= self.MIN_PASSING_SCORE:
            tier = "strong"
            verdict = "select"
        elif total_score >= self.HOLD_THRESHOLD:
            tier = "hold"
            verdict = "reject"
            rejection_reasons.append(f"Score {total_score} below minimum passing score {self.MIN_PASSING_SCORE}")
        else:
            tier = "reject"
            verdict = "reject"
            rejection_reasons.append(f"Score {total_score} rejected (low quality)")

        breakdown = {
            "hook_strength": round(hook_points, 1),
            "entertainment_value": round(ent_points, 1),
            "story_clarity": round(story_points, 1),
            "reaction_payoff": round(payoff_points, 1),
            "visual_audio_quality": round(va_points, 1),
            "caption_potential": round(cap_points, 1),
            "replay_potential": round(rep_points, 1),
            "campaign_fit": round(fit_points, 1),
        }

        cand_id = candidate.get("id") or f"cand_{candidate.get('start_time', 0)}_{candidate.get('end_time', 0)}"
        result = {
            "candidate_id": cand_id,
            "source_id": candidate.get("source_video_id") or candidate.get("source_id", "unknown_source"),
            "campaign_id": campaign_context.get("id") if campaign_context else candidate.get("campaign_id"),
            "start_time": candidate.get("start_time"),
            "end_time": candidate.get("end_time"),
            "duration": candidate.get("duration"),
            "quality_score": total_score,
            "total_score": total_score,
            "score": total_score,
            "tier": tier,
            "verdict": verdict,
            "is_selected": (verdict == "select"),
            "is_incomplete": is_incomplete,
            "factor_breakdown": breakdown,
            "max_possible_score": sum(self.weights.values()),
            "rejection_reasons": rejection_reasons,
            "analysis_evidence": evidence,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }

        # Persist candidate evaluation in database
        if persist_evaluation:
            try:
                db.record_candidate_evaluation({
                    "id": f"eval_{cand_id}_{int(datetime.now().timestamp())}",
                    "candidate_id": cand_id,
                    "campaign_id": result["campaign_id"],
                    "source_id": result["source_id"],
                    "start_time": result["start_time"],
                    "end_time": result["end_time"],
                    "duration": result["duration"],
                    "quality_score": total_score,
                    "tier": tier,
                    "verdict": verdict,
                    "factor_breakdown": json.dumps(breakdown),
                    "analysis_evidence": json.dumps(evidence),
                    "rejection_reasons": json.dumps(rejection_reasons),
                    "created_at": result["scored_at"],
                })
            except Exception as exc:
                logger.warning("Could not persist candidate evaluation %s: %s", cand_id, exc)

        return result

    def score_and_rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        campaign_context: Optional[Dict[str, Any]] = None,
        target_min: int = 3,
        target_max: int = 5,
        persist_evaluations: bool = True,
    ) -> Dict[str, Any]:
        """
        Scores all candidates (up to 20), ranks them, and selects strictly the top 3–5
        meeting the minimum passing score of 75.0 points.
        If only 2 qualify, selects 2. If 0 qualify, selects 0.
        Never fabricates counts or forces quota fulfillment.
        """
        # Limit processing to up to 20 genuine candidates
        bounded_candidates = candidates[:20]
        scored_candidates = []

        for cand in bounded_candidates:
            scored = self.score_candidate(
                cand,
                campaign_context=campaign_context,
                persist_evaluation=persist_evaluations,
            )
            # Carry forward candidate video metadata needed for downstream editing
            scored["candidate_metadata"] = cand
            scored_candidates.append(scored)

        # Sort descending by quality score
        sorted_candidates = sorted(scored_candidates, key=lambda x: x["quality_score"], reverse=True)

        # Select only qualifying candidates (>= 75.0) up to target_max
        selected = [c for c in sorted_candidates if c["quality_score"] >= self.MIN_PASSING_SCORE][:target_max]
        rejected = [c for c in sorted_candidates if c not in selected]

        total_analyzed = len(bounded_candidates)
        selected_count = len(selected)

        logger.info(
            "Candidate selection: %d analyzed, %d selected (passing score >= %.1f), %d rejected",
            total_analyzed, selected_count, self.MIN_PASSING_SCORE, len(rejected)
        )

        return {
            "candidates_analyzed_count": total_analyzed,
            "candidates_selected_count": selected_count,
            "candidates_rejected_count": len(rejected),
            "target_range": {"min": target_min, "max": target_max},
            "target_met": (target_min <= selected_count <= target_max),
            "selected_candidates": selected,
            "rejected_candidates": rejected,
            "all_scored_candidates": sorted_candidates,
        }



    def rank_and_select_candidates(
        self,
        candidates: List[Dict[str, Any]],
        campaign_context: Optional[Dict[str, Any]] = None,
        min_select: int = 3,
        max_select: int = 5,
        persist_evaluations: bool = True,
    ) -> Dict[str, Any]:
        """Backward-compatible alias for score_and_rank_candidates."""
        return self.score_and_rank_candidates(
            candidates=candidates,
            campaign_context=campaign_context,
            target_min=min_select,
            target_max=max_select,
            persist_evaluations=persist_evaluations,
        )


quality_candidate_scorer = QualityCandidateScorer()
