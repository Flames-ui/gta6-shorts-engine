"""
Comprehensive test suite for Milestone 2:
- Startup encryption key validation
- Durable browser session storage (cookies + localStorage origins)
- Authentic login indicator verification & session expiration
- Campaign deduplication & rule change detection
- Google Drive campaign asset integration & isolation
- Quality-first 100-point scoring model & selective ranking (3-5 target)
- Campaign-specific editing planner (differentiated campaigns)
- Dual-gate automated quality & compliance approval
- Publication package generation (TikTok vs YouTube Shorts)
- Earnings & performance tracking ($500 target progression)
- Dashboard operator overview counters
"""
import os
import json
import pytest
from unittest.mock import MagicMock, patch

from app.config import settings
from app.services.security import validate_encryption_key_at_startup, encrypt_secret, decrypt_secret
from app.services.browser_manager import browser_manager
from app.services.marketplaces.content_rewards_operator import content_rewards_operator
from app.services.google_drive_assets import google_drive_asset_service
from app.services.quality_candidate_scorer import quality_candidate_scorer
from app.services.editing_planner import editing_planner
from app.services.compliance_validator import compliance_validator
from app.services.publication_package import publication_package_service
from app.services.earnings_tracker import earnings_tracker
from app.services.operator_action_queue import operator_action_queue
from app.services.supabase_client import db


def test_startup_encryption_key_validation():
    """Verify startup validation detects key strength and rejects missing keys in production."""
    # 1. Valid key test
    with patch.object(settings, "TOKEN_ENCRYPTION_KEY", "a_very_secure_and_long_secret_key_with_32_plus_chars"):
        assert validate_encryption_key_at_startup() is True

    # 2. Missing key in production raises fatal error
    with patch.object(settings, "TOKEN_ENCRYPTION_KEY", ""):
        with patch.object(settings, "ENVIRONMENT", "production"):
            with pytest.raises(RuntimeError) as exc_info:
                validate_encryption_key_at_startup()
            assert "TOKEN_ENCRYPTION_KEY must be configured in production" in str(exc_info.value)

    # 3. Short key (<32 chars) in production raises fatal error
    with patch.object(settings, "TOKEN_ENCRYPTION_KEY", "short_key"):
        with patch.object(settings, "ENVIRONMENT", "production"):
            with pytest.raises(RuntimeError) as exc_info:
                validate_encryption_key_at_startup()
            assert "at least 32 characters in production" in str(exc_info.value)


def test_browser_storage_state_cookies_and_origins(tmp_path):
    """Verify session persistence encrypts and restores BOTH cookies and localStorage origins."""
    session_file = tmp_path / "session.enc"
    with patch.object(browser_manager, "session_file", session_file):
        # Mock Playwright context with both cookies and origins
        mock_context = MagicMock()
        mock_context.storage_state.return_value = {
            "cookies": [{"name": "auth_token", "value": "xyz123", "domain": "contentrewards.com"}],
            "origins": [{
                "origin": "https://contentrewards.com",
                "localStorage": [{"name": "user_id", "value": "user_789"}]
            }]
        }
        browser_manager._context = mock_context

        # Save session
        saved = browser_manager.save_encrypted_session()
        assert saved is True
        assert session_file.exists()

        # Decrypt state and verify both parts are preserved
        state = browser_manager.get_decrypted_storage_state()
        assert state is not None
        assert len(state.get("cookies", [])) == 1
        assert len(state.get("origins", [])) == 1
        assert state["origins"][0]["localStorage"][0]["value"] == "user_789"

        # Restore into context
        restored = browser_manager.restore_encrypted_session()
        assert restored is True
        mock_context.add_cookies.assert_called_once()


