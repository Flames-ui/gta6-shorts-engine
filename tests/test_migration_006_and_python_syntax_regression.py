"""
Regression test for Python f-string syntax in supabase_client.py,
compliance_validator.py, and foreign key alignment to public.generated_clips in migration 006.
"""
import ast
import os
import re
from uuid import uuid4
import pytest
from app.services.supabase_client import db
from app.services.compliance_validator import compliance_validator, ComplianceValidator


def test_no_nested_quote_syntax_in_any_python_files():
    """Verify all Python files parse cleanly and contain no Python 3.11-incompatible f-strings (nested same-quotes or backslashes)."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    checked_files = 0
    for root, _, files in os.walk(repo_root):
        if ".git" in root or "__pycache__" in root or ".pytest_cache" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                fpath = os.path.join(root, f)
                relpath = os.path.relpath(fpath, repo_root)
                with open(fpath, "r", encoding="utf-8") as py_file:
                    source = py_file.read()
                
                # Verify file parses cleanly via ast
                tree = ast.parse(source, filename=fpath)
                checked_files += 1

                for node in ast.walk(tree):
                    if isinstance(node, ast.JoinedStr):
                        full_seg = ast.get_source_segment(source, node)
                        if not full_seg:
                            continue
                        s = full_seg.lstrip("fFrRbBuU")
                        if s.startswith('"""'):
                            delimiter = '"""'
                        elif s.startswith("'''"):
                            delimiter = "'''"
                        elif s.startswith('"'):
                            delimiter = '"'
                        elif s.startswith("'"):
                            delimiter = "'"
                        else:
                            continue

                        for part in node.values:
                            if isinstance(part, ast.FormattedValue):
                                expr_seg = ast.get_source_segment(source, part.value)
                                if not expr_seg:
                                    continue
                                if delimiter in expr_seg:
                                    pytest.fail(
                                        f"Forbidden nested delimiter {delimiter!r} in f-string expression at {relpath}:{part.lineno}: "
                                        f"{expr_seg!r} inside {full_seg!r}"
                                    )
                                if "\\\\" in expr_seg:
                                    pytest.fail(
                                        f"Forbidden backslash in f-string expression at {relpath}:{part.lineno}: {expr_seg!r}"
                                    )
    assert checked_files > 30, f"Expected to check more than 30 python files, checked {checked_files}"


def test_compliance_validator_duplicate_detection_regression():
    """Verify compliance validator executes duplicate video path and timing checks without syntax error or runtime failure."""
    validator = ComplianceValidator()
    
    campaign = {
        "id": "camp_test_dup",
        "accepted_platforms": ["tiktok"],
        "min_duration": 10,
        "max_duration": 60,
    }
    source = {
        "id": "src_test_dup",
        "campaign_id": "camp_test_dup",
        "status": "verified",
        "rights_verification": {"status": "verified"},
    }
    sample_vid = "/tmp/test_assets/valid_vertical_25s.mp4"
    existing_alpha = [
        {"id": "clip_existing_alpha", "video_path": sample_vid, "start_time": 10.0, "end_time": 35.0}
    ]
    
    # 1. Match on video_path (line 266 verification: "Duplicate video file path matching clip {ex_id}.")
    res_path_dup = validator.validate_clip_compliance(
        clip_id="clip_new_beta",
        video_path=sample_vid,
        campaign=campaign,
        candidate_metadata={"start_time": 50.0, "end_time": 75.0, "title": "Test Title", "caption": "Test Caption"},
        source=source,
        target_platform="tiktok",
        existing_clips=existing_alpha,
    )
    assert res_path_dup.checks["duplicate_check"] is False
    assert "Duplicate video file path matching clip clip_existing_alpha." in res_path_dup.failure_reasons

    # 2. Match on timing (line 273 verification: "Duplicate highlight timing matches existing clip {ex_id}.")
    existing_gamma = [
        {"id": "clip_existing_gamma", "video_path": "/tmp/test_assets/different.mp4", "start_time": 50.2, "end_time": 74.9}
    ]
    res_time_dup = validator.validate_clip_compliance(
        clip_id="clip_new_delta",
        video_path=sample_vid,
        campaign=campaign,
        candidate_metadata={"start_time": 50.0, "end_time": 75.0, "title": "Test Title", "caption": "Test Caption"},
        source=source,
        target_platform="tiktok",
        existing_clips=existing_gamma,
    )
    assert res_time_dup.checks["duplicate_check"] is False
    assert "Duplicate highlight timing matches existing clip clip_existing_gamma." in res_time_dup.failure_reasons

    # 3. Clean clip with different timing and different path passes duplicate check
    existing_distinct = [
        {"id": "clip_existing_omega", "video_path": "/tmp/test_assets/different.mp4", "start_time": 100.0, "end_time": 125.0}
    ]
    res_clean = validator.validate_clip_compliance(
        clip_id="clip_new_clean",
        video_path=sample_vid,
        campaign=campaign,
        candidate_metadata={"start_time": 10.0, "end_time": 35.0, "title": "Test Title", "caption": "Test Caption"},
        source=source,
        target_platform="tiktok",
        existing_clips=existing_distinct,
    )
    assert res_clean.checks["duplicate_check"] is True


