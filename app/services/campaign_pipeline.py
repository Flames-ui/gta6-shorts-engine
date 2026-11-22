"""
Milestone 3 — Unified End-to-End Campaign Pipeline Orchestrator.
Orchestrates the entire campaign lifecycle:
Discovery → Rule Extraction → Eligibility Verification → Opportunity Scoring →
Operator Approval → Source Authorization → Campaign Asset Retrieval →
Moment Analysis → Candidate Ranking → Editing → Rendering → Video Inspection →
Compliance Validation → Publication Package → Operator-Approved Submission →
Performance Tracking → Confirmed Earnings.
"""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..config import settings
from .campaign_eligibility import CampaignEligibilityService
from .campaign_rule_reader import CampaignRuleReader
from .compliance_validator import compliance_validator
from .earnings_tracker import earnings_tracker
from .editing_planner import EditingPlanner
from .gameplay_analyzer import gameplay_analyzer
from .google_drive_assets import google_drive_asset_service
from .operator_action_queue import operator_action_queue
from .opportunity_scorer import OpportunityScorer
from .publication_package import publication_package_service
from .quality_candidate_scorer import quality_candidate_scorer
from .source_authorization import SourceAuthorizationService
from .supabase_client import db
from .video_renderer import video_renderer

logger = logging.getLogger("gta6_shorts.services.campaign_pipeline")


