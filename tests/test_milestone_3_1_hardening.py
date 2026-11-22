"""Focused Milestone 3.1 regression tests for production-safety boundaries."""
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings
from app.services.supabase_client import db
from app.services.source_authorization import source_authorization_service
from app.services.google_drive_assets import google_drive_asset_service
from app.services.quality_candidate_scorer import quality_candidate_scorer
from app.services.compliance_validator import compliance_validator
from app.services.campaign_pipeline import campaign_pipeline_service
from app.services.earnings_tracker import earnings_tracker
from app.services.marketplaces.content_rewards_operator import content_rewards_operator


def evidence_candidate(candidate_id, start=1, score=10):
    factors = ("hook_strength", "entertainment_value", "story_clarity", "reaction_payoff",
               "visual_audio_quality", "caption_potential", "replay_potential")
    return {
        "candidate_id": candidate_id,
        "source_id": "source-evidence-test",
        "start_time": start,
        "end_time": start + 25,
        "analysis_evidence": {
            factor: {"score": score, "evidence_reference": f"frame:{factor}",
                     "source_id": "source-evidence-test", "observed_at": "2026-11-22T00:00:00Z"}
            for factor in factors
        },
    }


def eligible_campaign(campaign_id):
    return {"id": campaign_id, "name": "Test campaign", "url": "https://example.org/campaign",
            "status": "approved", "operator_approved": True, "accepted_platforms": ["tiktok"],
            "eligible_games": ["gta6"], "min_clip_duration_seconds": 15,
            "max_clip_duration_seconds": 60, "required_hashtags": [], "required_mentions": []}


def verified_source(campaign_id):
    return {"id": "source-test", "campaign_id": campaign_id, "source_verification_status": "verified",
            "permission_evidence": {"permission_source": "license-ref-2026", "rights_holder": "Creator",
                                    "verified_by": "operator-test", "verified_at": "2026-11-22T00:00:00Z"},
            "original_creator": "Creator", "verified_by": "operator-test",
            "verified_at": "2026-11-22T00:00:00Z", "allowed_derivative_uses": ["short_form_clipping"],
            "authorized_platforms": ["tiktok"]}


def test_operator_override_and_unknown_bypasses_do_not_bypass_pipeline():
    result = campaign_pipeline_service.run_pipeline_for_campaign(
        campaign_id=f"missing-{uuid4().hex}", operator_override=True,
        bypass_source_verification=True, bypass_asset_verification=True)
    assert result["status"] == "failed"
    assert result["blockers"]
    assert not result.get("produced_clips")


def test_source_authorization_fails_closed_for_missing_expired_and_wrong_campaign():
    cid = f"campaign-{uuid4().hex}"
    db.upsert_campaign(eligible_campaign(cid))
    source = {"id": f"expired-{uuid4().hex}", "campaign_id": cid, "source_url": "https://source.example/video",
              "original_creator": "Creator", "source_type": "licensed", "permission_evidence": "license-ref-2026",
              "allowed_derivative_uses": ["short_form_clipping"], "authorized_platforms": ["tiktok"],
              "permission_expiry": "2020-01-01T00:00:00Z", "source_verification_status": "verified",
              "verified_by": "operator-test", "verified_at": "2026-11-22T00:00:00Z"}
    db.create_campaign_source(source)
    assert source_authorization_service.verify_source("missing-source", cid)[0] is False
    allowed, reason, _ = source_authorization_service.verify_source(source["id"], cid)
    assert not allowed and "expired" in reason.lower()
    assert source_authorization_service.verify_source(source["id"], "another-campaign")[0] is False


