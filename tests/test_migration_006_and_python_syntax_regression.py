"""
Regression test for Python f-string syntax in supabase_client.py
and foreign key alignment to public.generated_clips in migration 006.
"""
import ast
import os
import re
from uuid import uuid4
import pytest
from app.services.supabase_client import db


def test_no_nested_quote_syntax_in_any_python_files():
    """Verify all Python files parse cleanly with ast and contain no unescaped same-quote f-strings."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for root, _, files in os.walk(os.path.join(repo_root, "app")):
        for f in files:
            if f.endswith(".py"):
                fpath = os.path.join(root, f)
                with open(fpath, "r", encoding="utf-8") as py_file:
                    content = py_file.read()
                # Verify file parses cleanly via ast
                ast.parse(content, filename=fpath)
                lines = content.splitlines()
                for i, line in enumerate(lines, 1):
                    # Check for single-quote f-string with nested single quotes in expression
                    if "f'" in line and "{', '" in line:
                        pytest.fail(f"Found forbidden nested single quotes in f-string at {fpath}:{i}: {line}")


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

    # 1. Test update_campaign (was line 2192)
    updated_camp = db.update_campaign(camp_id, {"name": "Updated Campaign Name", "status": "eligible"})
    assert updated_camp["name"] == "Updated Campaign Name"
    assert updated_camp["status"] == "eligible"

    # 2. Test update_campaign_source (was line 2304)
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

    # 3. Test update_clip_submission (was line 2513)
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
