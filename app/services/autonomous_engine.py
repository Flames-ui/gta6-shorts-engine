"""Autonomous GTA 6 Short-Form Production Engine."""
import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..config import settings
from .clip_studio import CaptionItem, MultitrackTimeline, TimelineKeyframe, clip_studio, studio_hub
from .discovery.base import RightsStatus
from .discovery.duplicate_detector import duplicate_detector
from .discovery.gta_registry import gta_registry
from .discovery.rights_manager import rights_manager
from .gameplay_analyzer import gameplay_analyzer
from .internal_scheduler import internal_scheduler
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
        """
        Execute full connected autonomous production cycle:
        1. Find active rights-verified GTA 6 sources.
        2. Inspect permitted video assets and run gameplay analysis.
        3. Detect high-yield gameplay moments and rank candidates.
        4. Select top candidates exceeding min_score.
        5. Build multitrack timeline (trim, reframing, captions, hook, loudnorm, outro).
        6. Render vertical 9:16 video and validate.
        7. If auto_publish is enabled, schedule into internal publishing queue for TikTok and YouTube.
        8. Broadcast live SSE progress updates for real-time monitoring.
        """
        config = self.get_config()
        cycle_id = f"cycle_{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        logger.info("Starting autonomous cycle %s (target=%d, min_score=%.1f, auto_publish=%s)",
                    cycle_id, config.daily_target, config.min_score, config.auto_publish)

        # 1. Inspect registered GTA 6 sources
        all_sources = gta_registry.list_sources(active_only=True)
        verified_sources = []
        for s in all_sources:
            src_id = s.get("id")
            allowed, _ = rights_manager.check_rights_for_production(src_id, target_platform="youtube_shorts")
            if allowed:
                verified_sources.append(s)

        if not verified_sources:
            msg = (
                "No active rights-verified GTA 6 sources available. "
                "Register an approved source with verified editing rights before running autonomous production."
            )
            logger.info("Autonomous cycle %s completed early: %s", cycle_id, msg)
            return {
                "cycle_id": cycle_id,
                "status": "no_eligible_sources",
                "message": msg,
                "sources_checked": len(all_sources),
                "verified_sources_count": 0,
                "candidates_detected": 0,
                "clips_produced": [],
                "completed_at": now_iso,
            }

        # 2. Iterate through verified sources and process eligible videos
        produced_clips = []
        candidates_detected = 0

        for source in verified_sources:
            src_id = source.get("id")
            source_url = source.get("source_url") or source.get("channel_url")

            # Check if source has associated discovered or local video files
            discovered = db.list_discovered_content(source_id=src_id) if hasattr(db, "list_discovered_content") else []
            # Also check if media_url or source_url points to a local file
            video_files = []
            if source_url and os.path.isfile(source_url):
                video_files.append({"path": source_url, "title": source.get("name", "GTA 6 Gameplay"), "url": source_url})

            for disc in discovered:
                m_path = disc.get("media_url") or disc.get("original_url")
                if m_path and os.path.isfile(m_path):
                    video_files.append({"path": m_path, "title": disc.get("title", "GTA 6 Discovered Video"), "url": disc.get("original_url")})

            for vf in video_files:
                vpath = vf["path"]
                vtitle = vf["title"]

                await studio_hub.broadcast("global", "analyzing_gameplay", {
                    "cycle_id": cycle_id,
                    "source_id": src_id,
                    "video": vtitle,
                })

                # 3. Analyze gameplay footage
                candidates = gameplay_analyzer.analyze_gameplay_footage(
                    video_path=vpath,
                    source_id=src_id,
                    video_title=vtitle,
                )
                candidates_detected += len(candidates)

                # Filter candidates by score threshold
                eligible = [c for c in candidates if c.composite_score >= config.min_score]

                for cand in eligible:
                    # Ingest moment
                    moment_data = gta_registry.ingest_moment(
                        source_id=src_id,
                        video_url=vf.get("url"),
                        title=cand.suggested_hook,
                        start_time=cand.start_time,
                        end_time=cand.end_time,
                        score=cand.composite_score,
                        hook_text=cand.suggested_hook,
                        event_type=cand.event_type,
                    )

                    # Create clip record
                    clip_id = f"clip_{uuid.uuid4().hex[:10]}"
                    clip_record = {
                        "id": clip_id,
                        "job_id": cycle_id,
                        "title": cand.suggested_hook,
                        "hook_sentence": cand.suggested_hook,
                        "virality_reason": cand.why_interesting,
                        "score": cand.composite_score,
                        "start_time": cand.start_time,
                        "end_time": cand.end_time,
                        "duration": cand.duration,
                        "file_path": vpath,
                        "storage_url": vpath,
                        "publishing_status": "approved" if config.auto_publish else "draft",
                        "is_quality_approved": 1,
                        "created_at": now_iso,
                    }
                    db.create_clip(clip_record)

                    # Build multitrack timeline
                    timeline = clip_studio.get_or_create_timeline(clip_id)
                    timeline.video_track = {
                        "start_time": cand.start_time,
                        "end_time": cand.end_time,
                        "speed": 1.0,
                        "volume": 1.0,
                    }
                    timeline.hooks_track = {
                        "text": cand.suggested_hook,
                        "start": 0.0,
                        "end": 3.0,
                        "preset": "impact_banner",
                        "color": "#FFFFFF",
                    }
                    timeline.reframing_track = [
                        TimelineKeyframe(
                            time=kf.time_offset,
                            zoom=kf.zoom,
                            crop_x_percent=kf.crop_x_percent,
                            crop_y_percent=kf.crop_y_percent,
                        )
                        for kf in cand.keyframes
                    ]
                    timeline.outro_track["enabled"] = config.outro_enabled
                    clip_studio.save_timeline(clip_id, timeline.model_dump())

                    # Render timeline
                    render_res = clip_studio.render_timeline(clip_id)

                    # If auto_publish is active, schedule to internal publishing queue
                    scheduled_info = {}
                    if config.auto_publish:
                        tt_slot = internal_scheduler.schedule_clip(clip_id=clip_id, platform="tiktok")
                        yt_slot = internal_scheduler.schedule_clip(clip_id=clip_id, platform="youtube")
                        scheduled_info = {"tiktok_slot": tt_slot["id"], "youtube_slot": yt_slot["id"]}

                    produced_clips.append({
                        "clip_id": clip_id,
                        "title": cand.suggested_hook,
                        "event_type": cand.event_type,
                        "score": cand.composite_score,
                        "duration": render_res.get("duration"),
                        "output_path": render_res.get("output_path"),
                        "scheduled": scheduled_info,
                    })

                    if len(produced_clips) >= config.daily_target:
                        break

            if len(produced_clips) >= config.daily_target:
                break

        await studio_hub.broadcast("global", "cycle_completed", {
            "cycle_id": cycle_id,
            "clips_produced": len(produced_clips),
        })

        return {
            "cycle_id": cycle_id,
            "status": "completed",
            "verified_sources_checked": len(verified_sources),
            "candidates_detected": candidates_detected,
            "clips_produced": produced_clips,
            "total_produced": len(produced_clips),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }


autonomous_engine = AutonomousEngineService()
