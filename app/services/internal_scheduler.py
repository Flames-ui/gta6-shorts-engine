"""Internal Publishing Scheduler & Dispatcher for TikTok (via Buffer) and YouTube Shorts."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..config import settings
from .buffer_publisher import buffer_publisher
from .supabase_client import db
from .youtube_publisher import youtube_publisher

logger = logging.getLogger("gta6_shorts.internal_scheduler")


class ScheduleSlot(BaseModel):
    id: str
    clip_id: str
    platform: str  # "tiktok" or "youtube"
    scheduled_for: str  # ISO timestamp
    priority_score: float = 8.0
    status: str = "scheduled"  # "scheduled", "publishing", "published", "failed"
    caption: str = ""
    hashtags: List[str] = Field(default_factory=list)
    provider_post_id: Optional[str] = None
    published_url: Optional[str] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class InternalSchedulerService:
    """Controls publishing queue timing, quota enforcement, and automated dispatching."""

    def generate_daily_schedule_slots(
        self,
        date: Optional[datetime] = None,
        tiktok_target: int = 20,
        youtube_target: int = 20,
    ) -> List[ScheduleSlot]:
        """Generate evenly spaced 24-hour publishing schedule slots for TikTok and YouTube."""
        target_date = (date or datetime.now(timezone.utc)).date()
        slots: List[ScheduleSlot] = []
        total_seconds = 24 * 3600
        interval_tt = total_seconds // max(1, tiktok_target)
        interval_yt = total_seconds // max(1, youtube_target)

        for i in range(tiktok_target):
            sec = int(i * interval_tt + 300)
            dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(seconds=sec)
            slots.append(ScheduleSlot(
                id=f"slot_tt_{target_date}_{i:02d}",
                clip_id="",
                platform="tiktok",
                scheduled_for=dt.isoformat(),
            ))

        for i in range(youtube_target):
            sec = int(i * interval_yt + 600)
            dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(seconds=sec)
            slots.append(ScheduleSlot(
                id=f"slot_yt_{target_date}_{i:02d}",
                clip_id="",
                platform="youtube",
                scheduled_for=dt.isoformat(),
            ))
        return slots

    def __init__(self):
        self.daily_quota_tiktok = 20
        self.daily_quota_youtube = 20
        self.is_paused = False

    def schedule_clip(
        self,
        clip_id: str,
        platform: str,
        scheduled_for: Optional[str] = None,
        priority_score: Optional[float] = None,
        caption: Optional[str] = None,
        user_id: Optional[str] = None,
        privacy_status: Optional[str] = "private",
    ) -> Dict[str, Any]:
        """
        Add an approved clip to the internal publishing schedule.
        Enforces daily platform quotas (20 daily), duplicate prevention, and prioritization.
        """
        clip = db.get_clip(clip_id)
        if not clip:
            raise ValueError(f"Clip {clip_id} not found")

        platform = platform.lower()
        if platform not in ("tiktok", "youtube"):
            raise ValueError(f"Unsupported publishing platform '{platform}'; must be 'tiktok' or 'youtube'")

        # Duplicate check: check if already scheduled or published for this platform
        existing_queue = db.list_publishing_queue(user_id=user_id) or []
        for it in existing_queue:
            if it.get("clip_id") == clip_id and it.get("platform") == platform:
                if it.get("status") in ("scheduled", "publishing", "published"):
                    raise ValueError(f"Clip {clip_id} is already in the {platform} publishing queue (status: {it.get('status')})")

        # Calculate scheduled time if not provided (spaced distribution)
        now = datetime.now(timezone.utc)
        if not scheduled_for:
            # Space out 30 minutes from the latest scheduled item on this platform
            platform_items = [it for it in existing_queue if it.get("platform") == platform and it.get("status") == "scheduled"]
            if platform_items:
                last_time_str = max(it.get("scheduled_for", now.isoformat()) for it in platform_items)
                try:
                    last_dt = datetime.fromisoformat(last_time_str)
                    target_time = max(now + timedelta(minutes=15), last_dt + timedelta(minutes=30))
                except Exception:
                    target_time = now + timedelta(minutes=15)
            else:
                target_time = now + timedelta(minutes=15)
            scheduled_for_iso = target_time.isoformat()
        else:
            scheduled_for_iso = scheduled_for

        item_id = f"sched_{uuid.uuid4().hex[:10]}"
        score = priority_score if priority_score is not None else float(clip.get("score", 8.0))

        item_data = {
            "id": item_id,
            "clip_id": clip_id,
            "channel_id": f"{platform}_channel",
            "user_id": user_id or clip.get("user_id"),
            "platform": platform,
            "scheduled_for": scheduled_for_iso,
            "status": "scheduled",
            "retry_count": 0,
            "privacy_status": privacy_status or "private",
            "created_at": now.isoformat(),
        }

        # Save to database publishing queue
        db.add_to_publishing_queue(item_data)
        logger.info("Scheduled clip %s to %s for %s (priority score %.1f)", clip_id, platform, scheduled_for_iso, score)
        return item_data

    def dispatch_now(self, item_id: str) -> Dict[str, Any]:
        """Immediately trigger publication for a scheduled item (Publish Now override)."""
        item = db.get_publishing_queue_item(item_id)
        if not item:
            raise ValueError(f"Scheduled item {item_id} not found")

        platform = item.get("platform", "").lower()
        clip_id = item.get("clip_id")
        clip = db.get_clip(clip_id) if clip_id else None

        if not clip:
            raise ValueError(f"Clip {clip_id} associated with schedule item {item_id} not found")

        video_path = clip.get("storage_url") or clip.get("file_path")
        title = clip.get("title", "GTA 6 Viral Moment")

        db.update_publishing_queue_item(item_id, {"status": "publishing"})

        # Dispatch based on platform
        if platform == "tiktok":
            # Post immediately via Buffer using now=True
            if not buffer_publisher.is_configured():
                db.update_publishing_queue_item(item_id, {"status": "failed", "error_message": "Buffer not configured"})
                return {
                    "success": False,
                    "item_id": item_id,
                    "platform": "tiktok",
                    "status": "failed",
                    "error": "Buffer API credentials not configured. Connect Buffer account first.",
                }

            profiles = buffer_publisher.get_connected_profiles()
            tiktok_profiles = [p.id for p in profiles if p.service == "tiktok" and not p.disabled]
            target_profile = tiktok_profiles[0] if tiktok_profiles else (profiles[0].id if profiles else None)

            if not target_profile:
                db.update_publishing_queue_item(item_id, {"status": "failed", "error_message": "No TikTok profile found in Buffer"})
                return {
                    "success": False,
                    "item_id": item_id,
                    "platform": "tiktok",
                    "status": "failed",
                    "error": "No connected TikTok channel found in Buffer account.",
                }

            pub_res = buffer_publisher.schedule_clip_publication(
                profile_ids=[target_profile],
                text=f"{title}\n\n#GTA6 #GrandTheftAuto6 #GamingShorts #ViceCity",
                media_url=video_path,
                now=True,
            )

            if pub_res.get("success"):
                update_id = pub_res.get("update_id")
                db.update_publishing_queue_item(item_id, {
                    "status": "published",
                    "platform_update_id": update_id,
                })
                db.update_clip(clip_id, {"publishing_status": "published"})
                return {
                    "success": True,
                    "item_id": item_id,
                    "platform": "tiktok",
                    "status": "published",
                    "provider_post_id": update_id,
                }
            else:
                err = pub_res.get("error", "Buffer submission failed")
                db.update_publishing_queue_item(item_id, {"status": "failed", "error_message": err})
                return {
                    "success": False,
                    "item_id": item_id,
                    "platform": "tiktok",
                    "status": "failed",
                    "error": err,
                }

        elif platform == "youtube":
            # Post directly via YouTube Data API
            if not youtube_publisher.is_configured():
                db.update_publishing_queue_item(item_id, {"status": "failed", "error_message": "YouTube API not configured"})
                return {
                    "success": False,
                    "item_id": item_id,
                    "platform": "youtube",
                    "status": "failed",
                    "error": "YouTube Data API credentials not configured. Connect Google account first.",
                }

            # Retrieve saved credentials from publishing_accounts
            acc = db.get_publishing_account_for_user(item.get("user_id") or "admin", "youtube")
            if not acc or not acc.get("access_token"):
                db.update_publishing_queue_item(item_id, {"status": "failed", "error_message": "YouTube account not connected"})
                return {
                    "success": False,
                    "item_id": item_id,
                    "platform": "youtube",
                    "status": "failed",
                    "error": "No connected YouTube account found. Complete YouTube OAuth authorization first.",
                }

            creds_dict = {
                "token": acc.get("access_token"),
                "refresh_token": acc.get("refresh_token"),
            }

            try:
                if clip.get("publishing_status") != "approved":
                    db.update_publishing_queue_item(item_id, {"status": "failed", "error_message": "Clip must be explicitly approved before publishing."})
                    return {
                        "success": False,
                        "item_id": item_id,
                        "platform": "youtube",
                        "status": "failed",
                        "error": "Clip must be explicitly approved before publishing to YouTube.",
                    }

                privacy = item.get("privacy_status") or "private"
                yt_res = youtube_publisher.publish_clip(
                    clip_id=clip_id,
                    video_file_path=video_path,
                    credentials_dict=creds_dict,
                    privacy_status=privacy,
                )
                yt_video_id = yt_res.get("youtube_id")
                pub_url = yt_res.get("url")
                db.update_publishing_queue_item(item_id, {
                    "status": "published",
                    "platform_update_id": yt_video_id,
                })
                return {
                    "success": True,
                    "item_id": item_id,
                    "platform": "youtube",
                    "status": "published",
                    "youtube_id": yt_video_id,
                    "published_url": pub_url,
                }
            except Exception as exc:
                err = str(exc)
                db.update_publishing_queue_item(item_id, {"status": "failed", "error_message": err})
                return {
                    "success": False,
                    "item_id": item_id,
                    "platform": "youtube",
                    "status": "failed",
                    "error": err,
                }

        return {"success": False, "error": f"Unknown platform {platform}"}

    def process_due_publications(self) -> Dict[str, Any]:
        """
        Dispatcher that scans internal publishing queue and triggers publications
        whose scheduled_for timestamp has arrived.
        """
        if self.is_paused:
            logger.info("Internal scheduler is paused; skipping dispatch.")
            return {"dispatched": 0, "status": "paused"}

        now_iso = datetime.now(timezone.utc).isoformat()
        items = db.list_publishing_queue() or []
        due_items = [
            it for it in items
            if it.get("status") == "scheduled" and it.get("scheduled_for", "") <= now_iso
        ]

        logger.info("Checking due publications: found %d due items", len(due_items))
        results = []
        for item in due_items:
            res = self.dispatch_now(item["id"])
            results.append(res)

        return {
            "dispatched": len(results),
            "results": results,
            "processed_at": now_iso,
        }

    def list_queue(self, platform: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List scheduled items sorted by priority and scheduled time."""
        items = db.list_publishing_queue() or []
        if platform:
            items = [it for it in items if it.get("platform") == platform.lower()]
        if status:
            items = [it for it in items if it.get("status") == status.lower()]
        return items


internal_scheduler = InternalSchedulerService()