def test_drive_assets_require_explicit_rights_and_operator_identity():
    record = google_drive_asset_service.register_downloaded_asset(
        campaign_id=f"campaign-{uuid4().hex}", file_name="unverified.png",
        content_bytes=b"test image bytes", mime_type="image/png", permission_evidence=None)
    assert record["is_authorized"] is False
    assert record["permission_status"] == "unverified"
    ok, _, _ = google_drive_asset_service.operator_verify_asset_permission(
        asset_id=record["asset_id"], rights_holder="Rights holder",
        permission_source="Agreement REF-2026", permitted_derivative_uses=["short_form_video"],
        authorized_platforms=["tiktok"], verified_by="operator-test")
    assert ok is True
    denied, reason, _ = google_drive_asset_service.operator_verify_asset_permission(
        asset_id=record["asset_id"], rights_holder="Rights holder",
        permission_source="Agreement REF-2026", permitted_derivative_uses=["short_form_video"],
        authorized_platforms=["tiktok"])
    assert denied is False and "operator" in reason.lower()


def test_candidate_scores_are_evidence_backed_and_total_100_points():
    camp = eligible_campaign(f"campaign-{uuid4().hex}")
    high = quality_candidate_scorer.score_candidate(evidence_candidate("high"), camp, persist_evaluation=False)
    assert high["evidence_backed"] is True
    assert high["quality_score"] == 100.0
    assert high["verdict"] == "select"
    forged = {"candidate_id": "forged", "hook_score": 10, "entertainment_value": 10,
              "story_clarity": 10, "reaction_payoff": 10, "visual_audio_quality": 10,
              "caption_potential": 10, "replay_potential": 5}
    result = quality_candidate_scorer.score_candidate(forged, camp, persist_evaluation=False)
    assert result["evidence_backed"] is False
    assert result["verdict"] == "reject"


def test_overlapping_candidates_do_not_both_get_selected():
    camp = eligible_campaign(f"campaign-{uuid4().hex}")
    candidates = [evidence_candidate("one", 10), evidence_candidate("overlap", 20), evidence_candidate("separate", 100)]
    result = quality_candidate_scorer.rank_and_select_candidates(
        candidates, campaign_context=camp, min_select=1, max_select=3, persist_evaluations=False)
    ids = [x["candidate_id"] for x in result["selected_candidates"]]
    assert "one" in ids and "overlap" not in ids and "separate" in ids


def test_media_inspection_reads_real_video_and_samples_frames():
    path = "/tmp/test_assets/valid_vertical_25s.mp4"
    assert os.path.exists(path)
    report = compliance_validator.inspect_video_media(path)
    assert report["is_valid_format"] is True
    assert report["width"] == 1080 and report["height"] == 1920
    assert report["duration"] >= 24
    assert len(report["frame_samples"]) == 3


def test_quality_and_compliance_gates_are_independent_and_fail_closed():
    video = "/tmp/test_assets/valid_vertical_25s.mp4"
    camp = eligible_campaign(f"campaign-{uuid4().hex}")
    quality = quality_candidate_scorer.score_candidate(evidence_candidate("gate-test"), camp, persist_evaluation=False)
    good_source = verified_source(camp["id"])
    # Valid media/rights do not override a missing evidence-backed score.
    no_score = compliance_validator.validate_dual_gates("clip-no-score", video, camp, good_source)
    assert not no_score["gate_a_creative_quality"]["passed"]
    # A strong score cannot override missing explicit rights evidence.
    no_rights = compliance_validator.validate_dual_gates(
        "clip-no-rights", video, camp, None, candidate_quality_score=quality["quality_score"], quality_evaluation=quality,
        candidate_metadata={"caption": "GTA 6", "hook_score": 10})
    assert no_rights["gate_a_creative_quality"]["passed"]
    assert not no_rights["gate_b_campaign_compliance"]["passed"]


def test_pipeline_fails_closed_for_unapproved_campaign():
    campaign = eligible_campaign(f"campaign-{uuid4().hex}")
    campaign.update(status="discovered", operator_approved=False)
    db.upsert_campaign(campaign)
    result = campaign_pipeline_service.run_pipeline_for_campaign(campaign_id=campaign["id"])
    assert result["status"] in ("ineligible", "awaiting_operator_approval")
    assert result["blockers"]


def test_marketplace_application_requires_operator_terms_approval():
    with pytest.raises(ValueError):
        content_rewards_operator.apply_to_campaign(
            campaign_id="campaign-test", campaign_url="https://example.org/campaign",
            operator_approved_terms=False)


