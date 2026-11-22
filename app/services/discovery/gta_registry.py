"""Verified GTA 6 Content Registry & High-Yield Moment Ingestion."""
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..supabase_client import db
from .base import DiscoveredContentItem, RightsStatus, SourceType
from .rights_manager import rights_manager
from .duplicate_detector import duplicate_detector

logger = logging.getLogger("gta6_shorts.registry")

GTA6_KEYWORDS = [
    "gta 6", "gta vi", "grand theft auto vi", "grand theft auto 6",
    "gta6", "gtavi", "gta vi: ", "gta 6: ", "vice city", "leonida",
    "jason and lucia", "rockstar games gta"
]


class SourceRegistryItem(BaseModel):
    id: str
    name: str
    creator_name: Optional[str] = None
    source_url: str
    source_type: str = "approved_creator"
    platform: str = "youtube"
    permission_status: str = "pending_verification"
    rights_verification_status: str = "unverified"
    attribution_requirements: Optional[str] = None
    permitted_derivative_uses: List[str] = Field(default_factory=lambda: ["vertical_clip", "remix"])
    permission_expiration: Optional[str] = None
    monitoring_status: str = "active"
    is_active: bool = False
    last_discovery_time: Optional[str] = None
    last_successful_ingestion: Optional[str] = None
    error_history: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: Optional[str] = None


class DiscoveredMoment(BaseModel):
    id: str
    source_id: str
    video_url: Optional[str] = None
    title: str
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    duration: float = Field(ge=1.0)
    score: float = Field(ge=0.0, le=10.0)
    hook_text: Optional[str] = None
    event_type: str = "gameplay"
    status: str = "detected"
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GTARegistryService:
    """Manages verified GTA 6 content sources, rights authorization, and viral moment ingestion."""

    def is_gta6_relevant(self, title: str, description: str = "", tags: Optional[List[str]] = None) -> Tuple[bool, str]:
        """Verify strict GTA 6 topicality before ingestion."""
        text = f"{title} {description} {' '.join(tags or [])}".lower()
        for kw in GTA6_KEYWORDS:
            if kw in text:
                return True, f"Matched GTA 6 keyword: '{kw}'"
        return False, "Not recognized as GTA 6 content. Autonomous engine only ingests GTA 6 topics."

    def list_sources(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all content sources registered for GTA 6."""
        sources = db.list_sources(category_id="gta6")
        if not sources:
            sources = db.list_sources()
        if active_only:
            sources = [s for s in sources if s.get("is_active", True)]
        return sources

    def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve source details by ID."""
        return db.get_source(source_id)

    def add_source(self, source_data: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new content source with complete rights and monitoring metadata."""
        src_id = source_data.get("id") or f"src_{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        source_record = {
            "id": src_id,
            "name": source_data.get("name") or "GTA 6 Content Source",
            "creator_name": source_data.get("creator_name") or source_data.get("creator_identity") or "Authorized Creator",
            "source_url": source_data.get("source_url") or source_data.get("feed_url") or source_data.get("channel_url") or "",
            "source_type": source_data.get("source_type", "approved_creator"),
            "platform": source_data.get("platform", "youtube"),
            "category_id": "gta6",
            "is_active": source_data.get("is_active", False),
            "monitoring_status": "active" if source_data.get("is_active") else "paused",
            "attribution_requirements": source_data.get("attribution_requirements", "Credit original creator in caption"),
            "permitted_derivative_uses": source_data.get("permitted_derivative_uses", ["vertical_short", "captioned_highlight"]),
            "permission_expiration": source_data.get("permission_expiration"),
            "last_discovery_time": None,
            "last_successful_ingestion": None,
            "error_history": [],
            "created_at": now_iso,
        }

        # Store in database
        saved = db.create_source(source_record)

        # Register initial rights permission
        initial_status = RightsStatus.PENDING_VERIFICATION
        if source_data.get("rights_status") in [RightsStatus.AUTHORIZED_EDITING.value, RightsStatus.AUTHORIZED_PLATFORMS.value]:
            initial_status = RightsStatus(source_data["rights_status"])

        rights_manager.set_permission(
            source_id=src_id,
            status=initial_status,
            allowed_platforms=source_data.get("allowed_platforms", ["youtube_shorts", "tiktok"]),
            license_type=source_data.get("license_type", "explicit_creator_permission"),
            notes=source_data.get("verification_notes", "Registered via GTA 6 Source Registry."),
            verified_by=source_data.get("verified_by"),
        )
        return saved

    def record_source_error(self, source_id: str, error_msg: str):
        """Append an error incident to source's error history."""
        now_iso = datetime.now(timezone.utc).isoformat()
        db.update_source_health(source_id, error=error_msg, last_polled_at=now_iso, increment_failures=True)

    def record_source_success(self, source_id: str):
        """Record successful ingestion milestone for source health."""
        now_iso = datetime.now(timezone.utc).isoformat()
        db.update_source_health(source_id, last_success_at=now_iso, reset_failures=True)

    def ingest_moment(
        self,
        source_id: str,
        video_url: Optional[str],
        title: str,
        start_time: float,
        end_time: float,
        score: float,
        hook_text: Optional[str] = None,
        event_type: str = "gameplay",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Ingest a discovered high-yield moment into the autonomous moment pool.
        Enforces source verification, GTA 6 relevance, and deduplication.
        """
        # 1. Verify source permissions
        allowed, msg = rights_manager.check_rights_for_production(source_id, target_platform="youtube_shorts")
        if not allowed:
            raise PermissionError(f"Cannot ingest moment: source '{source_id}' lacks verified rights ({msg})")

        # 2. Check GTA 6 relevance
        is_rel, match_msg = self.is_gta6_relevant(title, hook_text or "")
        if not is_rel:
            raise ValueError(f"Moment rejected: {match_msg}")

        duration = max(1.0, round(end_time - start_time, 2))
        hash_seed = f"{source_id}:{video_url or ''}:{title}:{start_time:.1f}:{end_time:.1f}"
        moment_id = f"mom_{hashlib.sha256(hash_seed.encode()).hexdigest()[:12]}"

        moment_data = {
            "id": moment_id,
            "source_id": source_id,
            "video_url": video_url,
            "title": title,
            "start_time": start_time,
            "end_time": end_time,
            "duration": duration,
            "score": round(score, 1),
            "hook_text": hook_text or f"WATCH THIS: {title.upper()}",
            "event_type": event_type,
            "status": "detected",
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        saved = db.save_discovered_moment(moment_data)
        self.record_source_success(source_id)
        logger.info("Ingested GTA 6 moment %s: %s (score %.1f, duration %.1fs)", moment_id, title, score, duration)
        return saved

    def list_moments(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """List discovered GTA 6 moments ordered by viral score."""
        return db.list_discovered_moments(status=status, limit=limit)

    def get_moment(self, moment_id: str) -> Optional[Dict[str, Any]]:
        """Get moment details by ID."""
        return db.get_discovered_moment(moment_id)

    def approve_moment(self, moment_id: str) -> bool:
        """Approve a discovered moment for immediate production pipeline."""
        return db.update_discovered_moment_status(moment_id, "approved")

    def reject_moment(self, moment_id: str) -> bool:
        """Reject a discovered moment."""
        return db.update_discovered_moment_status(moment_id, "rejected")


gta_registry = GTARegistryService()
