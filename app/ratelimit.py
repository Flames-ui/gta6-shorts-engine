"""Per-principal sliding-window rate limiter (per process; use a shared store for multi-replica scale)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict


class SlidingWindowLimiter:
    def __init__(self, limit_per_minute: int, window_seconds: int = 60):
        self.limit = max(1, int(limit_per_minute))
        self.window = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory for many distinct keys
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window][:5000]:
                    self._hits.pop(k, None)
            return True


from .config import settings  # noqa: E402

limiter = SlidingWindowLimiter(limit_per_minute=settings.RATE_LIMIT_PER_MINUTE)