def test_supabase_client_dynamic_updates_execute_cleanly():
    """Verify that update_campaign, update_campaign_source, and update_clip_submission execute cleanly."""
    camp_id = f"test-camp-{uuid4().hex[:8]}"
    camp_data = {
        "id": camp_id,
        "name": "Regression Test Campaign",
        "url": "https://example.com/camp",
        "raw_rules": "Test rules",
        "status": "draft",
    }
    db.create_campaign(camp_data)

    # 1. Test update_campaign
    updated_camp = db.update_campaign(camp_id, {"name": "Updated Campaign Name", "status": "eligible"})
    assert updated_camp["name"] == "Updated Campaign Name"
    assert updated_camp["status"] == "eligible"

    # 2. Test update_campaign_source
    source_id = f"test-source-{uuid4().hex[:8]}"
    source_data = {
        "id": source_id,
        "campaign_id": camp_id,
        "source_url": "https://youtube.com/watch?v=12345",
        "source_type": "youtube",
        "original_creator": "Creator A",
        "permission_evidence": {"type": "direct_license"},
        "status": "pending_verification",
    }
    db.create_campaign_source(source_data)
    updated_source = db.update_campaign_source(source_id, {"source_verification_status": "verified", "verified_by": "operator_admin"})
    assert updated_source["source_verification_status"] == "verified"

    # 3. Test update_clip_submission
    sub_id = f"test-sub-{uuid4().hex[:8]}"
    sub_data = {
        "id": sub_id,
        "clip_id": f"clip-{uuid4().hex[:8]}",
        "campaign_id": camp_id,
        "platform": "tiktok",
        "status": "submitted",
    }
    db.create_clip_submission(sub_data)
    updated_sub = db.update_clip_submission(sub_id, {"status": "approved", "approved_rewards": 50.0})
    assert updated_sub["status"] == "approved"
    assert float(updated_sub["approved_rewards"]) == 50.0


def test_migration_006_references_generated_clips_base_table():
    """Verify Migration 006 uses public.generated_clips for foreign keys and ALTER TABLE, not the clips view."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    migration_paths = [
        os.path.join(repo_root, "migrations", "006_harden_evaluations_and_inspections.sql"),
        os.path.join(repo_root, "supabase", "migrations", "20261122040000_harden_evaluations_and_inspections.sql"),
    ]
    for path in migration_paths:
        assert os.path.exists(path), f"Migration file {path} must exist"
        with open(path, "r", encoding="utf-8") as f:
            sql = f.read()

        # Must not reference clips(id) as a foreign key target
        assert not re.search(r"REFERENCES\s+clips\(", sql, re.IGNORECASE), f"{path} must not reference 'clips' view in FK"
        assert re.search(r"REFERENCES\s+public\.generated_clips\(", sql, re.IGNORECASE), f"{path} must reference public.generated_clips"

        # Must not ALTER TABLE clips
        assert not re.search(r"ALTER\s+TABLE\s+clips\s+", sql, re.IGNORECASE), f"{path} must not ALTER TABLE on clips view"
        assert re.search(r"ALTER\s+TABLE\s+public\.generated_clips\s+", sql, re.IGNORECASE), f"{path} must ALTER TABLE public.generated_clips"

        # Must ensure public.clips view is aligned
        assert "CREATE OR REPLACE VIEW public.clips" in sql, f"{path} must refresh the public.clips view"
