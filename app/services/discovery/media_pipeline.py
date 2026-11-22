"""Discovered-content pipeline: metadata, authorized media, moment analysis, render gate.

Each stage writes its own status and an explicit reason to the item. A discovered record is never
marked processed by this pipeline; is_processed is left to the production clip path. Rendering is
not performed here, and render_gate reports why it is blocked.
"""
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..campaign_eligibility import CampaignEligibilityService
from ..supabase_client import db
from .media_ingestion import MediaIngestionError, check_authorized_media_url, ingest_authorized_media
from .moment_detection import (
    MAX_ANALYSIS_SECONDS,
    MAX_BACKGROUND_SECONDS,
    MomentAnalysisError,
    ScoreFn,
    analyze_footage,
)
from .poll_eligibility import evaluate_poll_eligibility
from .semantic_moments import SemanticFn, default_semantic_fn
from .rights_manager import rights_manager
from .youtube_metadata import (
    YouTubeMetadataError,
    availability_for,
    fetch_video_metadata,
    video_id_from_url,
)

logger = logging.getLogger("gta6_shorts.discovery.pipeline")

DEFAULT_MEDIA_DIR = os.path.join(tempfile.gettempdir(), "gta6_shorts_media")
RENDER_TARGET_PLATFORM = "tiktok"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(stage: str, item_id: str, source_id: Optional[str], outcome: str, **fields: Any) -> None:
    payload = {"stage": stage, "item_id": item_id, "source_id": source_id, "outcome": outcome, **fields}
    logger.info("discovery.pipeline %s", json.dumps(payload, default=str))


def _load(item_id: str) -> Dict[str, Any]:
    item = db.get_discovered_content(item_id)
    if not item:
        raise LookupError(f"Discovered item {item_id} not found")
    return item


