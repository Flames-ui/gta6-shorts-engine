"""Campaign management, eligibility review, source registration, and submission endpoints."""
import logging
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....auth import AuthContext, verify_api_key
from ....config import settings
from ....queue import job_queue
from ....schemas import (
    CampaignCreateRequest,
    CampaignEligibilityResult,
    CampaignImportRequest,
    CampaignListResponse,
    CampaignResponse,
    CampaignReviewRequest,
    CampaignSourceRequest,
    CampaignSourceResponse,
    CampaignStatus,
    ClipComplianceResult,
    ClipMetricsUpdateRequest,
    ClipSubmissionRequest,
    ClipSubmissionResponse,
    EarningsSummaryResponse,
    JobCreateRequest,
    JobResponse,
    JobStatus,
    SourceVerifyRequest,
)
from ....services.campaign_eligibility import campaign_eligibility_service
from ....services.campaign_rule_reader import campaign_rule_reader
from ....services.compliance_validator import compliance_validator
from ....services.earnings_tracker import earnings_tracker
from ....services.marketplaces.content_rewards import content_rewards_adapter
from ....services.source_authorization import source_authorization_service
from ....services.supabase_client import db
from ....services.video_processor import process_video_job

logger = logging.getLogger("gta6_shorts.api.campaigns")
router = APIRouter(prefix="/campaigns", tags=["Campaigns"])


@router.get("/discover", response_model=List[Dict[str, Any]])
async def discover_campaigns(
    limit: int = Query(20, ge=1, le=50),
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Discover active gaming clipping campaigns from Content Rewards marketplace.
    When browser session is not yet authenticated, returns empty list (no fabricated data).
    """
    return content_rewards_adapter.discover_campaigns(limit=limit)


@router.get("/discover/status", response_model=Dict[str, Any])
async def get_discovery_status(
    auth: AuthContext = Depends(verify_api_key),
):
    """Retrieve discovery integration and browser operator session status."""
    return content_rewards_adapter.get_discovery_status()


@router.post("/import", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def import_campaign(
    payload: CampaignImportRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Import a campaign from marketplace URL or operator-provided raw rules.
    Fails closed if rules cannot be acquired. Never invents missing requirements.
    """
    raw_rules = payload.raw_rules
    meta = {}
    if not raw_rules:
        details = content_rewards_adapter.fetch_campaign_details(payload.url)
        raw_rules = details.get("raw_rules", "")
        meta = details

    if not raw_rules or not raw_rules.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unable to acquire live rules automatically from Content Rewards (session unauthenticated). "
                "Please provide raw_rules text in the request payload."
            ),
        )

    imported = content_rewards_adapter.manual_import(
        raw_rules=raw_rules,
        campaign_url=payload.url,
        metadata=meta,
    )

    if payload.auto_analyze:
        new_status, reasons, checks = campaign_eligibility_service.evaluate_campaign(imported)
        imported["status"] = new_status
        if reasons:
            imported["review_notes"] = "; ".join(reasons)

    created = db.create_campaign(imported)
    return created