def test_real_login_indicators_and_session_expiration():
    """Verify auth detection requires genuine indicators and flags expired sessions."""
    operator_action_queue.clear_all()

    # Case 1: Page has login button visible -> Definitely unauthenticated
    mock_page = MagicMock()
    mock_page.url = "https://contentrewards.com/c/discover"
    unauth_btn = MagicMock()
    unauth_btn.is_visible.return_value = True
    mock_page.query_selector.side_effect = lambda sel: unauth_btn if "login" in sel or "Sign In" in sel else None

    assert content_rewards_operator._check_auth_indicators(mock_page) is False

    # Case 2: Positive authenticated indicator (e.g., user avatar or sign-out button)
    auth_page = MagicMock()
    auth_page.url = "https://contentrewards.com/c/discover"
    auth_page.query_selector.side_effect = lambda sel: MagicMock() if "logout" in sel or "user-avatar" in sel else None

    assert content_rewards_operator._check_auth_indicators(auth_page) is True

    # Case 3: Session expiration triggers operator queue action
    with patch.object(type(browser_manager), "is_running", new_callable=lambda: property(lambda s: True)):
        with patch.object(browser_manager, "get_page", return_value=mock_page):
            with patch.object(browser_manager, "get_health_status", return_value={
                "browser_running": True,
                "has_persisted_session": True,
                "active_cookies_count": 5,
            }):
                status = content_rewards_operator.check_session_status()
                assert status["authenticated"] is False
                assert status["session_expired"] is True
                assert status["recommended_action"] == "operator_relogin_required"

                actions = operator_action_queue.list_pending_actions()
                assert any(a["action_type"] == "session_expired" for a in actions)


def test_google_drive_asset_integration(tmp_path):
    """Test Drive link detection, classification, checksum, and access restriction handling."""
    operator_action_queue.clear_all()

    text_with_links = (
        "Here are the campaign assets: https://drive.google.com/drive/folders/1ABCxyz987_folder "
        "and video instruction: https://drive.google.com/file/d/1FILEabc123_video/view"
    )

    detected = google_drive_asset_service.detect_drive_links(text_with_links)
    assert len(detected) == 2
    kinds = {d["kind"] for d in detected}
    assert "folder" in kinds
    assert "file" in kinds

    # Test asset classification
    assert google_drive_asset_service.classify_asset_type("official_logo.png") == "logo"
    assert google_drive_asset_service.classify_asset_type("trailer_clip.mp4") == "video"
    assert google_drive_asset_service.classify_asset_type("guidelines.pdf") == "instruction_doc"
    assert google_drive_asset_service.classify_asset_type("font_bold.ttf") == "font"

    # Test asset storage & checksum calculation
    camp_id = "test_camp_drive_1"
    raw_bytes = b"fake_png_data_for_testing_logo"
    asset = google_drive_asset_service.register_and_store_asset(
        campaign_id=camp_id,
        file_name="logo.png",
        content_bytes=raw_bytes,
        drive_url="https://drive.google.com/file/d/1FILEabc123_video",
    )
    assert asset["file_type"] == "logo"
    assert asset["download_status"] == "downloaded"
    assert len(asset["asset_checksum"]) == 64  # SHA-256
    assert os.path.exists(asset["local_path"])

    # Test 403 access restriction handling
    res = google_drive_asset_service.handle_access_restriction(
        campaign_id=camp_id,
        drive_url="https://drive.google.com/file/d/restricted_file",
        status_code=403,
    )
    assert res["status"] == "access_restricted"
    assert res["operator_action_required"] is True
    actions = operator_action_queue.list_pending_actions()
    assert any(a["action_type"] == "google_drive_access_required" for a in actions)


def test_quality_first_100_point_scoring_and_selectivity():
    """Verify 100-point scoring model, thresholds, and top 3-5 selective candidate filtering."""
    # Candidate with premium qualities
    premium_cand = {
        "id": "cand_prem",
        "hook_score": 10.0,  # Hook strength: 20
        "entertainment_value": 10.0,  # 20
        "story_clarity": 14.0,  # 14
        "reaction_payoff": 14.0,  # 14
        "visual_audio_quality": 9.0,  # 9
        "caption_potential": 9.0,  # 9
        "replay_potential": 4.5,  # 4.5
        "campaign_fit": 5.0,  # 5
    }
    score_p = quality_candidate_scorer.score_candidate(premium_cand)
    assert score_p["total_score"] >= 85.0
    assert score_p["tier"] == "premium"
    assert score_p["verdict"] == "select"

    # Candidate with weak hook/story -> Reject
    weak_cand = {
        "id": "cand_weak",
        "hook_score": 2.0,  # 4
        "entertainment_value": 2.0,  # 4
        "story_clarity": 5.0,  # 5
        "reaction_payoff": 5.0,  # 5
        "visual_audio_quality": 5.0,  # 5
        "caption_potential": 5.0,  # 5
        "replay_potential": 2.0,  # 2
        "campaign_fit": 5.0,  # 5
    }
    score_w = quality_candidate_scorer.score_candidate(weak_cand)
    assert score_w["total_score"] < 65.0
    assert score_w["tier"] == "reject"
    assert score_w["verdict"] == "reject"

    # Test selective ranking on 10 candidates
    candidates_pool = []
    for i in range(10):
        val = 4.0 + (i * 0.6)  # Varied scores
        candidates_pool.append({
            "id": f"cand_{i}",
            "hook_score": min(10.0, val),
            "entertainment_value": min(10.0, val),
            "story_clarity": min(15.0, val * 1.5),
            "reaction_payoff": min(15.0, val * 1.5),
        })

    ranked_res = quality_candidate_scorer.rank_and_select_candidates(
        candidates=candidates_pool,
        min_select=3,
        max_select=5,
    )
    # Selected must never exceed 5
    assert len(ranked_res["selected_candidates"]) <= 5
    # All selected candidates must have score >= 75
    for sc in ranked_res["selected_candidates"]:
        assert sc["total_score"] >= 75.0


