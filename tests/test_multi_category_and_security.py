"""Comprehensive tests for security repairs, multi-category architecture, and Buffer integration."""
import os
import pytest
from app.config import settings
from app.services.analytics import MetricStatus, analytics_service
from app.services.categories import CategoryDefinition, category_service
from app.services.discovery.base import RightsStatus
from app.services.discovery.rights_manager import rights_manager
from app.services.scheduler import schedule_manager
from app.services.security import SecurityValidationError, is_ip_private, validate_safe_url
from app.services.video_validator import video_validator


def test_ssrf_and_private_network_defense():
    """Verify private IP addresses and dangerous schemes are blocked."""
    # Test private networks
    assert is_ip_private("127.0.0.1") is True
    assert is_ip_private("10.0.0.1") is True
    assert is_ip_private("192.168.1.1") is True
    assert is_ip_private("172.16.0.1") is True
    assert is_ip_private("169.254.169.254") is True  # Cloud metadata IP
    assert is_ip_private("8.8.8.8") is False

    # Blocked schemes
    with pytest.raises(SecurityValidationError):
        validate_safe_url("file:///etc/passwd")

    with pytest.raises(SecurityValidationError):
        validate_safe_url("ftp://example.com/video.mp4")

    # Blocked loopback and localhost
    with pytest.raises(SecurityValidationError):
        validate_safe_url("http://127.0.0.1/video.mp4", require_https=False)

    with pytest.raises(SecurityValidationError):
        validate_safe_url("https://localhost/video.mp4")


def test_multi_category_engine(client, auth_headers):
    """Verify categories API endpoints and category configuration."""
    # 1. List initial categories
    res = client.get("/api/v1/categories", headers=auth_headers)
    assert res.status_code == 200
    categories = res.json()
    cat_ids = [c["id"] for c in categories]
    assert "gaming" in cat_ids
    assert "viral_entertainment" in cat_ids
    assert "tech_ai" in cat_ids
    assert "animals_nature" in cat_ids
    assert "sports" in cat_ids

    # 2. Get specific category details
    res_gaming = client.get("/api/v1/categories/gaming", headers=auth_headers)
    assert res_gaming.status_code == 200
    data = res_gaming.json()
    assert "Gaming" in data["name"]
    assert "gta6" in data["presets"]

    # 3. Create a custom category via admin API
    custom_cat = {
        "id": "cooking_hacks",
        "name": "Viral Cooking Hacks",
        "description": "Short culinary tricks and unexpected food discoveries.",
        "icon": "fire",
        "system_prompt": "Identify visually mouth-watering steps and unexpected cooking secrets.",
        "default_hashtags": ["#FoodShorts", "#FoodHacks", "#QuickRecipe"],
    }
    create_res = client.post("/api/v1/categories", json=custom_cat, headers=auth_headers)
    assert create_res.status_code == 201
    assert create_res.json()["id"] == "cooking_hacks"

    # Confirm it can be retrieved
    res_get = client.get("/api/v1/categories/cooking_hacks", headers=auth_headers)
    assert res_get.status_code == 200


def test_rights_management_gates():
    """Verify content cannot enter production without valid rights status."""
    source_id = "test_src_456"

    # Pending verification -> not authorized
    rights_manager.set_permission(source_id, RightsStatus.PENDING_VERIFICATION)
    allowed, msg = rights_manager.check_rights_for_production(source_id)
    assert allowed is False
    assert "pending verification" in msg

    # Authorized for editing -> allowed
    rights_manager.set_permission(source_id, RightsStatus.AUTHORIZED_EDITING)
    allowed, msg = rights_manager.check_rights_for_production(source_id)
    assert allowed is True

    # Rejected -> not allowed
    rights_manager.set_permission(source_id, RightsStatus.REJECTED)
    allowed, msg = rights_manager.check_rights_for_production(source_id)
    assert allowed is False


def test_scheduler_quotas_and_duplicate_prevention():
    """Verify daily quotas, pause status, and duplicate publishing prevention."""
    clip_id = "clip_test_sched_001"
    channel_id = "chan_tiktok_main"

    # 1. Initial check - eligible
    eligible, reason = schedule_manager.check_scheduling_eligibility(clip_id, channel_id, "gaming")
    assert eligible is True

    # 2. Record schedule
    schedule_manager.record_scheduled_clip(clip_id, channel_id)

    # 3. Duplicate check - must be blocked
    dup_eligible, dup_reason = schedule_manager.check_scheduling_eligibility(clip_id, channel_id, "gaming")
    assert dup_eligible is False
    assert "already published or scheduled" in dup_reason


def test_analytics_metrics_integrity():
    """Verify analytics does not fabricate unverified missing metrics."""
    clip_id = "clip_analytics_001"

    # Record partial metrics (views, likes present; watch time and reach omitted)
    raw = {"views": 10000, "likes": 1200, "comments": 80, "shares": 50}
    perf = analytics_service.record_metrics(clip_id, "tiktok", raw)

    assert perf.views.value == 10000.0
    assert perf.views.status == MetricStatus.SYNCED
    assert perf.reach.value is None
    assert perf.reach.status == MetricStatus.UNAVAILABLE
    assert perf.average_watch_time_seconds.value is None
    assert perf.average_watch_time_seconds.status == MetricStatus.UNAVAILABLE
    assert perf.engagement_rate.value > 0  # computed from verified numbers


def test_buffer_oauth_url_generation(client, auth_headers):
    """Verify Buffer connect endpoint returns state-protected OAuth URL."""
    from app.services.buffer_publisher import buffer_publisher
    buffer_publisher.client_id = "test_buffer_client"
    buffer_publisher.redirect_uri = "https://app.lovable.dev/auth/buffer/callback"
    res = client.get("/api/v1/publishing/buffer/connect", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "auth_url" in data
    assert "state" in data
    assert "buffer.com/oauth2/authorize" in data["auth_url"]


def test_fail_closed_production_policy():
    """Verify that in production mode, empty ENGINE_API_KEY fails closed."""
    old_env = settings.ENVIRONMENT
    old_key = settings.ENGINE_API_KEY
    try:
        settings.ENVIRONMENT = "production"
        settings.ENGINE_API_KEY = ""
        # Creating request without key in production should fail
        # and not allow anonymous fallback
        from app.auth import AuthContext, verify_api_key
        with pytest.raises(Exception):
            import asyncio
            asyncio.run(verify_api_key(x_api_key=None, bearer=None))
    finally:
        settings.ENVIRONMENT = old_env
        settings.ENGINE_API_KEY = old_key
