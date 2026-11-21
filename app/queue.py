"""Redis and RQ task queue integration with resilient fallback."""
import logging
import threading
from typing import Dict, Optional
import redis
from rq import Queue
from .config import settings
from .services.video_processor import process_video_job

logger = logging.getLogger("gta6_shorts.queue")


class QueueManager:
    def __init__(self):
        self.redis_conn: Optional[redis.Redis] = None
        self.queue: Optional[Queue] = None
        self.is_connected = False
        self._init_connection()

    def _init_connection(self):
        try:
            self.redis_conn = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            # Test ping
            self.redis_conn.ping()
            self.queue = Queue(settings.QUEUE_NAME, connection=self.redis_conn, default_timeout=settings.JOB_TIMEOUT_SECONDS)
            self.is_connected = True
            logger.info("Connected to Redis task queue at %s (queue: %s)", settings.REDIS_URL, settings.QUEUE_NAME)
        except Exception as e:
            logger.warning("Redis queue unavailable (%s). Using in-process thread execution fallback.", e)
            self.is_connected = False

    def enqueue_job(self, job_id: str) -> bool:
        """Enqueue job to Redis RQ or spawn background thread fallback."""
        if settings.ENVIRONMENT == "test":
            logger.info("Test environment: bypassing worker execution for %s", job_id)
            return True

        if not self.is_connected:
            self._init_connection()

        if self.is_connected and self.queue:
            try:
                rq_job = self.queue.enqueue(
                    process_video_job,
                    job_id,
                    job_id=job_id,
                    job_timeout=settings.JOB_TIMEOUT_SECONDS,
                    result_ttl=86400,
                )
                logger.info("Enqueued job %s to RQ (RQ id: %s)", job_id, rq_job.id)
                return True
            except Exception as e:
                logger.error("Failed to enqueue to RQ (%s). Falling back to background thread.", e)

        # Fallback for local testing or when Redis is absent
        t = threading.Thread(target=process_video_job, args=(job_id,), daemon=True)
        t.start()
        logger.info("Dispatched job %s to background thread fallback", job_id)
        return True

    def cancel_job(self, job_id: str) -> bool:
        """Cancel queued RQ job if possible."""
        if self.is_connected and self.redis_conn:
            try:
                from rq.job import Job
                job = Job.fetch(job_id, connection=self.redis_conn)
                if job:
                    job.cancel()
                    return True
            except Exception as e:
                logger.warning("Could not cancel RQ job %s: %s", job_id, e)
        return False

    def get_stats(self) -> Dict[str, int]:
        """Return queue statistics."""
        if self.is_connected and self.queue and self.redis_conn:
            try:
                from rq import Worker
                workers = Worker.all(connection=self.redis_conn)
                workers_online = len([w for w in workers if w.state != "stopped"])
                return {
                    "queued_jobs": len(self.queue),
                    "workers_online": workers_online,
                }
            except Exception:
                pass
        return {"queued_jobs": 0, "workers_online": 1 if not self.is_connected else 0}


queue_manager = QueueManager()
