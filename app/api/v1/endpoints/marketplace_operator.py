"""
API endpoints for dedicated browser automation, marketplace operator controls,
opportunity scoring, operator login flows, and action queue management.
"""
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ....services.browser_manager import browser_manager
from ....services.campaign_scheduler import campaign_scheduler
from ....services.marketplaces.content_rewards_operator import content_rewards_operator
from ....services.operator_action_queue import operator_action_queue
from ....services.opportunity_scorer import opportunity_scorer
from ....services.supabase_client import db
from ....auth import AuthContext, verify_api_key

logger = logging.getLogger("gta6_shorts.api.marketplace_operator")
router = APIRouter(prefix="/marketplace", tags=["marketplace-operator"])


class OperatorLoginResumeRequest(BaseModel):
    notes: Optional[str] = Field(default=None, description="Optional notes on login completion")


class CampaignApplyRequest(BaseModel):
    operator_approved_terms: bool = Field(
        ...,
        description="Explicit confirmation that operator approves joining the campaign and accepting terms"
    )


class ResolveActionRequest(BaseModel):
    notes: Optional[str] = Field(default=None, description="Resolution notes")


@router.get("/browser/status", response_model=Dict[str, Any])
def get_browser_status(auth: AuthContext = Depends(verify_api_key)):
    """Check dedicated browser process health, memory, and Content Rewards session status."""
    return content_rewards_operator.check_session_status()


@router.post("/browser/start", response_model=Dict[str, Any])
def start_browser(auth: AuthContext = Depends(verify_api_key)):
    """Start the dedicated persistent Chromium browser session."""
    started = browser_manager.start_browser()
    if not started:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to launch dedicated browser process."
        )
    return {
        "status": "running",
        "message": "Dedicated browser session started successfully.",
        "health": browser_manager.get_health_status(),
    }


@router.post("/browser/stop", response_model=Dict[str, Any])
def stop_browser(auth: AuthContext = Depends(verify_api_key)):
    """Stop the dedicated persistent browser session and persist encrypted state."""
    browser_manager.stop_browser()
    return {"status": "stopped", "message": "Dedicated browser session stopped cleanly."}


@router.post("/operator/login-request", response_model=Dict[str, Any])
def request_operator_login(auth: AuthContext = Depends(verify_api_key)):
    """
    Initiate the human operator login workflow:
    Navigates browser to Content Rewards login, captures diagnostic screenshot,
    and queues an operator action.
    """
    try:
        return content_rewards_operator.request_operator_login()
    except Exception as exc:
        logger.error("Operator login request error: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/operator/login-resume", response_model=Dict[str, Any])
def resume_operator_login(
    payload: OperatorLoginResumeRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Verify operator login completion, persist encrypted session cookies,
    and clear active login action alerts.
    """
    operator_id = str(getattr(auth, "user_id", "operator"))
    res = content_rewards_operator.resume_after_login(operator_id=operator_id)
    if not res.get("authenticated"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("message", "Login could not be verified. Ensure credentials and 2FA were completed.")
        )
    return res


@router.post("/campaigns/discover-live", response_model=List[Dict[str, Any]])
def discover_live_campaigns(
    limit: int = Query(default=10, ge=1, le=50),
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Execute live browser discovery on Content Rewards.
    Requires an authenticated operator session. Filters for gaming & TikTok/YouTube Shorts.
    """
    session_status = content_rewards_operator.check_session_status()
    if not session_status.get("authenticated"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot perform live discovery: session is unauthenticated. Complete operator login first."
        )

    try:
        campaigns = content_rewards_operator.discover_live_campaigns(limit=limit)
        # Store discovered campaigns in database
        saved = []
        for c in campaigns:
            existing = db.get_campaign(c["id"])
            if not existing:
                db.create_campaign(c)
                saved.append(c)
            else:
                saved.append(existing)
        return saved
    except Exception as exc:
        logger.error("Live discovery failure: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/campaigns/{campaign_id}/opportunity", response_model=Dict[str, Any])
def get_campaign_opportunity(campaign_id: str, auth: AuthContext = Depends(verify_api_key)):
    """Evaluate and return transparent opportunity score and breakdown for a campaign."""
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Campaign {campaign_id} not found.")

    return opportunity_scorer.score_campaign(campaign)


@router.post("/campaigns/{campaign_id}/apply", response_model=Dict[str, Any])
def apply_to_campaign(
    campaign_id: str,
    payload: CampaignApplyRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """
    Submit application or join campaign where permitted.
    Requires explicit operator confirmation of terms. Never accepts legal terms automatically.
    """
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Campaign {campaign_id} not found.")

    url = campaign.get("url")
    if not url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Campaign does not have a valid URL.")

    try:
        res = content_rewards_operator.apply_to_campaign(
            campaign_id=campaign_id,
            campaign_url=url,
            operator_approved_terms=payload.operator_approved_terms,
        )
        # Update campaign application status in DB
        db.update_campaign(campaign_id, {
            "application_status": res.get("application_status"),
            "review_notes": f"Applied on {res.get('applied_at')}: {res.get('message')}"
        })
        return res
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/actions", response_model=List[Dict[str, Any]])
def list_operator_actions(
    status: Optional[str] = Query(default="pending"),
    auth: AuthContext = Depends(verify_api_key),
):
    """List pending or historical operator actions and alerts."""
    return operator_action_queue.list_actions(status=status)


@router.post("/actions/{action_id}/resolve", response_model=Dict[str, Any])
def resolve_operator_action(
    action_id: str,
    payload: ResolveActionRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Mark an operator action resolved."""
    operator_id = str(getattr(auth, "user_id", "operator"))
    resolved = operator_action_queue.resolve_action(
        action_id=action_id,
        resolved_by=operator_id,
        notes=payload.notes,
    )
    if not resolved:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Action {action_id} not found.")
    return resolved


@router.post("/scheduler/refresh", response_model=Dict[str, Any])
def trigger_scheduled_refresh(auth: AuthContext = Depends(verify_api_key)):
    """Trigger an immediate campaign refresh cycle."""
    return campaign_scheduler.run_refresh_cycle()
