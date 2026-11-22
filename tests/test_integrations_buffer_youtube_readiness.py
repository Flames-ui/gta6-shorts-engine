"""Tests for Buffer GraphQL API, YouTube Data API v3, TikTok route, and Autonomous Readiness."""
import json
import pytest
from unittest.mock import patch, MagicMock
from app.services.supabase_client import db
from app.services.buffer_publisher import buffer_publisher
from app.services.youtube_publisher import youtube_publisher
from app.config import settings


def test_buffer_connect_and_channels_lifecycle(client, auth_headers):
    # 1. Reject empty API key
    res_bad = client.post("/api/v1/publishing/buffer/connect", json={"api_key": "   "}, headers=auth_headers)
    assert res_bad.status_code == 400

    # 2. Mock Buffer GraphQL responses
    fake_graphql_payload = {
        "data": {
            "account": {
                "id": "buf_acc_123",
                "email": "creator@vicecity.gta",
                "name": "Vice City Productions",
                "organizations": [
                    {
                        "id": "org_456",
                        "name": "Vice City Media Org",
                        "ownerEmail": "creator@vicecity.gta"
                    }
                ]
            },
            "channels": [
                {
                    "id": "chan_tt_789",
                    "name": "@gta6vicecity",
                    "displayName": "GTA 6 Vice City",
                    "service": "tiktok",
                    "avatar": "https://img.buffer.com/avatar_tt.png",
                    "isQueuePaused": False
                },
                {
                    "id": "chan_yt_101",
                    "name": "Vice City Shorts",
                    "displayName": "Vice City Shorts",
                    "service": "youtube",
                    "avatar": "https://img.buffer.com/avatar_yt.png",
                    "isQueuePaused": False
                }
            ]
        }
    }

    with patch.object(buffer_publisher, "execute_graphql", return_value=fake_graphql_payload):
        connect_res = client.post(
            "/api/v1/publishing/buffer/connect",
            json={"api_key": "buf_live_secret_key_abc123"},
            headers=auth_headers
        )
        assert connect_res.status_code == 200
        data = connect_res.json()
        assert data["status"] == "connected"
        assert data["channels_discovered"] == 2
        assert data["account_name"] == "Vice City Productions"

    # 3. Check Buffer status endpoint
    status_res = client.get("/api/v1/publishing/buffer/status", headers=auth_headers)
    assert status_res.status_code == 200
    st = status_res.json()
    assert st["connected"] is True
    assert st["account_id"] == "buf_acc_123"
    assert st["account_name"] == "Vice City Productions"
    assert st["channel_count"] == 2
    assert len(st["channels"]) == 2
    services = {c["service"] for c in st["channels"]}
    assert "tiktok" in services
    assert "youtube" in services

    # 4. Check TikTok status route reflects the Buffer TikTok channel
    tt_res = client.get("/api/v1/publishing/tiktok/status", headers=auth_headers)
    assert tt_res.status_code == 200
    tt_data = tt_res.json()
    assert tt_data["publishing_route"] == "Buffer-Mediated"
    assert tt_data["channel_count"] >= 1
    assert any(c["service"] == "tiktok" for c in tt_data["channels"])
    assert tt_data["capabilities"]["max_duration_seconds"] == 60.0
    assert tt_data["capabilities"]["aspect_ratio"] == "9:16"

    # 5. Disconnect Buffer
    disc_res = client.post("/api/v1/publishing/buffer/disconnect", headers=auth_headers)
    assert disc_res.status_code == 200
    assert disc_res.json()["status"] == "disconnected"

    # 6. Verify status returns disconnected
    status_after = client.get("/api/v1/publishing/buffer/status", headers=auth_headers)
    assert status_after.status_code == 200
    assert status_after.json()["connected"] is False


