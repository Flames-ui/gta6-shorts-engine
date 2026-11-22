"""Comprehensive production hardening, auth verification, and tenant isolation tests."""
import time
import jwt
import pytest
from app.config import settings
from app.services.discovery.base import RightsStatus
from app.services.discovery.rights_manager import rights_manager
from app.services.scheduler import schedule_manager
from app.services.supabase_client import db

TEST_JWT_SECRET = "super-secret-test-jwt-signature-key-minimum-32-chars-length"


@pytest.fixture(autouse=True)
def configure_jwt_secret():
    old_secret = settings.SUPABASE_JWT_SECRET
    settings.SUPABASE_JWT_SECRET = TEST_JWT_SECRET
    yield
    settings.SUPABASE_JWT_SECRET = old_secret


def make_test_jwt(sub: str, role: str = "authenticated", is_admin: bool = False, expired: bool = False, aud: str = "authenticated"):
    now = int(time.time())
    payload = {
        "sub": sub,
        "role": role,
        "aud": aud,
        "exp": now - 3600 if expired else now + 3600,
        "iat": now,
        "user_metadata": {"is_admin": is_admin},
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


# =============================================================================
# 1. JWT Security & Verification Tests
# =============================================================================

def test_jwt_verification_rejects_forged_signature(client):
    """Tokens with altered payloads or forged signatures must be rejected."""
    token = make_test_jwt(sub="user_victim", role="authenticated")
    # Tamper with token signature
    parts = token.split(".")
    tampered = f"{parts[0]}.{parts[1]}.invalidsignature123"

    res = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {tampered}"})
    assert res.status_code == 401
    assert "Invalid authentication credentials" in res.json()["detail"]


def test_jwt_verification_rejects_expired_token(client):
    """Expired tokens must be denied with 401."""
    token = make_test_jwt(sub="user_123", expired=True)
    res = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert "expired" in res.json()["detail"].lower()


def test_jwt_verification_rejects_wrong_audience(client):
    """Tokens with unexpected audience must be rejected."""
    token = make_test_jwt(sub="user_123", aud="wrong_audience")
    res = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_jwt_verification_accepts_valid_token(client):
    """Cryptographically valid token grants access to authenticated endpoints."""
    token = make_test_jwt(sub="user_valid_001")
    res = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


def test_unverified_admin_role_claim_rejected(client):
    """A standard authenticated user cannot perform administrative actions."""
    user_token = make_test_jwt(sub="user_regular", role="authenticated", is_admin=False)
    # Attempt admin action: update production schedule
    res = client.patch(
        "/api/v1/schedules",
        json={"daily_target_min": 10},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert res.status_code == 403
    assert "Administrator privileges required" in res.json()["detail"]


def test_verified_admin_token_allowed(client):
    """A verified admin user is permitted to perform administrative actions."""
    admin_token = make_test_jwt(sub="user_admin", role="admin", is_admin=True)
    res = client.patch(
        "/api/v1/schedules",
        json={"daily_target_min": 12},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    assert res.json()["daily_target_min"] == 12


# =============================================================================
# 2. Tenant Isolation and Resource Ownership Tests
# =============================================================================

def test_tenant_isolation_cross_user_clip_access_forbidden(client):
    """User B cannot access or modify a clip owned by User A."""
    user_a_token = make_test_jwt(sub="user_alice")
    user_b_token = make_test_jwt(sub="user_bob")

    clip_id = "clip_alice_private_001"
    db.create_clip({
        "id": clip_id,
        "job_id": "job_alice",
        "user_id": "user_alice",
        "title": "Alice's Private Clip",
        "score": 90,
        "start_time": 0.0,
        "end_time": 20.0,
        "duration": 20.0,
        "storage_url": "https://example.com/alice.mp4",
        "publishing_status": "draft",
    })

    # User A can access
    res_a = client.get(f"/api/v1/clips/{clip_id}", headers={"Authorization": f"Bearer {user_a_token}"})
    assert res_a.status_code == 200
    assert res_a.json()["id"] == clip_id

    # User B is forbidden (HTTP 403)
    res_b = client.get(f"/api/v1/clips/{clip_id}", headers={"Authorization": f"Bearer {user_b_token}"})
    assert res_b.status_code == 403
    assert "You do not own this clip" in res_b.json()["detail"]

    # User B cannot approve User A's clip
    res_b_approve = client.post(
        f"/api/v1/clips/{clip_id}/approve",
        json={"approved_for_publishing": True},
        headers={"Authorization": f"Bearer {user_b_token}"},
    )
    assert res_b_approve.status_code == 403


def test_missing_ownership_denied_fail_closed(client):
    """Resource with missing owner information results in denial for non-admins."""
    user_token = make_test_jwt(sub="user_charlie")
    clip_id = "clip_orphan_999"
    db.create_clip({
        "id": clip_id,
        "job_id": "job_orphan",
        "user_id": None,  # Missing ownership
        "title": "Orphan Clip",
        "score": 80,
        "start_time": 0.0,
        "end_time": 15.0,
        "duration": 15.0,
        "storage_url": "https://example.com/orphan.mp4",
        "publishing_status": "draft",
    })

    res = client.get(f"/api/v1/clips/{clip_id}", headers={"Authorization": f"Bearer {user_token}"})
    assert res.status_code == 403
    assert "Missing ownership information" in res.json()["detail"]


def test_list_clips_filters_by_authenticated_user(client):
    """Non-admin user only receives clips belonging to their account in list results."""
    user_token = make_test_jwt(sub="user_tenant_x")
    clip_id_x = "clip_tenant_x_001"
    clip_id_y = "clip_tenant_y_001"

    db.create_clip({
        "id": clip_id_x,
        "job_id": "job_x",
        "user_id": "user_tenant_x",
        "title": "Tenant X Clip",
        "score": 85,
        "start_time": 0.0,
        "end_time": 10.0,
        "duration": 10.0,
        "storage_url": "https://example.com/x.mp4",
    })
    db.create_clip({
        "id": clip_id_y,
        "job_id": "job_y",
        "user_id": "user_tenant_y",
        "title": "Tenant Y Clip",
        "score": 85,
        "start_time": 0.0,
        "end_time": 10.0,
        "duration": 10.0,
        "storage_url": "https://example.com/y.mp4",
    })

    res = client.get("/api/v1/clips", headers={"Authorization": f"Bearer {user_token}"})
    assert res.status_code == 200
    clip_ids = [c["id"] for c in res.json()]
    assert clip_id_x in clip_ids
    assert clip_id_y not in clip_ids


# =============================================================================
# 3. Content Discovery & Persistent Rights Management Tests
# =============================================================================

def test_source_registration_and_persistence(client, auth_headers):
    """Registering a source stores it in database and registers rights."""
    payload = {
        "source_type": "rss",
        "name": "Tech Highlights Feed",
        "feed_url": "https://example.com/tech_feed.xml",
        "category_id": "tech_ai",
        "rights_status": "pending_verification",
        "allowed_platforms": ["tiktok", "youtube_shorts"],
    }
    res = client.post("/api/v1/discovery/sources", json=payload, headers=auth_headers)
    assert res.status_code == 201
    source_id = res.json()["source"]["id"]

    # Verify source was persisted in database
    persisted = db.get_source(source_id)
    assert persisted is not None
    assert persisted["name"] == "Tech Highlights Feed"

    # Verify permission was persisted
    perm = db.get_source_permission(source_id)
    assert perm is not None
    assert perm["rights_status"] == "pending_verification"


def test_rights_manager_persists_across_instances():
    """Rights permissions are saved in database and can be reloaded."""
    src_id = "test_src_persist_001"
    rights_manager.set_permission(src_id, RightsStatus.AUTHORIZED_EDITING, notes="Contract #987")

    # Clear in-memory cache to simulate worker restart
    rights_manager._source_permissions.clear()

    # Querying rights should reload from database
    allowed, msg = rights_manager.check_rights_for_production(src_id)
    assert allowed is True
    assert "Authorized" in msg


# =============================================================================
# 4. OAuth State Persistence & Single-Use Validation Tests
# =============================================================================

def test_oauth_state_single_use_validation(client):
    """OAuth state can only be consumed once; replays must be rejected."""
    from app.services.buffer_publisher import buffer_publisher

    # Generate state
    url, state = buffer_publisher.get_authorization_url(user_id="user_oauth_test")
    assert state is not None

    # First consumption: state is valid
    record = db.validate_and_consume_oauth_state(state, service="buffer")
    assert record is not None
    assert record["user_id"] == "user_oauth_test"

    # Second consumption attempt (replay attack): must fail
    replay = db.validate_and_consume_oauth_state(state, service="buffer")
    assert replay is None


def test_buffer_callback_without_internal_api_key(client):
    """External OAuth callbacks do not require internal API keys; they validate state."""
    from app.services.buffer_publisher import buffer_publisher
    url, state = buffer_publisher.get_authorization_url(user_id="user_oauth_external")

    # Mock exchange_code_for_token on buffer_publisher
    orig_exchange = buffer_publisher.exchange_code_for_token
    buffer_publisher.exchange_code_for_token = lambda code: {"success": True, "access_token": "buf_mock_token_abc"}

    try:
        # Call without X-API-Key or Bearer token
        res = client.post("/api/v1/publishing/buffer/callback", json={"code": "auth_code_123", "state": state})
        assert res.status_code == 200
        assert res.json()["status"] == "connected"

        # Calling again with consumed state must fail with 400
        res_replay = client.post("/api/v1/publishing/buffer/callback", json={"code": "auth_code_123", "state": state})
        assert res_replay.status_code == 400
        assert "Invalid, expired, or previously consumed" in res_replay.json()["detail"]
    finally:
        buffer_publisher.exchange_code_for_token = orig_exchange


# =============================================================================
# 5. Worker & Queue Health Reporting Tests
# =============================================================================

def test_queue_never_reports_online_workers_when_redis_offline():
    """When Redis is not available, worker count must report 0, not 1."""
    from app.queue import JobQueue
    jq = JobQueue()
    # Force offline
    jq.has_redis = False
    stats = jq.get_stats()
    assert stats["workers_online"] == 0
    assert stats["queued_jobs"] == 0


def test_readiness_probe_endpoints(client):
    """Readiness and liveness endpoints respond accurately."""
    res_ready = client.get("/health/ready")
    assert res_ready.status_code in (200, 503)

    res_live = client.get("/health/live")
    assert res_live.status_code == 200
    assert res_live.json()["status"] == "alive"


# =============================================================================
# 6. Scheduling Quotas & Usage Tracking Tests
# =============================================================================

def test_daily_usage_tracking_and_schedule_api(client, auth_headers):
    """Schedule usage endpoint returns accurate usage metrics."""
    res = client.get("/api/v1/schedules/usage", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "total_scheduled" in data
    assert "target_min" in data
    assert "target_max" in data
    assert "category_counts" in data