@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Register a new gaming campaign with full rules and initial eligibility evaluation."""
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    campaign_data = payload.model_dump()
    campaign_data["id"] = campaign_id

    # Run rule reader to verify consistency and detect ambiguities
    analysis = campaign_rule_reader.analyze_rules(
        raw_rules=payload.raw_rules,
        campaign_title=payload.name,
        campaign_url=payload.url,
    )
    campaign_data["ambiguities"] = analysis.ambiguities

    # Run strict eligibility check
    new_status, reasons, _ = campaign_eligibility_service.evaluate_campaign(campaign_data)
    campaign_data["status"] = new_status
    if reasons:
        campaign_data["review_notes"] = "; ".join(reasons)

    created = db.create_campaign(campaign_data)
    return created


@router.get("", response_model=CampaignListResponse)
async def list_campaigns(
    status: Optional[str] = Query(None, description="Filter by status: eligible, approved, needs_review, rejected, etc."),
    marketplace_id: Optional[str] = Query(None, description="Filter by marketplace: content_rewards"),
    game: Optional[str] = Query(None, description="Filter by game: gta6"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(verify_api_key),
):
    """List stored campaigns with filters and pagination."""
    items, total = db.list_campaigns(
        status=status,
        marketplace_id=marketplace_id,
        game=game,
        limit=limit,
        offset=offset,
    )
    return CampaignListResponse(
        items=[CampaignResponse(**item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """Retrieve complete campaign record including original unedited raw rules."""
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )
    return campaign


@router.post("/{campaign_id}/analyze", response_model=CampaignEligibilityResult)
async def analyze_campaign_eligibility(
    campaign_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """Re-analyze campaign rules and evaluate strict qualification criteria."""
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    analysis = campaign_rule_reader.analyze_rules(
        raw_rules=campaign["raw_rules"],
        campaign_title=campaign.get("name"),
        campaign_url=campaign.get("url"),
    )

    updates = {
        "ambiguities": analysis.ambiguities,
    }
    if analysis.accepted_platforms:
        updates["accepted_platforms"] = analysis.accepted_platforms
    if analysis.accepted_games:
        updates["eligible_games"] = analysis.accepted_games
    if analysis.cpm:
        updates["cpm"] = analysis.cpm
    if analysis.reward_model:
        updates["reward_model"] = analysis.reward_model

    merged = dict(campaign, **updates)
    new_status, reasons, checks = campaign_eligibility_service.evaluate_campaign(merged)
    updates["status"] = new_status
    if reasons:
        updates["review_notes"] = "; ".join(reasons)

    db.update_campaign(campaign_id, updates)

    return CampaignEligibilityResult(
        campaign_id=campaign_id,
        is_eligible=new_status in [CampaignStatus.ELIGIBLE.value, CampaignStatus.APPROVED.value],
        status=new_status,
        eligible_for_production=new_status == CampaignStatus.APPROVED.value,
        reasons=reasons,
        checks=checks,
    )


@router.post("/{campaign_id}/review", response_model=CampaignResponse)
async def review_campaign(
    campaign_id: str,
    payload: CampaignReviewRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Operator approval or rejection of a campaign.
    Strict Safety Check: An approval attempt fails closed if ANY critical requirement is
    missing, ambiguous, expired, or non-gaming.
    """
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    decision = payload.decision.lower()
    if decision not in ["approve", "reject"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be 'approve' or 'reject'.",
        )

    if decision == "approve":
        # Comprehensive eligibility check
        new_status, reasons, checks = campaign_eligibility_service.evaluate_campaign(campaign)
        failed_checks = [k for k, v in checks.items() if not v]
        if failed_checks or new_status in [
            CampaignStatus.REJECTED.value,
            CampaignStatus.NEEDS_REVIEW.value,
            CampaignStatus.EXPIRED.value,
            CampaignStatus.EXHAUSTED.value,
        ]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve ineligible or incomplete campaign. Failed checks: {', '.join(failed_checks)}. Reasons: {'; '.join(reasons)}",
            )
        updates = {
            "status": CampaignStatus.APPROVED.value,
            "operator_approved": True,
            "review_notes": payload.notes,
            "rejection_reason": None,
        }
    else:
        updates = {
            "status": CampaignStatus.REJECTED.value,
            "operator_approved": False,
            "rejection_reason": payload.reason or "Rejected by operator",
            "review_notes": payload.notes,
        }

    updated = db.update_campaign(campaign_id, updates)
    return updated


