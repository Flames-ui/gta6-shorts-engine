"""Repository behaviour against a real PostgreSQL database."""
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from app import states
from app.db import connection, repo
from app.errors import Conflict

from .conftest import requires_db

pytestmark = [requires_db]


def _source(owner="owner-a", **over):
    fields = dict(source_type="upload", status="ready", title="Clip", file_size_bytes=10, sha256=uuid.uuid4().hex + uuid.uuid4().hex,
                  file_key=f"sources/x/{uuid.uuid4().hex}/original.mp4", duration_seconds=120.0,
                  width=1280, height=720, has_audio=True, container_format="mov,mp4")
    fields.update(over)
    return repo.create_source(owner, **fields)


def _job(owner="owner-a", source=None, **over):
    source = source or _source(owner)
    fields = dict(clip_count=5, aspect_ratio="9:16", caption_preset="bold_creator", language=None,
                  min_clip_seconds=15, max_clip_seconds=90, requested_config={"clip_count": 5})
    fields.update(over)
    return repo.create_job(owner, str(source["id"]), **fields)


def test_job_starts_queued_with_a_created_event(clean_db):
    job = _job()
    assert job["status"] == states.QUEUED
    events = repo.list_events(str(job["id"]))
    assert events[0]["event_type"] == "created"


def test_transitions_follow_the_state_machine(clean_db):
    job_id = str(_job()["id"])
    repo.transition_job(job_id, states.VALIDATING, stage="validating", message="go")
    with pytest.raises(Conflict) as exc:
        repo.transition_job(job_id, states.COMPLETED)  # skipping stages is illegal
    assert exc.value.code == "illegal_transition"
    assert repo.get_job(job_id)["status"] == states.VALIDATING


def test_transition_is_compare_and_set(clean_db):
    job_id = str(_job()["id"])
    repo.transition_job(job_id, states.VALIDATING)
    # Simulate another worker that read the job as 'queued' and tries to move it now.
    with connection() as conn:
        conn.execute("UPDATE editing_jobs SET status = 'cancelled' WHERE id = %s", (job_id,))
    with pytest.raises(Conflict):
        repo.transition_job(job_id, states.RETRIEVING)
    assert repo.get_job(job_id)["status"] == states.CANCELLED


def test_every_transition_writes_an_event_with_from_and_to(clean_db):
    job_id = str(_job()["id"])
    repo.transition_job(job_id, states.VALIDATING)
    repo.transition_job(job_id, states.ANALYZING, stage="analyzing")
    moves = [(e["from_status"], e["to_status"]) for e in repo.list_events(job_id) if e["event_type"] == "status_changed"]
    assert moves == [("queued", "validating"), ("validating", "analyzing")]


def test_retry_from_active_increments_attempts_and_failed_resets_error(clean_db):
    job_id = str(_job()["id"])
    repo.transition_job(job_id, states.VALIDATING)
    repo.transition_job(job_id, states.QUEUED)
    job = repo.get_job(job_id)
    assert job["attempt_count"] == 1
    repo.transition_job(job_id, states.VALIDATING)
    repo.transition_job(job_id, states.FAILED, error_code="boom", error_message="Failed for test")
    assert repo.get_job(job_id)["error_code"] == "boom"
    repo.transition_job(job_id, states.QUEUED, message="manual retry")
    job = repo.get_job(job_id)
    assert job["status"] == states.QUEUED and job["error_code"] is None


def test_cancel_request_is_visible_to_the_worker_and_owner_only(clean_db):
    job = _job("owner-a")
    job_id = str(job["id"])
    assert repo.get_job(job_id, owner_id="owner-b") is None
    repo.request_cancel(job_id, "owner-a")
    assert repo.is_cancel_requested(job_id) is True


def test_idempotency_keys_are_scoped_per_owner(clean_db):
    a = _job("owner-a", idempotency_key="key-123456")
    b = _job("owner-b", idempotency_key="key-123456")
    assert a["id"] != b["id"]
    assert str(repo.find_job_by_idempotency("owner-a", "key-123456")["id"]) == str(a["id"])


def test_duplicate_idempotency_key_for_same_owner_is_rejected_by_the_database(clean_db):
    _job("owner-a", idempotency_key="same-key-000")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _job("owner-a", idempotency_key="same-key-000")


