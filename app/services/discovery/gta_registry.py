"""Verified GTA 6 Content Registry & High-Yield Moment Ingestion."""
import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..supabase_client import db
from .base import DiscoveredContentItem, RightsStatus, SourceType
from .rights_manager import rights_manager

logger = logging.getLogger("gta6_shorts.registry")

GTA6_KEYWORDS = [
    "gta 6", "gta vi", "grand theft auto vi", "grand theft auto 6",
    "gta6", "gtavi", "gta vi: ", "gta 6: "
]


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


class GTARegistryService:
    """Manages verified GTA 6 content sources, rights authorization, and viral moment ingestion."""

    def __init__(self):
        # Sources and rights are never seeded or granted implicitly. An administrator
        # must register a source and record verifiable permission before production.
        pass

    def is_gta6_relevant(self, title: str, description: str = "", tags: Optional[List[str]] = None) -> Tuple[bool, str]:
        """Verify strict GTA 6 topicality before ingestion."""
        text = f"{title} {description} {' '.join(tags or [])}".lower()
        for kw in GTA6_KEYWORDS:
            if kw in text:
                return True, f"Matched GTA 6 keyword: '{kw}'"
        return False, "Not recognized as GTA 6 content. Autonomous engine only ingests GTA 6 topics."

    def list_sources(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all verified content sources."""
        sources = db.list_sources(category_id="gta6")
        if not sources:
            sources = db.list_sources()
        if active_only:
            sources = [s for s in sources if s.get("is_active", True)]
        return sources

    def add_source(self, source_data: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new verified content source with rights metadata."""
        src_id = source_data.get("id") or f"src_{uuid.uuid4().hex[:8]}"
        source_data["id"] = src_id
        source_data["category"] = "gta6"
        source_data["created_at"] = datetime.now(timezone.utc).isoformat()
        source_data["is_active"] = False
        saved = db.create_source(source_data)

        # Grant rights permission
        rights_status = RightsStatus.PENDING_VERIFICATION
        rights_manager.set_permission(
            source_id=src_id,
            status=rights_status,
            allowed_platforms=[],
            notes="Source registered; reuse rights require administrator verification.",
        )
        return saved

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
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        saved = db.save_discovered_moment(moment_data)
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


gta_registry = GTARegistryService()
