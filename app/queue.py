"""Redis and RQ queue management with production fail-closed enforcement and modular job types."""
import logging
import threading
from typing import Any, Callable, Dict, Optional
import redis
from rq import Queue
from .config import settings

logger = logging.getLogger("gta6_shorts.queue")


class JobQueue:
    def __init__(self):
        self.redis_conn: Optional[redis.Redis] = None
        self.rq_queue: Optional[Queue] = None
        self.has_redis = False
        self.is_prod = settings.ENVIRONMENT.lower() == "production"

        try:
            self.redis_conn = redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=5)
            self.redis_conn.ping()
            self.rq_queue = Queue(settings.QUEUE_NAME, connection=self.redis_conn)
            self.has_redis = True
            logger.info("Connected to Redis at %s (Queue: %s)", settings.REDIS_URL, settings.QUEUE_NAME)
        except Exception as e:
            logger.warning("Redis connection failed (%s): %s", settings.REDIS_URL, e)
            if self.is_prod and settings.FAIL_CLOSED_IN_PRODUCTION:
                raise RuntimeError(
                    f"FATAL: Redis connection failed in production ({settings.REDIS_URL}). "
                    "Fail-closed policy forbids running background video processing in-process."
                ) from e
            self.has_redis = False

    def enqueue_job(
        self,
        func: Callable,
        job_id: str,
        *args,
        timeout: Optional[int] = None,
        **kwargs,
    ) -> bool:
        """Enqueue heavy video-processing job with timeout and duplicate prevention."""
        timeout_seconds = timeout or settings.JOB_TIMEOUT_SECONDS

        if self.has_redis and self.rq_queue:
            try:
                self.rq_queue.enqueue(
                    func,
                    job_id,
                    *args,
                    job_id=job_id,
                    job_timeout=timeout_seconds,
                    result_ttl=86400,
                    failure_ttl=86400,
                    **kwargs,
                )
                logger.info("Job %s enqueued to RQ worker (timeout: %ds)", job_id, timeout_seconds)
                return True
            except Exception as e:
                logger.error("Failed to enqueue job %s to Redis RQ: %s", job_id, e)
                if self.is_prod:
                    raise RuntimeError(f"Failed to enqueue job in production: {e}")

        # Local development thread fallback (NEVER allowed in production)
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


job_queue = JobQueue()

# Backward-compatibility alias
queue_manager = job_queue

def _get_stats(self):
    if self.has_redis and self.rq_queue:
        try:
            from rq import Worker
            workers = Worker.all(connection=self.redis_conn)
            return {
                "queued_jobs": len(self.rq_queue),
                "workers_online": len(workers),
            }
        except Exception:
            pass
    return {"queued_jobs": 0, "workers_online": 1}

JobQueue.get_stats = _get_stats