def test_publication_stays_disabled_and_unverified_package_is_rejected(client, auth_headers):
    assert settings.PUBLISHING_AUTOMATION_ENABLED is False
    response = client.post("/api/v1/publishing/publish-now",
        json={"clip_id": "clip-test", "platform": "tiktok"}, headers=auth_headers)
    assert response.status_code in (200, 400, 403, 503)
    if response.status_code == 200:
        assert "disabled" in str(response.json()).lower()
    campaign = eligible_campaign(f"campaign-{uuid4().hex}")
    db.upsert_campaign(campaign)
    rejected = client.post("/api/v1/marketplace/clips/clip-unverified/publication-package",
        json={"campaign_id": campaign["id"], "video_path": "/tmp/test_assets/valid_vertical_25s.mp4",
              "candidate_metadata": {"hook_score": 10, "start_time": 0, "end_time": 25},
              "editing_plan": {}, "target_platform": "tiktok"}, headers=auth_headers)
    assert rejected.status_code == 400
    assert "rejected" in str(rejected.json()).lower()


def test_only_unique_verified_marketplace_rewards_count_in_summary():
    campaign = eligible_campaign(f"campaign-{uuid4().hex}")
    db.upsert_campaign(campaign)
    submission_id = f"submission-{uuid4().hex}"
    db.create_clip_submission({"id": submission_id, "clip_id": "clip-reward-test",
        "campaign_id": campaign["id"], "platform": "tiktok", "status": "submitted"})
    reward = earnings_tracker.record_marketplace_reward(submission_id, 75.5,
        marketplace_reference=f"reward-{uuid4().hex}", operator_id="operator-finance")
    assert reward["success"] is True
    assert earnings_tracker.get_summary().total_approved_rewards >= 75.5
    duplicate = earnings_tracker.record_marketplace_reward(submission_id, 50,
        marketplace_reference=reward["marketplace_reward_ref"], operator_id="operator-finance")
    assert duplicate["success"] is False


def test_audit_log_captures_operator_reason_and_evidence():
    entity = f"campaign-{uuid4().hex}"
    audit = db.record_operator_audit(operator_id="operator-test", action="approve_campaign",
        entity_type="campaign", entity_id=entity, reason="Terms reviewed",
        evidence_reference="agreement:ref-1", previous_value={"status": "discovered"},
        new_value={"status": "approved"})
    assert audit["id"] and audit["operator_id"] == "operator-test"
    logs = db.list_operator_audits(entity_id=entity)
    assert any(x["evidence_reference"] == "agreement:ref-1" for x in logs)


def test_dashboard_uses_session_cookie_and_displays_recorded_not_fabricated_gates():
    root = Path(__file__).resolve().parents[1]
    js = (root / "app/static/app.js").read_text()
    assert "sessionStorage.setItem" not in js and "localStorage.setItem" not in js
    assert "apiFetch(`/api/v1/campaigns/inspections/" in js
    assert "No recorded inspection exists" in js
    assert "DUAL GATES PASSED (APPROVED)" not in js
    for migration in (root / "migrations/006_harden_evaluations_and_inspections.sql",
                      root / "supabase/migrations/20261122040000_harden_evaluations_and_inspections.sql"):
        text = migration.read_text()
        assert "REVOKE ALL ON operator_audit_logs FROM PUBLIC, anon, authenticated" in text
        assert "GRANT ALL ON operator_audit_logs TO service_role" in text


def test_dashboard_login_sets_cookie_and_csrf_protects_mutations(client):
    response = client.post("/api/v1/auth/login", json={"api_key": settings.ENGINE_API_KEY})
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    assert "session_id" in response.cookies
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True
    no_token = client.post("/api/v1/campaigns/rewards/confirm", json={"submission_id": "missing",
        "reward_amount": 10, "marketplace_ref": "ref-no-csrf"})
    assert no_token.status_code == 403
    with_token = client.post("/api/v1/campaigns/rewards/confirm", json={"submission_id": "missing",
        "reward_amount": 10, "marketplace_ref": "ref-with-csrf"}, headers={"X-CSRF-Token": token})
    assert with_token.status_code in (400, 404)
    client.cookies.clear()
