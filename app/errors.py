"""Consistent API error model. Internal details never reach clients."""
from __future__ import annotations

from typing import Any, Dict, Optional


class StudioError(Exception):
    """Expected, client-safe failure with a stable machine-readable code."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: Optional[str] = None, status_code: Optional[int] = None,
                 details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}


class NotFound(StudioError):
    status_code = 404
    code = "not_found"


class Unauthorized(StudioError):
    status_code = 401
    code = "unauthorized"


class Forbidden(StudioError):
    status_code = 403
    code = "forbidden"


class Conflict(StudioError):
    status_code = 409
    code = "conflict"


class Unprocessable(StudioError):
    status_code = 422
    code = "validation_failed"


class TooLarge(StudioError):
    status_code = 413
    code = "payload_too_large"


class ServiceUnavailable(StudioError):
    status_code = 503
    code = "service_unavailable"


class RetrievalError(StudioError):
    """A source could not be retrieved. `category` says why, for users and retries."""

    status_code = 422
    code = "source_retrieval_failed"

    def __init__(self, message: str, *, category: str, retryable: bool = False):
        super().__init__(message, details={"category": category, "retryable": retryable})
        self.category = category
        self.retryable = retryable


class MediaValidationError(StudioError):
    status_code = 422
    code = "media_validation_failed"


class PipelineError(Exception):
    """Internal pipeline failure. `code` is persisted on the job; `message` is client-safe."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class JobCancelled(Exception):
    """Raised inside a pipeline when the user has requested cancellation."""