def test_youtube_status_and_processing_inspector(client, auth_headers):
    # Ensure clean state first
    client.post("/api/v1/publishing/youtube/disconnect", headers=auth_headers)

    # Status when not connected
    st_none = client.get("/api/v1/publishing/youtube/status", headers=auth_headers)
    assert st_none.status_code == 200
    assert st_none.json()["connected"] is False

    # Mock an authorized YouTube account
    fake_yt_account = {
        "id": "acc_yt_test",
        "user_id": "system_admin",
        "platform": "youtube",
        "account_id": "UC_gta6_vice_city_test",
        "account_name": "GTA 6 Official Shorts",
        "access_token": "mock_encrypted_token",
        "refresh_token": "mock_refresh_token",
        "token_expires_at": "2099-01-01T00:00:00",
        "is_active": True,
        "metadata": {
            "channel_title": "GTA 6 Official Shorts",
            "channel_id": "UC_gta6_vice_city_test",
            "custom_url": "@gta6shorts",
            "thumbnail": "https://yt3.ggpht.com/channel.jpg",
            "scopes": [
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"
            ]
        }
    }
    db.save_publishing_account(fake_yt_account)

    # Status when connected
    st_res = client.get("/api/v1/publishing/youtube/status", headers=auth_headers)
    assert st_res.status_code == 200
    yt_data = st_res.json()
    assert yt_data["connected"] is True
    assert yt_data["channel_id"] == "UC_gta6_vice_city_test"
    assert yt_data["channel_title"] == "GTA 6 Official Shorts"
    assert yt_data["upload_permission_granted"] is True
    assert yt_data["has_refresh_token"] is True
    assert yt_data["default_privacy"] == "private"

    # Non-destructive video processing check
    fake_video_info = {
        "status": "succeeded",
        "video_id": "dQw4w9WgXcQ",
        "upload_status": "uploaded",
        "privacy_status": "private",
        "embeddable": True,
        "processing_details": {"processingStatus": "succeeded"}
    }
    with patch.object(youtube_publisher, "check_processing_status", return_value=fake_video_info):
        proc_res = client.get("/api/v1/publishing/youtube/processing/dQw4w9WgXcQ", headers=auth_headers)
        assert proc_res.status_code == 200
        proc_data = proc_res.json()
        assert proc_data["status"] == "succeeded"
        assert proc_data["video_id"] == "dQw4w9WgXcQ"

    # Disconnect YouTube
    disc_res = client.post("/api/v1/publishing/youtube/disconnect", headers=auth_headers)
    assert disc_res.status_code == 200
    assert disc_res.json()["status"] == "disconnected"

    st_post = client.get("/api/v1/publishing/youtube/status", headers=auth_headers)
    assert st_post.json()["connected"] is False


def test_marketplace_operator_status(client, auth_headers):
    res = client.get("/api/v1/marketplace/accounts/status", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "marketplaces" in data
    mps = data["marketplaces"]
    assert "content_rewards" in mps
    assert "vyro" in mps
    assert "clipster" in mps
    assert mps["content_rewards"]["priority"] == 1
    assert mps["vyro"]["priority"] == 2
    assert mps["clipster"]["priority"] == 3


def test_autonomous_readiness_16_checks(client, auth_headers):
    res = client.get("/api/v1/autonomous/readiness", headers=auth_headers)
    assert res.status_code == 200
    readiness = res.json()

    assert "all_passed" in readiness
    assert "publishing_automation_enabled" in readiness
    assert "checks" in readiness
    checks = readiness["checks"]
    assert len(checks) == 16

    check_ids = {c["id"] for c in checks}
    expected_ids = {
        "operator_authenticated",
        "persistent_session_valid",
        "content_rewards_connected",
        "eligible_campaigns_discovered",
        "buffer_connected",
        "buffer_channels_retrieved",
        "youtube_connected",
        "youtube_upload_permission",
        "tiktok_route_verified",
        "google_drive_connected",
        "authorized_source_footage",
        "campaign_assets_available",
        "quality_analysis_operational",
        "rendering_worker_operational",
        "compliance_operational",
        "publishing_operational",
    }
    assert check_ids == expected_ids

    # Each check must have id, ready (bool), and message
    for c in checks:
        assert isinstance(c["ready"], bool)
        assert isinstance(c["message"], str)

    # In safe mode (PUBLISHING_AUTOMATION_ENABLED=false), autonomous execution must be guarded
    assert readiness["publishing_automation_enabled"] is settings.PUBLISHING_AUTOMATION_ENABLED
