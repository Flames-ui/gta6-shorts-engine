"""Shared FastAPI dependencies: authenticated principal, rate limit, owned-resource lookups."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Request

from ..auth import Principal, current_principal
from ..config import settings
from ..errors import NotFound, StudioError, TooLarge
from ..ratelimit import limiter


def rate_limited(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
    if not limiter.allow(f"user:{principal.user_id}"):
        raise StudioError("Too many requests. Wait a minute and try again.", code="rate_limited",
                          status_code=429, details={"retry_after_seconds": 60})
    return principal


def check_content_length(request: Request) -> None:
    raw = request.headers.get("content-length")
    if raw and raw.isdigit() and int(raw) > settings.MAX_UPLOAD_BYTES + 1_048_576:
        raise TooLarge(f"Request is larger than the {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                       code="upload_too_large")


def not_found(what: str) -> NotFound:
    return NotFound(f"{what} was not found.")


def optional_header(value: Optional[str]) -> Optional[str]:
    return value.strip() if value and value.strip() else None