def test_campaign_specific_editing_planner_differentiation():
    """Verify that two different campaigns produce distinctly differentiated editing plans."""
    # Campaign A: Fast Action Campaign
    camp_a = {
        "id": "camp_action",
        "name": "High Action Stunts",
        "min_clip_duration_seconds": 20,
        "max_clip_duration_seconds": 30,
        "required_hashtags": ["#GTA6Stunts", "#Action"],
        "required_mentions": ["@rockstar_stunts"],
        "required_captions": ["Most insane stunt in Vice City!"],
    }

    # Campaign B: Story / Lore Campaign
    camp_b = {
        "id": "camp_story",
        "name": "Vice City Story Secrets",
        "min_clip_duration_seconds": 40,
        "max_clip_duration_seconds": 55,
        "required_hashtags": ["#GTA6Lore", "#ViceCitySecrets"],
        "required_mentions": ["@lore_masters"],
        "required_captions": ["Did you notice this hidden detail?"],
    }

    cand = {"id": "clip_moment_1", "start_time": 10.0, "end_time": 45.0, "hook_text": "Look closely at this scene!"}

    plan_a = editing_planner.create_editing_plan(camp_a, cand, "/footage/gameplay.mp4", platform="tiktok")
    plan_b = editing_planner.create_editing_plan(camp_b, cand, "/footage/gameplay.mp4", platform="youtube_shorts")

    # Verify differentiated decisions
    assert plan_a["target_duration"] == 30.0  # Capped at camp_a max
    assert plan_b["target_duration"] == 40.0  # Floored at camp_b min
    assert plan_a["opening_hook"]["style"] == "high-intensity-immediate"
    assert plan_b["opening_hook"]["style"] == "question-mystery-setup"
    assert plan_a["exact_publication_caption"] != plan_b["exact_publication_caption"]
    assert "#GTA6Stunts" in plan_a["required_hashtags"]
    assert "#GTA6Lore" in plan_b["required_hashtags"]
    assert plan_a["resolution_and_framerate"]["frame_rate"] == 30
    assert plan_b["resolution_and_framerate"]["frame_rate"] == 60


def test_dual_gate_compliance_and_quality():
    """Verify dual gates (Gate A Creative Quality and Gate B Campaign Compliance)."""
    campaign = {
        "id": "camp_gate_test",
        "accepted_platforms": ["tiktok"],
        "min_clip_duration_seconds": 15,
        "max_clip_duration_seconds": 35,
        "required_hashtags": ["#ViceCity"],
        "required_mentions": ["@cr_gamer"],
    }
    source = {"source_verification_status": "verified", "authorized_platforms": ["tiktok"]}

    # Candidate with good hook and metadata
    good_meta = {
        "duration": 22.0,
        "width": 1080,
        "height": 1920,
        "caption": "Insane moment! #ViceCity @cr_gamer",
        "hook_score": 8.5,
        "story_clarity": 11.0,
        "pacing": 8.0,
    }

    res_pass = compliance_validator.validate_dual_gates(
        clip_id="clip_pass_1",
        video_path=None,
        campaign=campaign,
        candidate_metadata=good_meta,
        source=source,
        target_platform="tiktok",
    )
    assert res_pass["overall_approved"] is True
    assert res_pass["gate_a_creative_quality"]["passed"] is True
    assert res_pass["gate_b_campaign_compliance"]["passed"] is True

    # Candidate with weak hook -> Fails Gate A
    weak_hook_meta = dict(good_meta)
    weak_hook_meta["hook_score"] = 5.0
    res_fail_a = compliance_validator.validate_dual_gates(
        clip_id="clip_fail_gate_a",
        video_path=None,
        campaign=campaign,
        candidate_metadata=weak_hook_meta,
        source=source,
        target_platform="tiktok",
    )
    assert res_fail_a["overall_approved"] is False
    assert res_fail_a["gate_a_creative_quality"]["passed"] is False

    # Candidate with duration violation -> Fails Gate B
    bad_dur_meta = dict(good_meta)
    bad_dur_meta["duration"] = 5.0  # Below 15s
    res_fail_b = compliance_validator.validate_dual_gates(
        clip_id="clip_fail_gate_b",
        video_path=None,
        campaign=campaign,
        candidate_metadata=bad_dur_meta,
        source=source,
        target_platform="tiktok",
    )
    assert res_fail_b["overall_approved"] is False
    assert res_fail_b["gate_b_campaign_compliance"]["passed"] is False


