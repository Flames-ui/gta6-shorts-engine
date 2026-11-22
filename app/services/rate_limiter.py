"""In-memory rate limiter using sliding window token counter."""
import logging
import time
from collections import defaultdict
from typing import Dict, List
from fastapi import HTTPException, Request, status

logger = logging.getLogger("gta6_shorts.rate_limiter")


class SlidingWindowRateLimiter:
    """Lightweight in-memory sliding window rate limiter."""

    def __init__(self, requests_per_minute: int = 120):
        self.requests_per_minute = requests_per_minute
        self.history: Dict[str, List[float]] = defaultdict(list)

    def check(self, identifier: str) -> bool:
        now = time.time()
        window_start = now - 60.0

        # Purge timestamps older than 60s
        self.history[identifier] = [t for t in self.history[identifier] if t > window_start]

        if len(self.history[identifier]) >= self.requests_per_minute:
            return False

        self.history[identifier].append(now)
        return True


rate_limiter = SlidingWindowRateLimiter(requests_per_minute=120)


async def check_rate_limit(request: Request):
    """FastAPI dependency to rate limit incoming API calls."""
    # Use client IP or authorization header token as identifier
    auth_header = request.headers.get("X-API-Key") or request.headers.get("Authorization", "")
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{auth_header[:16]}"

    if not rate_limiter.check(key):
        logger.warning("Rate limit exceeded for client %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait before submitting more requests.",
            headers={"Retry-After": "60"},
        )


login_rate_limiter = SlidingWindowRateLimiter(requests_per_minute=8)


async def check_login_rate_limit(request: Request):
    """Rate limit login attempts to 8 per minute per IP to prevent brute force."""
    client_ip = request.client.host if request.client else "unknown"
    key = f"login:{client_ip}"
    if not login_rate_limiter.check(key):
        logger.warning("Operator login rate limit exceeded for IP %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait 60 seconds before trying again.",
            headers={"Retry-After": "60"},
        )
