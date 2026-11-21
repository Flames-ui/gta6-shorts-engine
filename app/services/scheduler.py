"""Automated scheduling, daily production limits, and publishing window controls."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from ..config import settings
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.scheduler")


class PublishingWindow(BaseModel):
    start_time: str = "11:00"  # HH:MM UTC
    end_time: str = "14:00"


class ProductionScheduleConfig(BaseModel):
    daily_target_min: int = Field(default=15, ge=1, le=100)
    daily_target_max: int = Field(default=20, ge=1, le=100)
    timezone: str = "UTC"
    is_paused: bool = False
    category_distribution: Dict[str, float] = Field(
        default_factory=lambda: {
            "gaming": 0.35,
            "viral_entertainment": 0.25,
            "tech_ai": 0.20,
            "sports": 0.10,
            "animals_nature": 0.10,
        }
    )
    publishing_windows: List[PublishingWindow] = Field(
        default_factory=lambda: [
            PublishingWindow(start_time="11:00", end_time="14:00"),
            PublishingWindow(start_time="17:00", end_time="22:00"),
        ]
    )


class ScheduleManager:
    """Manages daily production quotas, duplicate prevention, and queue scheduling."""

    def __init__(self):
        self.config = ProductionScheduleConfig(
            daily_target_min=settings.DAILY_TARGET_MIN,
            daily_target_max=settings.DAILY_TARGET_MAX,
        )
        # Tracks channel_id:clip_id to prevent duplicate publishing
        self._published_registry: Dict[str, datetime] = {}
        # Daily counts: date_str -> int
        self._daily_counts: Dict[str, int] = {}
        # Category daily counts: date_str -> {category: count}
        self._category_daily_counts: Dict[str, Dict[str, int]] = {}
        # Platform daily counts: date_str -> {platform: count}
        self._platform_daily_counts: Dict[str, Dict[str, int]] = {}

    def get_config(self) -> ProductionScheduleConfig:
        return self.config

    def update_config(self, new_config: ProductionScheduleConfig) -> ProductionScheduleConfig:
        self.config = new_config
        logger.info("Updated production schedule config: min=%d, max=%d, paused=%s",
                    new_config.daily_target_min, new_config.daily_target_max, new_config.is_paused)
        return self.config

    def check_scheduling_eligibility(
        self,
        clip_id: str,
        channel_id: str,
        category: str,
        override_quota: bool = False,
        platform: str = "tiktok",
    ) -> Tuple[bool, str]:
        """Verify whether a clip can be scheduled according to quotas, pause state, and duplicate checks."""
        if self.config.is_paused and not override_quota:
            return False, "Production schedule is currently paused."

        registry_key = f"{channel_id}:{clip_id}"
        if registry_key in self._published_registry:
            return False, f"Clip {clip_id} was already published or scheduled to channel {channel_id}."

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        current_daily_count = self._daily_counts.get(today_str, 0)

        if current_daily_count >= self.config.daily_target_max and not override_quota:
            return False, f"Daily production ceiling reached ({current_daily_count}/{self.config.daily_target_max} videos for {today_str})."

        # Check category-specific target if distribution specified
        cat_dist = self.config.category_distribution.get(category)
        if cat_dist is not None and not override_quota:
            cat_target = int(self.config.daily_target_max * cat_dist) + 1
            cat_count = self._category_daily_counts.get(today_str, {}).get(category, 0)
            if cat_count >= cat_target:
                return False, f"Category '{category}' daily target reached ({cat_count}/{cat_target} for {today_str})."

        return True, "Eligible for scheduling."

    def record_scheduled_clip(
        self,
        clip_id: str,
        channel_id: str,
        category: Optional[str] = None,
        platform: Optional[str] = None,
    ):
        """Register that a clip has been dispatched or scheduled to prevent duplicate runs."""
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._published_registry[f"{channel_id}:{clip_id}"] = datetime.now(timezone.utc)
        self._daily_counts[today_str] = self._daily_counts.get(today_str, 0) + 1

        if category:
            if today_str not in self._category_daily_counts:
                self._category_daily_counts[today_str] = {}
            self._category_daily_counts[today_str][category] = (
                self._category_daily_counts[today_str].get(category, 0) + 1
            )

        if platform:
            if today_str not in self._platform_daily_counts:
                self._platform_daily_counts[today_str] = {}
            self._platform_daily_counts[today_str][platform] = (
                self._platform_daily_counts[today_str].get(platform, 0) + 1
            )

    def get_daily_usage(self, date_str: Optional[str] = None) -> Dict[str, Any]:
        """Report daily usage metrics against target quotas."""
        d = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "date": d,
            "total_scheduled": self._daily_counts.get(d, 0),
            "target_min": self.config.daily_target_min,
            "target_max": self.config.daily_target_max,
            "is_paused": self.config.is_paused,
            "category_counts": self._category_daily_counts.get(d, {}),
            "platform_counts": self._platform_daily_counts.get(d, {}),
        }


schedule_manager = ScheduleManager()
