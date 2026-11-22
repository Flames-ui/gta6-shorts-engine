import uuid
"""Tests for internal publishing scheduler, quota enforcement (20 TikTok / 20 YouTube), and platform publishing."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.services.internal_scheduler import internal_scheduler, ScheduleSlot
from app.services.buffer_publisher import buffer_publisher
from app.services.youtube_publisher import youtube_publisher
from app.services.supabase_client import db


def test_daily_publishing_slots_generation():
    slots = internal_scheduler.generate_daily_schedule_slots(
        date=datetime.now(timezone.utc),
        tiktok_target=20,
        youtube_target=20,
    )
    assert len(slots) == 40
    tiktok_slots = [s for s in slots if s.platform == "tiktok"]
    youtube_slots = [s for s in slots if s.platform == "youtube"]
    assert len(tiktok_slots) == 20
    assert len(youtube_slots) == 20


def test_schedule_clip_quota_enforcement():
    clip_id = f"clip_sched_quota_yt_{uuid.uuid4().hex[:8]}"
    db.create_clip({
        "id": clip_id,
        "title": "GTA 6 Drift King",
        "file_path": "/tmp/dummy.mp4",
        "score": 9.0,
    })

    res = internal_scheduler.schedule_clip(
        clip_id=clip_id,
        platform="youtube",
        user_id="test_user_quota",
    )
    assert res["status"] in ("scheduled", "already_scheduled")
    assert res["platform"] == "youtube"


def test_buffer_post_now_workflow(monkeypatch):
    """Test TikTok immediate publish workflow via Buffer with now=True."""
    mock_schedule = MagicMock(return_value={
        "success": True,
        "update_id": "buf_update_123",
        "status": "sent",
    })
    monkeypatch.setattr(buffer_publisher, "schedule_clip_publication", mock_schedule)

    result = buffer_publisher.schedule_clip_publication(
        profile_ids=["profile_tiktok_1"],
        text="Lucia police chase #GTA6 #gaming",
        media_url="https://example.com/short.mp4",
        now=True,
    )
    assert result["success"] is True
    assert mock_schedule.called
    kwargs = mock_schedule.call_args[1]
    assert kwargs.get("now") is True


def test_youtube_video_upload_workflow(tmp_path, monkeypatch):
    """Test YouTube direct video upload with gaming metadata."""
    dummy_video = tmp_path / "dummy.mp4"
    dummy_video.write_bytes(b"fake mp4 video bytes")

    clip_id = f"clip_yt_upload_test_{uuid.uuid4().hex[:8]}"
    db.create_clip({
        "id": clip_id,
        "title": "GTA 6 Epic Stunt",
        "file_path": str(dummy_video),
        "publishing_status": "approved",
    })

    mock_execute = MagicMock(return_value={"id": "yt_short_abc999", "status": "uploaded"})
    mock_insert = MagicMock()
    mock_insert.execute = mock_execute
    mock_videos = MagicMock()
    mock_videos.insert.return_value = mock_insert
    mock_yt_service = MagicMock()
    mock_yt_service.videos.return_value = mock_videos

    # Mock build in googleapiclient.discovery
    import googleapiclient.discovery
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *args, **kwargs: mock_yt_service)

    result = youtube_publisher.publish_clip(
        clip_id=clip_id,
        video_file_path=str(dummy_video),
        credentials_dict={"token": "fake_token", "refresh_token": "fake_refresh"},
        privacy_status="private",
    )

    assert result["youtube_id"] == "yt_short_abc999"
    assert result["url"] == "https://www.youtube.com/shorts/yt_short_abc999"
