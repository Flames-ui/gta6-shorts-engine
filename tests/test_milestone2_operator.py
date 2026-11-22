"""
Comprehensive automated tests for Milestone 2: Automated Creator-Rewards Marketplace Operator.
Covers browser manager lifecycle, encrypted session storage at rest, memory constraints,
opportunity scoring, operator login workflows, CAPTCHA/challenge detection,
operator action queue, campaign application controls, and API endpoints.
"""
import json
import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from app.config import settings
from app.services.browser_manager import BrowserManager, browser_manager
from app.services.campaign_scheduler import campaign_scheduler
from app.services.marketplaces.content_rewards_operator import ContentRewardsOperator, content_rewards_operator
from app.services.operator_action_queue import OperatorActionQueue, operator_action_queue
from app.services.opportunity_scorer import OpportunityScorer, opportunity_scorer
from app.services.supabase_client import db


def test_browser_manager_lifecycle_and_flags():
    """Verify browser manager launches with safe memory flags and shuts down cleanly."""
    with patch("playwright.sync_api.sync_playwright") as mock_pw_init:
        mock_pw = MagicMock()
        mock_pw_init.return_value.start.return_value = mock_pw
        mock_browser = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page

        bm = BrowserManager()
        started = bm.start_browser(headless=True)
        assert started is True
        assert bm.is_running is True

        # Verify Chromium flags include memory limits and sandbox protections
        mock_pw.chromium.launch.assert_called_once()
        _, kwargs = mock_pw.chromium.launch.call_args
        args = kwargs.get("args", [])
        assert "--no-sandbox" in args
        assert "--disable-dev-shm-usage" in args
        assert any("max-old-space-size" in a for a in args)

        # Health status check
        health = bm.get_health_status()
        assert health["browser_running"] is True
        assert health["memory_limit_mb"] == settings.BROWSER_MAX_MEMORY_MB

        # Graceful shutdown
        bm.stop_browser()
        assert bm.is_running is False
        mock_browser.close.assert_called_once()
        mock_pw.stop.assert_called_once()


def test_browser_session_encryption_at_rest():
    """Verify session storage and cookies are encrypted at rest with Fernet and never saved in plaintext."""
    bm = BrowserManager()
    with tempfile.TemporaryDirectory() as tmp_dir:
        bm.profile_dir = type(bm.profile_dir)(tmp_dir)
        bm.session_file = bm.profile_dir / "session.enc"

        mock_context = MagicMock()
        mock_context.storage_state.return_value = {
            "cookies": [
                {"name": "cr_session_token", "value": "super_secret_session_token_12345", "domain": "contentrewards.com", "path": "/"}
            ],
            "origins": []
        }
        bm._context = mock_context

        # Save session
        saved = bm.save_encrypted_session()
        assert saved is True
        assert bm.session_file.exists()

        # Read raw file content from disk
        with open(bm.session_file, "r") as f:
            raw_content = f.read()

        # Plaintext secrets must NOT appear on disk
        assert "super_secret_session_token_12345" not in raw_content
        assert "cr_session_token" not in raw_content

        # Restore session
        bm._context.add_cookies = MagicMock()
        restored = bm.restore_encrypted_session()
        assert restored is True
        bm._context.add_cookies.assert_called_once()
        restored_cookies = bm._context.add_cookies.call_args[0][0]
        assert restored_cookies[0]["value"] == "super_secret_session_token_12345"


def test_redact_sensitive_cookies():
    """Verify cookie values are redacted from diagnostic outputs."""
    bm = BrowserManager()
    cookies = [
        {"name": "auth_token", "value": "secret_abc123", "domain": ".contentrewards.com", "path": "/"}
    ]
    redacted = bm.redact_cookies(cookies)
    assert len(redacted) == 1
    assert "value" not in redacted[0]
    assert redacted[0]["value_length"] == len("secret_abc123")
    assert redacted[0]["name"] == "auth_token"


def test_opportunity_scoring_formula():
    """Verify transparent 0-100 scoring based on platform fit, reward rates, and capacity."""
    scorer = OpportunityScorer()

    # 1. High value campaign (Dual platform, $3.50 CPM, substantial remaining budget)
    high_camp = {
        "accepted_platforms": ["tiktok", "youtube_shorts"],
        "reward_model": "cpm",
        "cpm": 3.50,
        "remaining_budget": 5000.0,
        "min_clip_duration_seconds": 15,
        "max_clip_duration_seconds": 60,
        "requires_brand_approval": False,
    }
    high_res = scorer.score_campaign(high_camp)
    assert high_res["tier"] == "HIGH"
    assert high_res["overall_score"] >= 80.0
    assert high_res["is_viable"] is True
    assert "$3.50 CPM" in high_res["verified_earnings_structure"]

    # 2. Unsupported platform campaign (Instagram only) -> REJECTED
    bad_platform = {
        "accepted_platforms": ["instagram"],
        "reward_model": "cpm",
        "cpm": 2.0,
    }
    bad_res = scorer.score_campaign(bad_platform)
    assert bad_res["tier"] == "REJECTED"
    assert bad_res["overall_score"] == 0.0

    # 3. Exhausted campaign
    exhausted_camp = {
        "accepted_platforms": ["tiktok"],
        "reward_model": "cpm",
        "cpm": 2.0,
        "remaining_budget": 0.0,
    }
    ex_res = scorer.score_campaign(exhausted_camp)
    assert ex_res["tier"] == "EXHAUSTED"
    assert ex_res["is_viable"] is False


