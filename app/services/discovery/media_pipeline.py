"""Discovered-content pipeline: metadata, authorized media, moment analysis, render gate.

Each stage writes its own status and an explicit reason to the item. A discovered record is never
marked processed by this pipeline; is_processed is left to the production clip path. Rendering is
not performed here, and render_gate reports why it is blocked.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..campaign_eligibility import CampaignEligibilityService
from ..supabase_client import db
from .media_ingestion import MediaIngestionError, check_authorized_media_url, ingest_authorized_media
from .moment_detection import MAX_ANALYSIS_SECONDS, MomentAnalysisError, ScoreFn, analyze_footage
from .poll_eligibility import evaluate_poll_eligibility
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

def render_gate(item: Dict[str, Any], source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Evaluate every render requirement. This only reports; nothing is rendered from here."""
    decision, rights_reason = _rights_decision(source)
    permission = rights_manager.get_permission(str(source.get("id"))) if source else None
    campaign_ok, campaign_reason, checks = CampaignEligibilityService().validate_clipping_gate(
        None, None, target_platform=RENDER_TARGET_PLATFORM,
    )
    media_ok = item.get("media_status") == "validated" and bool(item.get("media_sha256"))
    reasons = []
    if rights_reason:
        reasons.append(rights_reason)
    if not media_ok:
        reasons.append("media_not_validated")
    if not campaign_ok:
        reasons.append(campaign_reason)
    status = permission.get("rights_status") if permission else None
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "rights_status": getattr(status, "value", status),
        "campaign_checks": checks,
    }


def _update_analysis(item_id: str, updates: Dict[str, Any]) -> None:
    db.update_discovered_content(item_id, {**updates, "analysis_checked_at": _now_iso()})


def analyze_item(
    item_id: str,
    dest_dir: str = DEFAULT_MEDIA_DIR,
    score_fn: Optional[ScoreFn] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run motion, audio and scoring on validated footage. Only qualifying windows (score >= 75) are kept."""
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

    probe = item.get("media_probe") or {}
    duration = float(probe.get("duration_seconds") or 0.0)
    if duration <= 0:
        _update_analysis(item_id, {"analysis_status": "blocked", "analysis_reason": "duration_unknown"})
        return {"item_id": item_id, "analysis_status": "blocked", "analysis_reason": "duration_unknown"}
    if duration > MAX_ANALYSIS_SECONDS:
        reason = "duration_exceeds_synchronous_limit"
        _update_analysis(item_id, {"analysis_status": "blocked", "analysis_reason": reason})
        _log("analysis", item_id, source_id, "blocked", reason=reason, duration_seconds=duration)
        return {"item_id": item_id, "analysis_status": "blocked", "analysis_reason": reason}

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
        analysis = analyze_footage(path, item_id=item_id, source_id=str(source_id), duration=duration, score_fn=score_fn)
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
         qualifying=len(qualifying), render_allowed=gate["allowed"], render_reasons=gate["reasons"])
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
