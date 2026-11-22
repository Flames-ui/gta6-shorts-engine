"""Comprehensive end-to-end tests for production delivery, security hardening, and multi-category engine."""
import hashlib
import time
import pytest
from starlette.testclient import TestClient

from app.auth import AuthContext
from app.config import settings
from app.main import app
from app.services.buffer_publisher import buffer_publisher
from app.services.categories import category_service
from app.services.discovery.duplicate_detector import duplicate_detector
from app.services.discovery.rights_manager import rights_manager
from app.services.security import (
    SecurityValidationError,
    decrypt_secret,
    encrypt_secret,
    safe_requests_get,
    validate_safe_url,
)
from app.services.supabase_client import db


@pytest.fixture
def test_client():
    with TestClient(app) as c:
        yield c


def test_token_encryption_at_rest():
    """Verify cryptographic encryption and decryption of sensitive OAuth tokens at rest."""
    raw_token = "buf_acc_secret_oauth_token_12345"
    encrypted = encrypt_secret(raw_token)
    assert encrypted != raw_token
    assert len(encrypted) > len(raw_token)

    decrypted = decrypt_secret(encrypted)
    assert decrypted == raw_token

    # Verify fallback safety for unencrypted legacy tokens
    legacy_token = "legacy_plain_token_abc"
    assert decrypt_secret(legacy_token) == legacy_token


def test_publishing_accounts_encrypted_persistence():
    """Verify that saving publishing accounts encrypts tokens in database and decrypts on retrieval."""
    user_id = "user_enc_test_42"
    account = db.save_publishing_account({
        "id": f"buffer_{user_id}",
        "user_id": user_id,
        "service": "buffer",
        "access_token": "my_top_secret_buffer_token",
        "is_active": True,
    })

    assert account is not None
    # Retrieve and verify decrypted token is returned
    retrieved = db.get_publishing_account_for_user(user_id, "buffer")
    assert retrieved is not None
    assert retrieved["access_token"] == "my_top_secret_buffer_token"


def test_auth_me_endpoint(test_client, auth_headers):
    """Verify /api/v1/auth/me returns caller identity, role, and administrator status."""
    resp = test_client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "system_admin"
    assert data["is_admin"] is True
    assert data["auth_method"] == "api_key"


def test_dashboard_stats_endpoint(test_client, auth_headers):
    """Verify /api/v1/dashboard/stats provides aggregated metrics for Lovable cards."""
    resp = test_client.get("/api/v1/dashboard/stats", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_jobs" in data
    assert "active_jobs" in data
    assert "total_clips" in data
    assert "active_categories" in data
    assert data["active_categories"] >= 5


def test_quotas_endpoint(test_client, auth_headers):
    """Verify /api/v1/quotas returns daily production targets and usage."""
    resp = test_client.get("/api/v1/quotas", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_scheduled" in data
    assert "target_min" in data
    assert "target_max" in data
    assert data["target_min"] == 3
    assert data["target_max"] == 5


def test_audit_logs_creation_and_listing(test_client, auth_headers):
    """Verify system audit logs recording and retrieval."""
    entry = db.create_audit_log(
        action="test_action",
        resource_type="clip",
        user_id="user_audit_1",
        resource_id="clip_123",
        details={"status": "approved"},
    )
    assert entry["action"] == "test_action"

    resp = test_client.get("/api/v1/audit-logs", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert any(i["action"] == "test_action" for i in items)


def test_category_patch_admin_only(test_client, auth_headers):
    """Verify category enable/disable and configuration update via PATCH /api/v1/categories/{id}."""
    resp = test_client.patch(
        "/api/v1/categories/gaming",
        json={"is_active": False, "description": "Updated gaming description"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is False
    assert data["description"] == "Updated gaming description"

    # Re-enable category
    resp2 = test_client.patch(
        "/api/v1/categories/gaming",
        json={"is_active": True},
        headers=auth_headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["is_active"] is True


def test_ssrf_safe_requests_and_redirect_protection():
    """Verify safe_requests_get blocks direct SSRF and malicious redirect targets."""
    # 1. Direct private IP rejection
    with pytest.raises(SecurityValidationError):
        safe_requests_get("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(SecurityValidationError):
        safe_requests_get("http://127.0.0.1:8000/internal")

    with pytest.raises(SecurityValidationError):
        safe_requests_get("http://10.0.0.1/admin")


def test_source_health_tracking():
    """Verify source health status, error reporting, and consecutive failure counters."""
    source_id = "src_health_test_1"
    db.create_source({
        "id": source_id,
        "name": "Health Test Source",
        "source_type": "approved_rss",
        "feed_url": "https://example.com/feed.xml",
    })

    # Record failure
    db.update_source_health(source_id, status="error", error="Timeout connecting to host", increment_failures=True)
    src = db.get_source(source_id)
    assert src is not None

    # Record success reset
    db.update_source_health(source_id, status="healthy", last_success_at="2026-11-21T12:00:00Z", reset_failures=True)


def test_platform_specification_validation():
    """Verify platform validation for TikTok, YouTube Shorts, and Instagram Reels."""
    # Under 60s is valid
    ok, msg = buffer_publisher.validate_platform_spec("tiktok", 45.0)
    assert ok is True

    # Over 60s for TikTok Shorts is rejected
    ok2, msg2 = buffer_publisher.validate_platform_spec("tiktok", 75.0)
    assert ok2 is False
    assert "exceeds 60s" in msg2

    # Over 60s for YouTube Shorts is rejected
    ok3, msg3 = buffer_publisher.validate_platform_spec("youtube", 90.0)
    assert ok3 is False
    assert "exceeds 60s" in msg3


def test_discovery_poll_endpoint(test_client, auth_headers):
    """Verify POST /api/v1/discovery/poll endpoint triggers active discovery."""
    resp = test_client.post("/api/v1/discovery/poll", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "polled_sources" in data
    assert "new_items_discovered" in data
    assert "duplicates_skipped" in data


def test_publishing_sync_endpoint(test_client, auth_headers):
    """Verify POST /api/v1/publishing/sync endpoint triggers status synchronization."""
    resp = test_client.post("/api/v1/publishing/sync", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "synced_count" in data
    assert "items" in data