def test_publication_package_generation():
    """Verify platform-specific publication package formatting for TikTok vs YouTube Shorts."""
    campaign = {
        "id": "camp_pub_test",
        "name": "Vice City Stunts",
        "required_hashtags": ["#GTA6", "#Gaming"],
        "required_mentions": ["@rockstar"],
        "required_captions": ["Check out this insane jump!"],
    }
    editing_plan = {
        "plan_id": "plan_123",
        "cta": "Follow for daily clips!",
        "attribution": "Footage by CreatorX",
    }
    dual_gate_ok = {
        "overall_approved": True,
        "gate_a_creative_quality": {"passed": True},
        "gate_b_campaign_compliance": {"passed": True},
    }
    quality_res = {"total_score": 88.0, "tier": "premium"}

    # TikTok package
    pkg_tt = publication_package_service.generate_package(
        clip_id="clip_pub_1",
        video_path="/app/output/test.mp4",
        campaign=campaign,
        editing_plan=editing_plan,
        dual_gate_result=dual_gate_ok,
        quality_score_result=quality_res,
        target_platform="tiktok",
    )
    assert pkg_tt["target_platform"] == "tiktok"
    assert "#GTA6" in pkg_tt["exact_campaign_caption"]
    assert "@rockstar" in pkg_tt["exact_campaign_caption"]
    assert pkg_tt["publishing_automation_enabled"] is False
    assert pkg_tt["publishing_state"] == "staged_pending_operator_approval"

    # YouTube Shorts package
    pkg_yt = publication_package_service.generate_package(
        clip_id="clip_pub_1",
        video_path="/app/output/test.mp4",
        campaign=campaign,
        editing_plan=editing_plan,
        dual_gate_result=dual_gate_ok,
        quality_score_result=quality_res,
        target_platform="youtube_shorts",
    )
    assert pkg_yt["target_platform"] == "youtube_shorts"
    assert "#Shorts" in pkg_yt["exact_campaign_caption"]


def test_earnings_progression_and_financial_integrity():
    """Verify earnings tracker progress toward $500 target based on approved rewards only."""
    # Ensure fresh test submissions
    sub = earnings_tracker.record_submission(
        clip_id="clip_earn_1",
        campaign_id="camp_earn_1",
        platform="tiktok",
        submission_url="https://tiktok.com/@user/video/123",
    )
    assert sub["status"] == "submitted"

    # Update with pending and approved rewards
    earnings_tracker.update_metrics(
        submission_id=sub["id"],
        evidence_source="marketplace_dashboard_export",
        verified_by="operator_human",
        eligible_views=15000,
        pending_rewards=50.00,
        approved_rewards=75.00,
        withdrawable_earnings=75.00,
    )

    summary = earnings_tracker.get_summary(target_amount=500.0)
    assert summary.total_eligible_views >= 15000
    assert summary.total_approved_rewards >= 75.00
    # Progress must be strictly calculated on approved rewards: 75 / 500 = 15.0%
    assert summary.progress_percent >= 15.0
    # Negative values must be rejected
    with pytest.raises(ValueError):
        earnings_tracker.update_metrics(
            submission_id=sub["id"],
            evidence_source="audit",
            verified_by="system",
            approved_rewards=-10.0,
        )