class CampaignPipelineOrchestrator:
    """
    Unified end-to-end campaign orchestrator.
    Every stage consumes actual output from the preceding stage.
    Fails closed on unauthorized sources, missing assets, failed gates, or absent approvals.
    """

    def __init__(self):
        self.eligibility_service = CampaignEligibilityService()
        self.rule_reader = CampaignRuleReader()
        self.opportunity_scorer = OpportunityScorer()
        self.source_service = SourceAuthorizationService()
        self.editing_planner = EditingPlanner()

    def run_pipeline_for_campaign(
        self,
        campaign_id: str,
        source_id: Optional[str] = None,
        source_video_path: Optional[str] = None,
        target_platform: str = "tiktok",
        operator_override: bool = False,
        daily_target_max: int = 5,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Executes the single unified workflow end-to-end.
        Fails closed on missing or unverified preconditions.
        No programmatic bypasses or operator overrides are permitted.
        """
        if operator_override:
            logger.warning("operator_override parameter is strictly prohibited and ignored in Milestone 3.1. Verification must be evidence-backed.")

        now_iso = datetime.now(timezone.utc).isoformat()
        pipeline_log = {
            "run_id": f"pipe_{uuid.uuid4().hex[:10]}",
            "campaign_id": campaign_id,
            "source_id": source_id,
            "target_platform": target_platform,
            "started_at": now_iso,
            "stages_completed": [],
            "status": "in_progress",
            "blockers": [],
            "produced_clips": [],
        }

        # ---------------------------------------------------------------------
        # STAGE 1: Campaign Rule Extraction
        # ---------------------------------------------------------------------
        campaign = db.get_campaign(campaign_id)
        if campaign and not source_id:
            source_id = campaign.get("source_id")
        if campaign and not source_video_path:
            source_video_path = campaign.get("source_file_path")
        if not campaign:
            err = f"Stage 1 Blocker: Campaign {campaign_id} not found."
            pipeline_log["blockers"].append(err)
            pipeline_log["status"] = "failed"
            return pipeline_log

        raw_desc = campaign.get("description") or ""
        extracted_rules = self.rule_reader.analyze_rules(raw_rules=raw_desc, campaign_title=campaign.get("name"))
        pipeline_log["stages_completed"].append("stage_1_rule_extraction")

        # ---------------------------------------------------------------------
        # STAGE 2: Strict Campaign Eligibility Verification
        # ---------------------------------------------------------------------
        status, reasons, checks = self.eligibility_service.evaluate_campaign(campaign)
        if status not in ("eligible", "approved"):
            blocker = f"Stage 2 Blocker: Campaign {campaign_id} is ineligible ({status}): {reasons}"
            pipeline_log["blockers"].append(blocker)
            pipeline_log["status"] = "ineligible"
            logger.warning(blocker)
            return pipeline_log

        pipeline_log["stages_completed"].append("stage_2_eligibility_verification")

        # ---------------------------------------------------------------------
        # STAGE 3: Opportunity Scoring
        # ---------------------------------------------------------------------
        opp_result = self.opportunity_scorer.score_campaign(campaign)
        opp_score = opp_result.get("overall_score", 0.0)
        pipeline_log["opportunity_score"] = opp_score
        pipeline_log["stages_completed"].append("stage_3_opportunity_scoring")

        # ---------------------------------------------------------------------
        # STAGE 4: Operator Approval Verification
        # ---------------------------------------------------------------------
        is_approved = bool(campaign.get("operator_approved"))
        if not is_approved:
            blocker = f"Stage 4 Blocker: Campaign {campaign_id} requires explicit operator approval before production."
            pipeline_log["blockers"].append(blocker)
            pipeline_log["status"] = "awaiting_operator_approval"
            operator_action_queue.push_action(
                action_type="campaign_review_required",
                title=f"Review Campaign: {campaign.get('name')}",
                message=blocker,
                campaign_id=campaign_id,
                severity="warning",
            )
            return pipeline_log

        pipeline_log["stages_completed"].append("stage_4_operator_approval")

        # ---------------------------------------------------------------------
        # STAGE 5: Source Footage Authorization Verification
        # ---------------------------------------------------------------------
        source_record = db.get_campaign_source(source_id)
        if not source_record:
            blocker = f"Stage 5 Blocker: Source footage {source_id} record not found."
            pipeline_log["blockers"].append(blocker)
            pipeline_log["status"] = "unauthorized_source"
            return pipeline_log

        authorized, authorization_reason, source_record = self.source_service.verify_source(
            source_id=str(source_id), campaign_id=str(campaign_id), target_platform=target_platform
        )
        if not authorized:
            pipeline_log["blockers"].append(f"Stage 5 Blocker: {authorization_reason}")
            pipeline_log["status"] = "source_not_verified"
            return pipeline_log

        pipeline_log["stages_completed"].append("stage_5_source_authorization")

        # ---------------------------------------------------------------------
        # STAGE 6: Campaign Asset Retrieval & Verification
        # ---------------------------------------------------------------------
        req_assets = []
        if campaign.get("requires_logo"):
            req_assets.append("logo")

        assets_ok, missing_assets = google_drive_asset_service.verify_campaign_assets_for_job(
            campaign_id=campaign_id,
            required_asset_types=req_assets,
            require_verified_permission=True,
        )
        if not assets_ok:
            blocker = f"Stage 6 Blocker: Required assets missing or unverified: {missing_assets}"
            pipeline_log["blockers"].append(blocker)
            pipeline_log["status"] = "missing_campaign_assets"
            return pipeline_log

        campaign_assets = google_drive_asset_service.list_campaign_assets(campaign_id)
        pipeline_log["stages_completed"].append("stage_6_asset_retrieval")

        # ---------------------------------------------------------------------
        # STAGE 7: Moment Analysis from Authorized Footage
        # ---------------------------------------------------------------------
        if not source_video_path or not os.path.isfile(source_video_path):
            blocker = f"Stage 7 Blocker: Source video file missing on disk: {source_video_path or 'not provided'}"
            pipeline_log["blockers"].append(blocker)
            pipeline_log["status"] = "missing_video_file"
            return pipeline_log

        # Analyze up to 20 candidate moments from authorized video
        gameplay_candidates = gameplay_analyzer.analyze_gameplay_video(
            video_path=source_video_path,
            video_title=campaign.get("name", "GTA 6 Gameplay"),
            source_id=source_id,
            max_candidates=20,
        )
        raw_candidates_dicts = [c.model_dump() for c in gameplay_candidates]
        pipeline_log["moments_analyzed_count"] = len(raw_candidates_dicts)
        pipeline_log["stages_completed"].append("stage_7_moment_analysis")

        # ---------------------------------------------------------------------
        # STAGE 8: Candidate Quality Scoring & Ranking (Top 3–5)
        # ---------------------------------------------------------------------
        scoring_result = quality_candidate_scorer.score_and_rank_candidates(
            candidates=raw_candidates_dicts,
            campaign_context=campaign,
            target_min=3,
            target_max=daily_target_max,
            persist_evaluations=True,
        )
        selected_candidates = scoring_result.get("selected_candidates", [])
        pipeline_log["selected_candidates_count"] = len(selected_candidates)
        pipeline_log["stages_completed"].append("stage_8_candidate_ranking")

        if len(selected_candidates) == 0:
            pipeline_log["status"] = "zero_qualifying_candidates"
            logger.info("Pipeline completed: 0 qualifying candidates met passing score >= 75.0.")
            return pipeline_log

        # ---------------------------------------------------------------------
        # STAGES 9-13: Production per Selected Candidate
        # ---------------------------------------------------------------------
        output_dir = os.path.join(settings.LOCAL_OUTPUT_DIR, campaign_id)
        os.makedirs(output_dir, exist_ok=True)

        for idx, cand in enumerate(selected_candidates):
            cand_id = cand.get("candidate_id")
            clip_id = f"clip_{campaign_id[:8]}_{cand_id[:8]}_{idx}"

            # Idempotency check: Reuse existing clip if already rendered and compliant
            existing_clip = db.get_clip(clip_id)
            if existing_clip and existing_clip.get("is_compliant") and existing_clip.get("video_path") and os.path.exists(existing_clip.get("video_path")):
                logger.info("Reusing idempotent rendered clip %s", clip_id)
                pipeline_log["produced_clips"].append({
                    "clip_id": clip_id,
                    "video_path": existing_clip.get("video_path"),
                    "quality_score": cand.get("quality_score"),
                    "gate_a_passed": True,
                    "gate_b_passed": True,
                    "idempotent_reused": True,
                })
                continue

            # Stage 9: Build structured editing plan
            plan = self.editing_planner.create_editing_plan(
                campaign=campaign,
                candidate=cand.get("candidate_metadata", cand),
                source_footage_path=source_video_path,
                platform=target_platform,
                source_authorized=True,
                campaign_assets=campaign_assets,
            )

            # Stage 10: Render video via FFmpeg
            rendered_video_path = os.path.join(output_dir, f"{clip_id}.mp4")
            try:
                render_report = video_renderer.render_editing_plan(plan, rendered_video_path)
            except Exception as exc:
                logger.error("Rendering failed for clip %s: %s", clip_id, exc)
                continue

            # Stage 11: Real video inspection & dual-gate validation
            val_result = compliance_validator.validate_dual_gates(
                clip_id=clip_id,
                video_path=rendered_video_path,
                campaign=campaign,
                source=source_record,
                editing_plan=plan,
                target_platform=target_platform,
                candidate_quality_score=cand.get("quality_score"),
                quality_evaluation=cand,
                clip_metadata={"caption": plan.get("exact_publication_caption")},
            )

            # Stage 12: Bounded Auto-Correction & Rerender if needed
            if not val_result.get("overall_approved") and val_result.get("can_auto_correct"):
                correction_res = compliance_validator.attempt_auto_correction_and_rerender(
                    clip_id=clip_id,
                    editing_plan=plan,
                    campaign=campaign,
                    source=source_record,
                    current_video_path=rendered_video_path,
                    validation_result=val_result,
                    target_platform=target_platform,
                    max_attempts=2,
                    quality_evaluation=cand,
                )
                val_result = correction_res["validation"]
                rendered_video_path = correction_res["video_path"]

            # If clip passes both gates, assemble publication package
            if val_result.get("overall_approved"):
                pkg = publication_package_service.build_publication_package(
                    clip_id=clip_id,
                    video_path=rendered_video_path,
                    campaign=campaign,
                    editing_plan=plan,
                    compliance_result=val_result,
                    target_platform=target_platform,
                )

                # Persist clip record
                clip_rec = {
                    "id": clip_id,
                    "title": plan.get("suggested_title", "GTA 6 Viral Short"),
                    "video_path": rendered_video_path,
                    "storage_path": rendered_video_path,
                    "campaign_id": campaign_id,
                    "source_id": source_id,
                    "status": "ready",
                    "compliance_status": "compliant",
                    "is_compliant": True,
                    "publishing_status": "awaiting_approval",  # Strict: requires operator approval!
                    "created_at": now_iso,
                }
                db.create_clip(clip_rec)

                pipeline_log["produced_clips"].append({
                    "clip_id": clip_id,
                    "video_path": rendered_video_path,
                    "quality_score": cand.get("quality_score"),
                    "gate_a_passed": True,
                    "gate_b_passed": True,
                    "package": pkg,
                })

        pipeline_log["stages_completed"].extend([
            "stage_9_editing_plan",
            "stage_10_video_rendering",
            "stage_11_video_inspection",
            "stage_12_dual_gates",
            "stage_13_publication_package",
        ])

        if len(pipeline_log["produced_clips"]) > 0:
            pipeline_log["status"] = "completed"
        else:
            pipeline_log["status"] = "failed"
            pipeline_log["blockers"].append("All candidate clips failed rendering or dual-gate validation.")

        pipeline_log["completed_at"] = datetime.now(timezone.utc).isoformat()
        return pipeline_log

    execute_campaign_pipeline = run_pipeline_for_campaign


campaign_pipeline_orchestrator = CampaignPipelineOrchestrator()

# Alias for service naming convention
campaign_pipeline_service = campaign_pipeline_orchestrator