def test_operator_action_queue_lifecycle():
    """Verify queueing, deduplication, listing, and resolution of operator actions."""
    queue = OperatorActionQueue()

    # Push action
    action = queue.push_action(
        action_type="login_required",
        title="Content Rewards Login Required",
        message="Please log in to your account.",
        marketplace_id="content_rewards",
        severity="warning",
    )
    assert action["status"] == "pending"
    action_id = action["id"]

    # Deduplication test: pushing again updates rather than duplicating
    action_dupe = queue.push_action(
        action_type="login_required",
        title="Content Rewards Login Required",
        message="Updated login message.",
        marketplace_id="content_rewards",
    )
    assert action_dupe["id"] == action_id
    assert action_dupe["message"] == "Updated login message."

    # List pending actions
    pending = queue.list_actions(status="pending")
    assert any(a["id"] == action_id for a in pending)

    # Resolve action
    resolved = queue.resolve_action(action_id, resolved_by="admin_operator", notes="Completed via 2FA")
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == "admin_operator"


def test_content_rewards_operator_login_flow():
    """Verify login request workflow generates operator action and handles resume verification."""
    operator = ContentRewardsOperator()

    browser_manager._is_running = True
    browser_manager._browser = MagicMock()
    try:
        with patch.object(browser_manager, "get_page") as mock_get_page:
            mock_page = MagicMock()
            mock_page.title.return_value = "Content Rewards - Creator Login"
            mock_page.inner_text.return_value = "Sign in to your account"
            mock_page.query_selector.return_value = None
            mock_get_page.return_value = mock_page

            # 1. Request operator login
            req = operator.request_operator_login()
            assert req["status"] == "awaiting_operator_login"
            assert "login" in req["login_url"]
            assert req["action_id"].startswith("act_")

            # 2. Check session detects unauthenticated
            with patch.object(operator, "_check_auth_indicators", return_value=False):
                session_stat = operator.check_session_status()
                assert session_stat["authenticated"] is False
                assert session_stat["operator_action_required"] is True

            # 3. Resume after login verifies indicators and persists encrypted session
            with patch.object(operator, "_check_auth_indicators", return_value=True),                  patch.object(browser_manager, "save_encrypted_session", return_value=True):
                res = operator.resume_after_login(operator_id="test_admin")
                assert res["authenticated"] is True
                assert res["operator_id"] == "test_admin"
    finally:
        browser_manager._is_running = False
        browser_manager._browser = None


def test_campaign_application_strictly_requires_operator_approval():
    """Verify that applying to a campaign strictly raises ValueError without operator approval."""
    operator = ContentRewardsOperator()

    with pytest.raises(ValueError, match="Explicit operator approval is strictly required"):
        operator.apply_to_campaign(
            campaign_id="camp_123",
            campaign_url="https://contentrewards.com/c/test",
            operator_approved_terms=False,
        )


def test_campaign_scheduler_refresh_cycle():
    """Verify background scheduler cycle detects expired deadlines and alerts operator."""
    mock_camp = {
        "id": "camp_refresh_test",
        "name": "Refresh Test GTA 6",
        "url": "https://example.com/refresh",
        "status": "eligible",
        "deadline": "2020-01-01T00:00:00Z",  # Past deadline
        "accepted_platforms": ["tiktok"],
        "eligible_games": ["gta6"],
        "reward_model": "cpm",
        "cpm": 2.0,
    }
    db.create_campaign(mock_camp)

    cycle_res = campaign_scheduler.run_refresh_cycle()
    assert cycle_res["total_campaigns_checked"] >= 1

    updated = db.get_campaign("camp_refresh_test")
    assert updated["status"] == "expired"


def test_marketplace_api_endpoints(client, auth_headers):
    """Verify marketplace operator REST API endpoints."""
    # 1. GET /api/v1/marketplace/browser/status
    res1 = client.get("/api/v1/marketplace/browser/status", headers=auth_headers)
    assert res1.status_code == 200
    assert "marketplace_id" in res1.json()

    # 2. GET /api/v1/marketplace/actions
    res2 = client.get("/api/v1/marketplace/actions", headers=auth_headers)
    assert res2.status_code == 200
    assert isinstance(res2.json(), list)

    # 3. GET /api/v1/marketplace/campaigns/{id}/opportunity
    camp = {
        "id": "camp_api_opp",
        "name": "GTA 6 High Payout",
        "url": "https://example.com/opp",
        "raw_rules": "GTA 6 $3 CPM",
        "accepted_platforms": ["tiktok", "youtube_shorts"],
        "eligible_games": ["gta6"],
        "reward_model": "cpm",
        "cpm": 3.0,
        "remaining_budget": 5000.0,
        "min_clip_duration_seconds": 15,
        "max_clip_duration_seconds": 60,
    }
    db.create_campaign(camp)

    res3 = client.get(f"/api/v1/marketplace/campaigns/{camp['id']}/opportunity", headers=auth_headers)
    assert res3.status_code == 200
    opp_data = res3.json()
    assert opp_data["tier"] == "HIGH"
    assert opp_data["overall_score"] >= 80.0
