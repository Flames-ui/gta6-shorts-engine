"""Worker reliability: stale-job recovery, retry exhaustion and duplicate-execution prevention."""
from app import states
from app.db import connection, repo
from app.pipeline.orchestrator import run_job_task
from app.worker import recover_stale_jobs

from .conftest import requires_db

pytestmark = [requires_db]


def _active_job(status=states.ANALYZING, attempts=0):
    src = repo.create_source("owner-a", source_type="upload", status="ready", file_key="sources/x/y/original.mp4",
                             sha256="a" * 64, title="t", duration_seconds=60.0)
    job = repo.create_job("owner-a", str(src["id"]), clip_count=5, aspect_ratio="9:16",
                          caption_preset="bold_creator", min_clip_seconds=15, max_clip_seconds=90)
    job_id = str(job["id"])
    repo.transition_job(job_id, states.VALIDATING, worker_id="dead-worker")
    if status != states.VALIDATING:
        repo.transition_job(job_id, status, worker_id="dead-worker")
    with connection() as conn:
        conn.execute("UPDATE editing_jobs SET heartbeat_at = now() - interval '2 hours', attempt_count = %s "
                     "WHERE id = %s", (attempts, job_id))
    return job_id


def test_job_whose_worker_died_is_requeued(clean_db, fake_queue):
    job_id = _active_job()
    assert recover_stale_jobs() >= 1
    job = repo.get_job(job_id)
    assert job["status"] == states.QUEUED
    assert job["attempt_count"] == 1
    assert any(j == job_id for j, _ in fake_queue.enqueued)


def test_job_is_failed_once_retries_are_exhausted(clean_db, fake_queue):
    job_id = _active_job(attempts=2)  # MAX_JOB_ATTEMPTS is 3 in tests
    recover_stale_jobs()
    job = repo.get_job(job_id)
    assert job["status"] == states.FAILED
    assert job["error_code"] == "worker_lost"


def test_fresh_jobs_are_not_touched(clean_db, fake_queue):
    src = repo.create_source("owner-a", source_type="upload", status="ready", file_key="sources/x/z/original.mp4",
                             sha256="b" * 64, title="t", duration_seconds=60.0)
    job = repo.create_job("owner-a", str(src["id"]), clip_count=5, aspect_ratio="9:16",
                          caption_preset="bold_creator", min_clip_seconds=15, max_clip_seconds=90)
    repo.transition_job(str(job["id"]), states.VALIDATING, worker_id="live-worker")
    recover_stale_jobs()
    assert repo.get_job(str(job["id"]))["status"] == states.VALIDATING


def test_duplicate_delivery_does_not_run_a_job_twice(clean_db, fake_queue, monkeypatch):
    src = repo.create_source("owner-a", source_type="upload", status="ready", file_key="sources/x/q/original.mp4",
                             sha256="c" * 64, title="t", duration_seconds=60.0)
    job = repo.create_job("owner-a", str(src["id"]), clip_count=5, aspect_ratio="9:16",
                          caption_preset="bold_creator", min_clip_seconds=15, max_clip_seconds=90)
    job_id = str(job["id"])
    fake_queue.locks[job_id] = "someone-else"  # another worker holds the lock
    ran = []
    import app.pipeline.orchestrator as orch

    monkeypatch.setattr(orch, "run_job", lambda *a, **k: ran.append(a) or "completed")
    run_job_task(job_id)
    assert ran == []
    assert repo.get_job(job_id)["status"] == states.QUEUED


def test_lock_is_released_after_the_job_finishes(clean_db, fake_queue, monkeypatch):
    import app.pipeline.orchestrator as orch

    monkeypatch.setattr(orch, "run_job", lambda *a, **k: "failed")
    run_job_task("11111111-1111-1111-1111-111111111111")
    assert "11111111-1111-1111-1111-111111111111" not in fake_queue.locks


def test_long_queued_jobs_are_re_enqueued_at_most_once_per_hour(clean_db, fake_queue):
    src = repo.create_source("owner-a", source_type="upload", status="ready", file_key="sources/x/w/original.mp4",
                             sha256="e" * 64, title="t", duration_seconds=60.0)
    job = repo.create_job("owner-a", str(src["id"]), clip_count=5, aspect_ratio="9:16",
                          caption_preset="bold_creator", min_clip_seconds=15, max_clip_seconds=90)
    job_id = str(job["id"])
    with connection() as conn:
        conn.execute("UPDATE editing_jobs SET queued_at = now() - interval '2 hours' WHERE id = %s", (job_id,))
    recover_stale_jobs()
    recover_stale_jobs()
    assert [j for j, _ in fake_queue.enqueued].count(job_id) == 1