def test_one_live_source_per_owner_per_youtube_video(clean_db):
    _source("owner-a", source_type="youtube", status="pending_retrieval", youtube_video_id="dQw4w9WgXcQ",
            file_key=None, sha256=None)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _source("owner-a", source_type="youtube", status="pending_retrieval", youtube_video_id="dQw4w9WgXcQ",
                file_key=None, sha256=None)
    # A failed source does not block re-adding the video later.
    repo.update_source(str(repo.find_live_youtube_source("owner-a", "dQw4w9WgXcQ")["id"]), status="failed",
                       failure_code="private", failure_message="Private")
    assert repo.find_live_youtube_source("owner-a", "dQw4w9WgXcQ") is None
    _source("owner-a", source_type="youtube", status="pending_retrieval", youtube_video_id="dQw4w9WgXcQ",
            file_key=None, sha256=None)


def test_same_file_is_not_stored_twice_for_one_owner_but_can_be_for_another(clean_db):
    digest = "f" * 64
    _source("owner-a", sha256=digest)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _source("owner-a", sha256=digest)
    _source("owner-b", sha256=digest)


def test_source_check_constraints_reject_ready_without_file(clean_db):
    with pytest.raises(psycopg.errors.CheckViolation):
        _source("owner-a", status="ready", file_key=None)


def test_highlights_are_replaced_atomically_and_keep_rejection_reasons(clean_db):
    job_id = str(_job()["id"])
    row = {"candidate_rank": 1, "start_seconds": 1.0, "end_seconds": 21.0, "duration_seconds": 20.0,
           "suggested_title": "T", "description": "d", "transcript": "t", "selection_rationale": "r",
           "engagement_score": 80, "content_category": "x", "confidence": 0.9, "selected": False,
           "rejection_reason": "not_selected"}
    repo.replace_highlights(job_id, [row])
    repo.replace_highlights(job_id, [dict(row, candidate_rank=1, selected=True, rejection_reason=None)])
    highlights = repo.list_highlights(job_id)
    assert len(highlights) == 1 and highlights[0]["selected"] is True


def test_clip_creation_is_idempotent_per_job_and_number(clean_db):
    """A retried job re-creates its clip rows: the same (job, number) updates in place, never duplicates."""
    job_id = str(_job()["id"])
    base = dict(job_id=job_id, owner_id="owner-a", highlight_id=None, title="T", description=None,
                start_seconds=0.0, end_seconds=20.0, duration_seconds=20.0,
                render_status="pending", validation_status="pending")
    first = repo.create_clip(clip_number=1, **base)
    second = repo.create_clip(clip_number=1, **dict(base, title="Retitled"))
    assert first["id"] == second["id"]
    clips = repo.list_clips(job_id)
    assert len(clips) == 1 and clips[0]["title"] == "Retitled"


def test_clip_must_be_within_the_job_clip_limit(clean_db):
    job_id = str(_job()["id"])
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.create_clip(job_id=job_id, owner_id="owner-a", highlight_id=None, clip_number=6, title="T",
                         description=None, start_seconds=0.0, end_seconds=20.0, duration_seconds=20.0,
                         render_status="pending", validation_status="pending")


def test_list_jobs_paginates_and_filters(clean_db):
    for _ in range(3):
        _job("owner-a")
    other = _job("owner-b")
    page = repo.list_jobs("owner-a", status=None, limit=2, offset=0)
    assert page["total"] == 3 and len(page["items"]) == 2
    assert repo.list_jobs("owner-a", status=states.FAILED, limit=10, offset=0)["total"] == 0
    assert all(str(j["id"]) != str(other["id"]) for j in repo.list_jobs("owner-a", status=None, limit=10,
                                                                        offset=0)["items"])


def test_usage_is_summed_per_owner(clean_db):
    job_id = str(_job("owner-a")["id"])
    repo.record_usage("owner-a", job_id, "render_seconds", 2.5)
    repo.record_usage("owner-a", job_id, "render_seconds", 1.5)
    repo.record_usage("owner-b", job_id, "render_seconds", 100.0)
    summary = repo.usage_summary("owner-a", datetime.now(timezone.utc) - timedelta(days=1))
    assert summary["render_seconds"] == pytest.approx(4.0)


def test_stale_heartbeats_are_detected_and_fresh_workers_listed(clean_db):
    job_id = str(_job()["id"])
    repo.transition_job(job_id, states.VALIDATING, worker_id="w1")
    with connection() as conn:
        conn.execute("UPDATE editing_jobs SET heartbeat_at = now() - interval '2 hours' WHERE id = %s", (job_id,))
    stale = repo.jobs_with_stale_heartbeat(300)
    assert [str(j["id"]) for j in stale] == [job_id]
    repo.heartbeat("w1", hostname="h", pid=1, status="busy", current_job_id=job_id, version="t")
    assert any(w["worker_id"] == "w1" and w["is_fresh"] for w in repo.list_workers(300))
