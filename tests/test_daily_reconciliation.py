import uuid
"""Tests for 24-hour analytics reconciliation for YouTube and TikTok/Buffer."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.services.reconciliation import reconciliation_engine, DailyReconciliationReport
from app.services.supabase_client import db


def test_reconciliation_report_structure():
    report = reconciliation_engine.generate_reconciliation_report()
    assert isinstance(report, dict)
    assert "report_id" in report
    assert "timestamp" in report
    assert "discrepancies" in report
    assert "reconciled_items" in report


def test_youtube_views_reconciliation(monkeypatch):
    clip_id = f"clip_reconcile_yt_{uuid.uuid4().hex[:8]}"
    db.create_clip({
        "id": clip_id,
        "title": "GTA 6 Car Physics Reconcile",
        "file_path": "/tmp/dummy.mp4",
        "publishing_status": "published",
    })
    q_id = f"sched_reconcile_yt_{uuid.uuid4().hex[:8]}"
    db.create_publishing_queue_item({
        "id": q_id,
        "clip_id": clip_id,
        "platform": "youtube",
        "platform_update_id": "yt_video_rec_123",
        "status": "published",
        "scheduled_for": datetime.now(timezone.utc).isoformat(),
    })

    # Mock YouTube videos().list().execute()
    mock_list = MagicMock()
    mock_list.execute.return_value = {
        "items": [
            {
                "id": "yt_video_rec_123",
                "statistics": {
                    "viewCount": "1250",
                    "likeCount": "88",
                    "commentCount": "12",
                },
            }
        ]
    }
    mock_videos = MagicMock()
    mock_videos.list.return_value = mock_list
    mock_service = MagicMock()
    mock_service.videos.return_value = mock_videos

    import googleapiclient.discovery
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *args, **kwargs: mock_service)

    stats = reconciliation_engine.reconcile_single_item(q_id)
    assert stats["success"] is True
    assert stats["platform"] == "youtube"
    assert stats.get("views") == 1250


def test_buffer_analytics_reconciliation(monkeypatch):
    clip_id = f"clip_reconcile_buf_{uuid.uuid4().hex[:8]}"
    db.create_clip({
        "id": clip_id,
        "title": "GTA 6 Stunt Reconcile Buffer",
        "file_path": "/tmp/dummy.mp4",
        "publishing_status": "published",
    })
    q_id = f"sched_reconcile_buf_{uuid.uuid4().hex[:8]}"
    db.create_publishing_queue_item({
        "id": q_id,
        "clip_id": clip_id,
        "platform": "tiktok",
        "platform_update_id": "buf_update_rec_456",
        "status": "published",
        "scheduled_for": datetime.now(timezone.utc).isoformat(),
    })

    from app.services.buffer_publisher import buffer_publisher
    mock_status = MagicMock(return_value={
        "status": "sent",
        "statistics": {
            "views": 4500,
            "clicks": 320,
            "shares": 45,
        },
    })
    monkeypatch.setattr(buffer_publisher, "get_update_status", mock_status)

    stats = reconciliation_engine.reconcile_single_item(q_id)
    assert stats["success"] is True
    assert stats["platform"] == "tiktok"
    assert stats.get("views") == 4500
