"""Dedicated Background Worker entrypoint for Render and production."""
import logging
import os
import signal
import sys
import redis
from rq import Queue, Worker

from .config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gta6_shorts.worker")


def run_worker():
    logger.info("Initializing GTA 6 Shorts Background Worker")
    logger.info("Connecting to Redis queue at %s", settings.REDIS_URL)

    try:
        redis_conn = redis.from_url(
            settings.REDIS_URL,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
        redis_conn.ping()
        logger.info("Redis connection established successfully.")
    except Exception as e:
        logger.critical("Failed to connect to Redis queue at %s: %s", settings.REDIS_URL, e)
        sys.exit(1)

    listen_queues = [settings.REDIS_QUEUE_NAME]
    logger.info("Listening on queue: %s", listen_queues)

    def signal_handler(signum, frame):
        logger.info("Received termination signal %s. Shutting down worker gracefully...", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    worker = Worker(listen_queues, connection=redis_conn)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    run_worker()