def _load_source(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    source_id = item.get("source_id")
    return db.get_source(source_id) if source_id else None


def _rights_decision(source: Optional[Dict[str, Any]], now: Optional[datetime] = None):
    if source is None:
        return None, "source_not_found"
    permission = rights_manager.get_permission(str(source.get("id")))
    decision = evaluate_poll_eligibility(source, permission, now)
    return decision, (None if decision.eligible else f"rights_gate:{decision.reason}")


# ---------------------------------------------------------------- metadata

def _metadata_updates(item: Dict[str, Any], api_key: Optional[str] = None) -> Dict[str, Any]:
    now = _now_iso()
    video_id = item.get("youtube_video_id") or video_id_from_url(item.get("original_url"))
    base: Dict[str, Any] = {"metadata_checked_at": now}
    if not video_id:
        return {**base, "metadata_status": "failed", "metadata_error": "not_a_youtube_video"}
    base["youtube_video_id"] = video_id
    try:
        found = fetch_video_metadata([video_id], api_key=api_key)
    except YouTubeMetadataError as exc:
        return {**base, "metadata_status": "failed", "metadata_error": exc.code}
    meta = found.get(video_id)
    if meta is None:
        return {**base, "metadata_status": "failed", "metadata_error": "video_not_found"}

    availability = availability_for(meta)
    details = {
        **base,
        "title": meta.get("title") or item.get("title"),
        "creator_name": meta.get("channel_title") or item.get("creator_name"),
        "channel_title": meta.get("channel_title"),
        "thumbnail_url": meta.get("thumbnail_url"),
        "published_at": meta.get("published_at"),
        "availability": availability,
    }
    if meta.get("duration_seconds") is None:
        return {**details, "metadata_status": "failed", "metadata_error": "duration_missing"}
    if availability != "available":
        return {**details, "metadata_status": "failed", "metadata_error": f"video_not_available:{availability}"}
    return {
        **details,
        "metadata_status": "resolved",
        "metadata_error": None,
        "duration_seconds": meta["duration_seconds"],
    }


def resolve_metadata(item_id: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Resolve title, channel, thumbnail, duration and availability. Never marks the item processed."""
    item = _load(item_id)
    updates = _metadata_updates(item, api_key=api_key)
    db.update_discovered_content(item_id, updates)
    _log("metadata", item_id, item.get("source_id"), updates["metadata_status"],
         error=updates.get("metadata_error"), duration_seconds=updates.get("duration_seconds"))
    return {"item_id": item_id, **{k: updates.get(k) for k in (
        "metadata_status", "metadata_error", "duration_seconds", "availability", "title", "channel_title",
        "thumbnail_url", "published_at", "youtube_video_id")}}


# ---------------------------------------------------------------- media

def ingest_media(
    item_id: str,
    media_url: Optional[str] = None,
    dest_dir: str = DEFAULT_MEDIA_DIR,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Download and validate authorized footage. Records the outcome on the item either way."""
    item = _load(item_id)
    source = _load_source(item)
    source_id = item.get("source_id")

    decision, gate_reason = _rights_decision(source, now)
    if gate_reason:
        db.update_discovered_content(item_id, {"media_status": "blocked", "media_error": gate_reason, "media_checked_at": _now_iso()})
        _log("media", item_id, source_id, "blocked", reason=gate_reason)
        return {"item_id": item_id, "media_status": "blocked", "media_error": gate_reason}

    url = (media_url or "").strip() or item.get("media_url")
    if not url:
        db.update_discovered_content(item_id, {
            "media_status": "awaiting_authorized_media",
            "media_error": "no_authorized_media_url",
            "media_checked_at": _now_iso(),
        })
        _log("media", item_id, source_id, "awaiting_authorized_media", reason="no_authorized_media_url")
        return {"item_id": item_id, "media_status": "awaiting_authorized_media", "media_error": "no_authorized_media_url"}

    try:
        checked_url = check_authorized_media_url(url)
    except MediaIngestionError as exc:
        db.update_discovered_content(item_id, {"media_status": "failed", "media_error": f"{exc.code}: {exc}", "media_checked_at": _now_iso()})
        _log("media", item_id, source_id, "failed", reason=exc.code)
        return {"item_id": item_id, "media_status": "failed", "media_error": f"{exc.code}: {exc}"}

    previous_sha = item.get("media_sha256") if item.get("media_status") == "validated" else None
    db.update_discovered_content(item_id, {"media_status": "downloading", "media_error": None, "media_url": checked_url,
                                           "media_access_method": "authorized_url"})
    try:
        result = ingest_authorized_media(checked_url, item_id, dest_dir, expected_sha256=previous_sha)
    except MediaIngestionError as exc:
        db.update_discovered_content(item_id, {"media_status": "failed", "media_error": f"{exc.code}: {exc}", "media_checked_at": _now_iso()})
        _log("media", item_id, source_id, "failed", reason=exc.code)
        return {"item_id": item_id, "media_status": "failed", "media_error": f"{exc.code}: {exc}"}

    updates = {
        "media_status": "validated",
        "media_error": None,
        "media_sha256": result["sha256"],
        "media_bytes": result["bytes"],
        "media_probe": {**result["probe"], "content_type": result["content_type"]},
        "media_checked_at": _now_iso(),
    }
    db.update_discovered_content(item_id, updates)
    _log("media", item_id, source_id, "validated", sha256=result["sha256"], bytes=result["bytes"],
         duration_seconds=result["probe"]["duration_seconds"])
    return {"item_id": item_id, "media_status": "validated", "media_error": None,
            "media_sha256": result["sha256"], "media_bytes": result["bytes"], "media_probe": updates["media_probe"]}


# ---------------------------------------------------------------- analysis and gates

BACKGROUND_JOB_TIMEOUT_SECONDS = 6 * 3600


def campaign_gate_inputs(source: Optional[Dict[str, Any]]):
    """Find the campaign and the campaign-linked footage record the production gate checks.

    The discovery source must have a campaign_sources record with the same id, and that record must name
    an existing campaign. Returns (campaign, campaign_source, reason); reason is None only when both are linked.
    """
    if source is None:
        return None, None, "source_not_found"
    campaign_source = db.get_campaign_source(str(source.get("id")))
    if not campaign_source:
        return None, None, "no_campaign_source_record"
    campaign_id = campaign_source.get("campaign_id")
    if not campaign_id:
        return None, campaign_source, "campaign_source_unlinked"
    campaign = db.get_campaign(str(campaign_id))
    if not campaign:
        return None, campaign_source, "campaign_not_found"
    return campaign, campaign_source, None


def render_gate(item: Dict[str, Any], source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Evaluate every render requirement against the real campaign. This only reports; nothing is rendered here."""
    decision, rights_reason = _rights_decision(source)
    permission = rights_manager.get_permission(str(source.get("id"))) if source else None
    campaign, campaign_source, link_reason = campaign_gate_inputs(source)
    if link_reason:
        campaign_ok, campaign_reason, checks = False, f"campaign_link:{link_reason}", {}
    else:
        campaign_ok, message, checks = CampaignEligibilityService().validate_clipping_gate(
            campaign, campaign_source, target_platform=RENDER_TARGET_PLATFORM,
        )
        campaign_reason = None if campaign_ok else message
    media_ok = item.get("media_status") == "validated" and bool(item.get("media_sha256"))
    reasons = []
    if rights_reason:
        reasons.append(rights_reason)
    if not media_ok:
        reasons.append("media_not_validated")
    if campaign_reason:
        reasons.append(campaign_reason)
    status = permission.get("rights_status") if permission else None
    gate = {
        "allowed": not reasons,
        "reasons": reasons,
        "rights_status": getattr(status, "value", status),
        "campaign_id": campaign.get("id") if campaign else None,
        "campaign_checks": checks,
    }
    _log("render_gate", str(item.get("id")), str(item.get("source_id")),
         "allowed" if gate["allowed"] else "blocked", reasons=reasons, campaign_id=gate["campaign_id"])
    return gate


def _update_analysis(item_id: str, updates: Dict[str, Any]) -> None:
    db.update_discovered_content(item_id, {**updates, "analysis_checked_at": _now_iso()})


def enqueue_background_analysis(item_id: str, source_id: Optional[str]) -> Dict[str, Any]:
    """Hand footage longer than the synchronous limit to the background worker. Fails visibly if no queue."""
    from ...queue import job_queue

    job_id = f"discovery-analysis-{_safe(item_id)}-{uuid.uuid4().hex[:8]}"
    try:
        queued = bool(job_queue.enqueue_job(run_analysis_job, job_id, item_id, timeout=BACKGROUND_JOB_TIMEOUT_SECONDS))
    except Exception as exc:  # production enqueue raises when Redis is unreachable
        logger.error("discovery.pipeline enqueue failed item=%s error=%s", item_id, exc)
        queued = False
    if queued:
        _update_analysis(item_id, {"analysis_status": "queued", "analysis_reason": "background_analysis_queued"})
        _log("analysis", item_id, source_id, "queued", job_id=job_id)
        return {"item_id": item_id, "analysis_status": "queued", "analysis_reason": "background_analysis_queued",
                "job_id": job_id}
    _update_analysis(item_id, {"analysis_status": "failed", "analysis_reason": "queue_unavailable"})
    _log("analysis", item_id, source_id, "failed", reason="queue_unavailable")
    return {"item_id": item_id, "analysis_status": "failed", "analysis_reason": "queue_unavailable"}


def run_analysis_job(job_id: str, item_id: str) -> Dict[str, Any]:
    """Background worker entry point for footage longer than the synchronous limit."""
    _log("analysis", item_id, None, "job_started", job_id=job_id)
    return analyze_item(item_id, in_background=True)


def analyze_item(
    item_id: str,
    dest_dir: str = DEFAULT_MEDIA_DIR,
    score_fn: Optional[ScoreFn] = None,
    now: Optional[datetime] = None,
    in_background: bool = False,
    semantic_fn: Optional[SemanticFn] = None,
) -> Dict[str, Any]:
    """Run motion, audio, semantic and scoring on validated footage. Only qualifying windows (score >= 75) are kept.

    Analysis needs a semantic provider: without one the item is blocked, not scored with guessed evidence.
    """
    item = _load(item_id)
    source = _load_source(item)
    source_id = item.get("source_id")

    decision, gate_reason = _rights_decision(source, now)
    if gate_reason:
        _update_analysis(item_id, {"analysis_status": "blocked", "analysis_reason": gate_reason})
        _log("analysis", item_id, source_id, "blocked", reason=gate_reason)
        return {"item_id": item_id, "analysis_status": "blocked", "analysis_reason": gate_reason}

    if item.get("media_status") != "validated" or not item.get("media_sha256"):
        _update_analysis(item_id, {"analysis_status": "blocked", "analysis_reason": "media_not_validated"})
        _log("analysis", item_id, source_id, "blocked", reason="media_not_validated")
        return {"item_id": item_id, "analysis_status": "blocked", "analysis_reason": "media_not_validated"}

    semantic = semantic_fn if semantic_fn is not None else default_semantic_fn()
    if semantic is None:
        reason = "semantic_provider_not_configured"
        _update_analysis(item_id, {"analysis_status": "blocked", "analysis_reason": reason})
        _log("analysis", item_id, source_id, "blocked", reason=reason)
        return {"item_id": item_id, "analysis_status": "blocked", "analysis_reason": reason}

    probe = item.get("media_probe") or {}
    duration = float(probe.get("duration_seconds") or 0.0)
    if duration <= 0:
        _update_analysis(item_id, {"analysis_status": "blocked", "analysis_reason": "duration_unknown"})
        _log("analysis", item_id, source_id, "blocked", reason="duration_unknown")
        return {"item_id": item_id, "analysis_status": "blocked", "analysis_reason": "duration_unknown"}

    limit = MAX_BACKGROUND_SECONDS if in_background else MAX_ANALYSIS_SECONDS
    if duration > limit:
        if not in_background:
            return enqueue_background_analysis(item_id, source_id)
        reason = "duration_exceeds_background_limit"
        _update_analysis(item_id, {"analysis_status": "blocked", "analysis_reason": reason})
        _log("analysis", item_id, source_id, "blocked", reason=reason, duration_seconds=duration)
        return {"item_id": item_id, "analysis_status": "blocked", "analysis_reason": reason}

    _update_analysis(item_id, {"analysis_status": "analyzing", "analysis_reason": None})
    path = os.path.join(dest_dir, f"{_safe(item_id)}-{item['media_sha256'][:16]}.video")
    if not os.path.isfile(path):
        # The local copy is gone (for example after a restart): fetch it again and require the same hash.
        try:
            restored = ingest_authorized_media(item["media_url"], item_id, dest_dir, expected_sha256=item["media_sha256"])
        except (MediaIngestionError, KeyError) as exc:
            code = getattr(exc, "code", "media_unavailable")
            _update_analysis(item_id, {"analysis_status": "failed", "analysis_reason": f"media_unavailable:{code}"})
            _log("analysis", item_id, source_id, "failed", reason=f"media_unavailable:{code}")
            return {"item_id": item_id, "analysis_status": "failed", "analysis_reason": f"media_unavailable:{code}"}
        path = restored["path"]

    try:
        analysis = analyze_footage(path, item_id=item_id, source_id=str(source_id), duration=duration,
                                   max_seconds=limit, score_fn=score_fn, semantic_fn=semantic)
    except MomentAnalysisError as exc:
        _update_analysis(item_id, {"analysis_status": "failed", "analysis_reason": exc.code})
        _log("analysis", item_id, source_id, "failed", reason=exc.code)
        return {"item_id": item_id, "analysis_status": "failed", "analysis_reason": exc.code}

    qualifying = analysis["qualifying"]
    reason = "qualifying_moments_found" if qualifying else "no_candidate_reached_minimum_score"
    top = analysis["candidates"][:8]
    gate = render_gate(item, source)
    _update_analysis(item_id, {
        "analysis_status": "completed",
        "analysis_reason": reason,
        "qualifying_moment_count": len(qualifying),
        "analysis_candidates": top,
    })
    _log("analysis", item_id, source_id, "completed", reason=reason, candidate_windows=len(analysis["candidates"]),
         qualifying=len(qualifying), render_allowed=gate["allowed"], render_reasons=gate["reasons"],
         duration_seconds=duration, background=in_background)
    return {
        "item_id": item_id,
        "analysis_status": "completed",
        "analysis_reason": reason,
        "signals": analysis["signals"],
        "candidate_windows": len(analysis["candidates"]),
        "qualifying_moment_count": len(qualifying),
        "candidates": top,
        "render_gate": gate,
    }


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in value)[:80] or "item"
