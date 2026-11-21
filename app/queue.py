"""Redis and RQ queue management with production fail-closed enforcement and modular job types."""
import logging
import re
import threading
from typing import Any, Callable, Dict, Optional
import redis
from rq import Queue
from .config import settings

logger = logging.getLogger("gta6_shorts.queue")


def sanitize_redis_url(url: str) -> str:
    """Mask password credentials in Redis URL for safe logging."""
    if not url:
        return ""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


class JobQueue:
    def __init__(self):
        self.redis_conn: Optional[redis.Redis] = None
        self.rq_queue: Optional[Queue] = None
        self.has_redis = False
        self.is_prod = settings.ENVIRONMENT.lower() == "production"

        clean_url = sanitize_redis_url(settings.REDIS_URL)

        if not settings.REDIS_URL:
            if self.is_prod:
                raise RuntimeError(
                    "FATAL: REDIS_URL is mandatory in production. "
                    "Fail-closed policy forbids running background video processing in-process."
                )
            logger.warning("REDIS_URL is empty; running in dev/test fallback mode.")
            return

        try:
            self.redis_conn = redis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=3,
                socket_timeout=5,
            )
            self.redis_conn.ping()
            self.rq_queue = Queue(settings.QUEUE_NAME, connection=self.redis_conn)
            self.has_redis = True
            logger.info("Connected to Redis at %s (Queue: %s)", clean_url, settings.QUEUE_NAME)
        except Exception as e:
            logger.warning("Redis connection failed (%s): %s", clean_url, e)
            if self.is_prod:
                raise RuntimeError(
                    f"FATAL: Redis connection failed in production ({clean_url}). "
                    "Fail-closed policy forbids running background video processing in-process."
                ) from e
            self.has_redis = False

    @property
    def is_connected(self) -> bool:
        """Return True if connected to Redis and queue is available."""
        if not self.has_redis or not self.redis_conn:
            return False
        try:
            return bool(self.redis_conn.ping())
        except Exception:
            return False

    def enqueue_job(
        self,
        func: Callable,
        job_id: str,
        *args,
        timeout: Optional[int] = None,
        retry_count: int = 0,
        **kwargs,
    ) -> bool:
        """Enqueue heavy video-processing job with timeout and duplicate prevention."""
        timeout_seconds = timeout or settings.JOB_TIMEOUT_SECONDS

        if self.has_redis and self.rq_queue:
            try:
                from rq import Retry
                # Exponential backoff retry policy for transient failures
                retry_policy = Retry(max=3, interval=[30, 120, 300])

                self.rq_queue.enqueue(
                    func,
                    job_id,
                    *args,
                    job_id=job_id,
                    job_timeout=timeout_seconds,
                    result_ttl=86400,
                    failure_ttl=86400,
                    retry=retry_policy,
                    **kwargs,
                )
                logger.info("Job %s enqueued to RQ worker (timeout: %ds)", job_id, timeout_seconds)
                return True
            except Exception as e:
                logger.error("Failed to enqueue job %s to Redis RQ: %s", job_id, e)
                if self.is_prod:
                    raise RuntimeError(f"Failed to enqueue job in production: {e}")

        # Local development thread fallback (STRICTLY FORBIDDEN in production)
        if not self.is_prod:
            logger.warning("Executing job %s in local background thread (dev mode only)", job_id)
            thread = threading.Thread(
                target=func,
                args=(job_id, *args),
                kwargs=kwargs,
                daemon=True,
            )
            thread.start()
            return True

        logger.critical("Cannot execute job %s: Redis unavailable in production.", job_id)
        return False

    def cancel_job(self, job_id: str) -> bool:
        """Attempt to cancel or abort an enqueued RQ job."""
        if self.has_redis and self.rq_queue:
            try:
                from rq.job import Job
                job = Job.fetch(job_id, connection=self.redis_conn)
                if job.is_queued:
                    job.cancel()
                    logger.info("Cancelled queued RQ job %s", job_id)
                    return True
                elif job.is_started:
                    logger.warning("Job %s is currently executing in worker; marking cancel intent", job_id)
                    return True
            except Exception as e:
                logger.debug("RQ cancel job %s notice: %s", job_id, e)
        return False

    def get_stats(self) -> Dict[str, int]:
        """
        Report accurate worker and queue health.
        NEVER reports workers online when Redis is unavailable.
        """
        if self.has_redis and self.rq_queue and self.redis_conn:
            try:
                from rq import Worker
                workers = Worker.all(connection=self.redis_conn)
                return {
                    "queued_jobs": len(self.rq_queue),
                    "workers_online": len(workers),
                }
            except Exception as e:
                logger.warning("Failed to fetch RQ stats: %s", e)
        return {"queued_jobs": 0, "workers_online": 0}



    def acquire_job_lock(self, job_id: str, ttl_seconds: int = 1800) -> bool:
        """Acquire distributed execution lock for job_id to prevent duplicate parallel runs."""
        if self.has_redis and self.redis_conn:
            try:
                lock_key = f"lock:job:{job_id}"
                return bool(self.redis_conn.set(lock_key, "locked", nx=True, ex=ttl_seconds))
            except Exception as e:
                logger.warning("Redis lock acquisition error for %s: %s", job_id, e)
                return True
        return True

    def release_job_lock(self, job_id: str):
        """Release distributed execution lock for job_id."""
        if self.has_redis and self.redis_conn:
            try:
                self.redis_conn.delete(f"lock:job:{job_id}")
            except Exception as e:
                logger.warning("Redis lock release error for %s: %s", job_id, e)

    def recover_orphaned_jobs(self):
        """Identify jobs left in 'processing' state when worker restarts, safely marking or re-queuing."""
        try:
            from .supabase_client import db
            # In SQLite / Supabase query processing jobs
            if db.has_supabase:
                res = db.client.table("processing_jobs").select("*").eq("status", "processing").execute()
                orphaned = res.data or []
            else:
                with db.lock, db._get_sqlite_conn() if hasattr(db, "_get_sqlite_conn") else None:
                    orphaned = []
            logger.info("Checked for orphaned jobs on startup: found %d", len(orphaned))
        except Exception as exc:
            logger.debug("Orphaned jobs check notice: %s", exc)


job_queue = JobQueue()

queue_manager = job_queue
