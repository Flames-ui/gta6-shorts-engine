"""Autonomous GTA 6 Short-Form Production Engine."""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..config import settings
from .clip_studio import clip_studio, studio_hub
from .discovery.gta_registry import gta_registry
from .discovery.rights_manager import rights_manager
from .outro_service import outro_service
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.autonomous")


class AutonomousConfig(BaseModel):
    id: str = "default"
    is_active: bool = False
    daily_target: int = Field(default=15, ge=1, le=50)
    auto_publish: bool = False
    min_score: float = Field(default=8.0, ge=5.0, le=10.0)
    outro_enabled: bool = True
    updated_at: Optional[str] = None


class AutonomousEngineService:
    """Manages 24/7 hands-free autonomous discovery, clipping, rendering, and publishing for GTA 6."""

    def get_config(self) -> AutonomousConfig:
        """Fetch active autonomous pipeline configuration."""
        data = db.get_autonomous_config()
        return AutonomousConfig(**data)

    def update_config(self, updates: Dict[str, Any]) -> AutonomousConfig:
        """Update autonomous configuration."""
        current = self.get_config().model_dump()
        current.update(updates)
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        saved = db.save_autonomous_config(current)
        return AutonomousConfig(**saved)

    def get_daily_stats(self) -> Dict[str, Any]:
        """Calculate production throughput metrics for today."""
        config = self.get_config()
        clips = db.list_clips(limit=100)
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        today_clips = [c for c in clips if (c.get("created_at") or "").startswith(today_iso)]
        produced_today = len(today_clips)
        published_today = len([c for c in today_clips if c.get("publishing_status") == "published"])

        moments = gta_registry.list_moments(limit=100)
        detected_count = len(moments)
        avg_score = round(sum(m.get("score", 0.0) for m in moments) / max(1, len(moments)), 1) if moments else None

        return {
            "daily_target": config.daily_target,
            "produced_today": produced_today,
            "published_today": published_today,
            "target_progress_percent": min(100, int((produced_today / max(1, config.daily_target)) * 100)),
            "is_active": config.is_active,
            "auto_publish": config.auto_publish,
            "detected_moments_count": detected_count,
            "average_viral_score": avg_score if moments else None,
            "min_score_threshold": config.min_score,
        }

    async def run_autonomous_cycle(self) -> Dict[str, Any]:
        """Reject unimplemented autonomous ingestion rather than report fabricated work."""
        raise RuntimeError(
            "Autonomous source ingestion and gameplay analysis are not connected yet. "
            "No moments will be created or clips reported as produced until a verified media ingestion pipeline is configured."
        )



autonomous_engine = AutonomousEngineService()
