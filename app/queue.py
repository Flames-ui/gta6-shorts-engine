"""Redis + RQ job queue. API processes enqueue; separate worker processes execute.

Jobs are referenced by id (never by pickled payloads). Each enqueue uses a unique
RQ id, and execution takes a per-job Redis lock so a duplicate delivery can never
run the same job twice.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from .config import settings
from .errors import ServiceUnavailable

logger = logging.getLogger("studio.queue")
TASK_PATH = "app.pipeline.orchestrator.run_job_task"


class JobQueue:
    def __init__(self, redis_url: str, queue_name: str):
        import redis
        from rq import Queue

        self.redis = redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=5)
        self.rq = Queue(queue_name, connection=self.redis)
        self.name = queue_name

    def ping(self) -> bool:
        try:
            return bool(self.redis.ping())
        except Exception:
            return False

    def depth(self) -> int:
        return len(self.rq)

    def enqueue(self, job_id: str, delay_seconds: int = 0) -> None:
        try:
            kwargs = {"job_timeout": settings.JOB_TIMEOUT_SECONDS, "result_ttl": 3600,
                      "failure_ttl": 7 * 24 * 3600, "job_id": f"{job_id}:{uuid.uuid4().hex[:8]}"}
            if delay_seconds > 0:
                from datetime import timedelta

                self.rq.enqueue_in(timedelta(seconds=delay_seconds), TASK_PATH, job_id, **kwargs)
            else:
                self.rq.enqueue(TASK_PATH, job_id, **kwargs)
        except Exception as exc:
            logger.error("enqueue failed", extra={"job_id": job_id, "error_type": type(exc).__name__})
            raise ServiceUnavailable("The processing queue is unavailable. Please retry shortly.",
                                     code="queue_unavailable") from exc

    def acquire_lock(self, job_id: str) -> Optional[str]:
        token = uuid.uuid4().hex
        ok = self.redis.set(f"lock:job:{job_id}", token, nx=True, ex=settings.JOB_TIMEOUT_SECONDS)
        return token if ok else None

    def release_lock(self, job_id: str, token: str) -> None:
        key = f"lock:job:{job_id}"
        try:
            if self.redis.get(key) == token.encode():
                self.redis.delete(key)
        except Exception as exc:
            logger.warning("lock release failed", extra={"job_id": job_id, "error_type": type(exc).__name__})

    def force_release_lock(self, job_id: str) -> None:
        self.redis.delete(f"lock:job:{job_id}")


_queue: Optional[JobQueue] = None


def get_queue() -> JobQueue:
    global _queue
    if _queue is None:
        if not settings.REDIS_URL:
            raise ServiceUnavailable("The processing queue is not configured.", code="queue_not_configured")
        _queue = JobQueue(settings.REDIS_URL, settings.QUEUE_NAME)
    return _queue
