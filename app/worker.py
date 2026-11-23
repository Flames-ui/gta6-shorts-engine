"""Background worker process: `python -m app.worker`.

* Sends heartbeats so operators can see live workers.
* On start, and every minute, recovers jobs whose worker died: stale active jobs
  go back to the queue (bounded by MAX_JOB_ATTEMPTS) and never stay stuck.
* Runs RQ in the foreground; SIGTERM finishes the current stage and stops cleanly.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading

from . import states
from .config import settings, validate_settings
from .db import repo
from .logging_config import configure_logging
from .pipeline.orchestrator import VERSION, new_worker_id
from .queue import get_queue

logger = logging.getLogger("studio.worker")
RECOVERY_INTERVAL_SECONDS = 60


def recover_stale_jobs() -> int:
    queue = get_queue()
    recovered = 0
    for job in repo.jobs_with_stale_heartbeat(settings.STALE_HEARTBEAT_SECONDS):
        job_id = str(job["id"])
        attempts = int(job.get("attempt_count") or 0)
        queue.force_release_lock(job_id)
        if attempts + 1 < settings.MAX_JOB_ATTEMPTS:
            repo.transition_job(job_id, states.QUEUED, message="Worker lost; job recovered and re-queued",
                                details={"reason": "stale_heartbeat"})
            queue.enqueue(job_id)
            logger.warning("recovered stale job", extra={"job_id": job_id, "event": "job_recovered",
                                                        "previous_status": job["status"], "attempt": attempts + 1})
        else:
            repo.transition_job(job_id, states.FAILED, error_code="worker_lost",
                                error_message="The worker stopped while processing and the retry limit was reached.",
                                message="Worker lost; retry limit reached")
            logger.error("stale job failed after retries", extra={"job_id": job_id, "event": "job_failed"})
        recovered += 1
    for job in repo.queued_older_than(600):
        job_id = str(job["id"])
        # At most one safety re-enqueue per job per hour, so a backlog never multiplies queue entries.
        if queue.redis.set(f"requeue:{job_id}", "1", nx=True, ex=3600):
            queue.enqueue(job_id)
            recovered += 1
    return recovered


def _heartbeat_loop(worker_id: str, state: dict, stop: threading.Event) -> None:
    while not stop.wait(settings.HEARTBEAT_INTERVAL_SECONDS):
        try:
            repo.heartbeat(worker_id, hostname=socket.gethostname(), pid=os.getpid(),
                           status="busy" if state.get("job") else "idle",
                           current_job_id=state.get("job"), version=VERSION)
        except Exception as exc:
            logger.warning("worker heartbeat failed", extra={"error_type": type(exc).__name__})


def _recovery_loop(stop: threading.Event) -> None:
    while not stop.wait(RECOVERY_INTERVAL_SECONDS):
        try:
            recover_stale_jobs()
        except Exception as exc:
            logger.warning("recovery pass failed", extra={"error_type": type(exc).__name__})


def main() -> int:
    configure_logging(settings.LOG_LEVEL)
    try:
        validate_settings()
    except Exception as exc:
        logger.critical("worker configuration is invalid: %s", exc)
        return 2
    from rq import Worker

    worker_id = os.environ.get("WORKER_ID") or new_worker_id()
    os.environ["WORKER_ID"] = worker_id
    queue = get_queue()
    if not queue.ping():
        logger.critical("cannot reach Redis; worker not started")
        return 1
    os.makedirs(settings.TEMP_DIR, exist_ok=True)

    state: dict = {"job": None}
    stop = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(worker_id, state, stop), daemon=True).start()
    threading.Thread(target=_recovery_loop, args=(stop,), daemon=True).start()
    try:
        recovered = recover_stale_jobs()
        logger.info("worker started", extra={"event": "worker_started", "worker_id": worker_id,
                                             "queue": settings.QUEUE_NAME, "recovered_on_start": recovered})
    except Exception as exc:
        logger.warning("startup recovery skipped: %s", type(exc).__name__)

    def _terminate(signum, _frame):
        logger.info("termination signal received; finishing current work", extra={"signal": signum})
        stop.set()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    worker = Worker([settings.QUEUE_NAME], connection=queue.redis, name=worker_id)
    try:
        worker.work(with_scheduler=False)
    except SystemExit:
        pass
    finally:
        stop.set()
        try:
            repo.heartbeat(worker_id, hostname=socket.gethostname(), pid=os.getpid(), status="stopping",
                           current_job_id=None, version=VERSION)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