@router.post("/{campaign_id}/sources", response_model=CampaignSourceResponse, status_code=status.HTTP_201_CREATED)
async def add_campaign_source(
    campaign_id: str,
    payload: CampaignSourceRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Register source footage authorizing short-form clipping for this campaign.
    All sources begin as 'pending_verification'. Requires operator verification.
    """
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    source = source_authorization_service.register_source(
        campaign_id=campaign_id,
        source_url=payload.source_url,
        original_creator=payload.original_creator,
        source_type=payload.source_type,
        permission_evidence=payload.permission_evidence,
        allowed_derivative_uses=payload.allowed_derivative_uses,
        authorized_platforms=payload.authorized_platforms,
        permission_expiry=payload.permission_expiry,
        notes=payload.notes,
    )
    return source


@router.post("/{campaign_id}/sources/{source_id}/verify", response_model=CampaignSourceResponse)
async def verify_campaign_source(
    campaign_id: str,
    source_id: str,
    payload: SourceVerifyRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Explicit operator verification of source footage rights.
    Validates non-vague evidence, platform authorization, and expiry.
    """
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    ok, reason, updated = source_authorization_service.operator_verify_source(
        source_id=source_id,
        verified_by=auth.user_id,
        notes=payload.notes,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=reason,
        )
    return updated


@router.get("/{campaign_id}/sources", response_model=List[CampaignSourceResponse])
async def list_campaign_sources(
    campaign_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """List all source footage records associated with this campaign."""
    return db.list_campaign_sources(campaign_id=campaign_id)


@router.post("/{campaign_id}/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign_clipping_job(
    campaign_id: str,
    payload: JobCreateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Enforce strict campaign eligibility gate and enqueue clipping job.
    Rejects job if campaign is not active and approved, or if source footage is unauthorized.
    """
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    # Locate source footage
    source_id = payload.source_id
    source = None
    if source_id:
        source = db.get_campaign_source(source_id)
    else:
        # Check if an authorized source exists matching this URL
        sources = db.list_campaign_sources(campaign_id=campaign_id)
        source = next((s for s in sources if s.get("source_url") == payload.url), None)
        if source:
            source_id = source.get("id")

    # Enforce Central Production Gate
    gate_ok, gate_reason, details = campaign_eligibility_service.validate_clipping_gate(
        campaign=campaign,
        source=source,
        target_platform=payload.target_platform,
    )
    if not gate_ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Production Gate Rejection: {gate_reason}",
        )

    # Duplicate check
    if not payload.force:
        active = db.find_active_job_by_url(payload.url)
        if active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An active processing job already exists for this source (Job ID: {active['id']}).",
            )

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    user_id = None if auth.user_id == "system_admin" else auth.user_id

    job_record = {
        "id": job_id,
        "user_id": user_id,
        "campaign_id": campaign_id,
        "source_id": source_id,
        "target_platform": payload.target_platform,
        "url": payload.url,
        "category_id": "gaming",
        "status": JobStatus.QUEUED.value,
        "stage": "pending",
        "progress": 0,
        "num_clips": payload.num_clips,
        "aspect_ratio": campaign.get("aspect_ratio", "9:16"),
        "target_format": payload.target_format,
        "language": payload.language,
        "mode": "local",
        "llm_provider": payload.llm_provider or settings.LLM_PROVIDER,
        "custom_prompt": payload.custom_prompt,
        "video_title": None,
        "video_duration": None,
        "error": None,
        "retry_count": 0,
    }

    db.create_job(job_record)
    db.add_log(job_id, "pending", f"Campaign job enqueued for '{campaign['name']}' ({payload.target_platform})")

    # Enqueue to Redis RQ background worker
    job_queue.enqueue_job(process_video_job, job_id)

    full_job = db.get_job(job_id)
    return full_job


@router.get("/{campaign_id}/clips", response_model=List[Dict[str, Any]])
async def list_campaign_clips(
    campaign_id: str,
    auth: AuthContext = Depends(verify_api_key),
):
    """List all clips produced for this campaign with compliance status."""
    return db.list_campaign_clips(campaign_id=campaign_id)


@router.post("/{campaign_id}/submit", response_model=ClipSubmissionResponse, status_code=status.HTTP_201_CREATED)
async def record_clip_submission(
    campaign_id: str,
    payload: ClipSubmissionRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Record live post submission evidence for verified earnings tracking."""
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    # Verify clip compliance before allowing submission
    comp = db.get_campaign_clip_compliance(payload.clip_id)
    if comp and not comp.get("submission_ready"):
        reasons = comp.get("failure_reasons") or ["Non-compliant clip"]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot submit non-compliant clip: {'; '.join(reasons)}",
        )

    submission = earnings_tracker.record_submission(
        clip_id=payload.clip_id,
        campaign_id=campaign_id,
        platform=payload.platform,
        submission_url=payload.submission_url,
        submission_post_id=payload.submission_post_id,
        submission_evidence=payload.submission_evidence,
        operator_notes=payload.operator_notes,
    )
    return submission


@router.patch("/submissions/{submission_id}/metrics", response_model=ClipSubmissionResponse)
async def update_submission_metrics(
    submission_id: str,
    payload: ClipMetricsUpdateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Update verified submission metrics with required evidence source and auditor identifier.
    Prevents negative or inconsistent financial values.
    """
    try:
        updated = earnings_tracker.update_metrics(
            submission_id=submission_id,
            evidence_source=payload.evidence_source,
            verified_by=auth.user_id,
            eligible_views=payload.eligible_views,
            estimated_rewards=payload.estimated_rewards,
            pending_rewards=payload.pending_rewards,
            approved_rewards=payload.approved_rewards,
            rejected_rewards=payload.rejected_rewards,
            withdrawable_earnings=payload.withdrawable_earnings,
            evidence_url=payload.evidence_url,
            status=payload.status,
            rejection_reason=payload.rejection_reason,
            operator_notes=payload.operator_notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Submission {submission_id} not found")
    return updated


@router.get("/earnings/summary", response_model=EarningsSummaryResponse)
async def get_earnings_summary(
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Retrieve verified earnings statistics and progress toward $500 30-day target.
    Strictly separates pending from confirmed rewards.
    """
    return earnings_tracker.get_summary()
