"""FastAPI application: `uvicorn app.main:app`.

Configuration is validated at startup. Migrations are NOT run here; they run as a
separate release step (`python -m app.migrate`) so API replicas never race.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api.health import router as health_router
from .api.v1.router import api_v1_router
from .config import settings, validate_settings
from .errors import StudioError
from .logging_config import configure_logging

configure_logging(settings.LOG_LEVEL)
logger = logging.getLogger("studio.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_settings()  # fails fast with every missing or invalid variable listed
    logger.info("api started", extra={"event": "api_started", "environment": settings.ENVIRONMENT,
                                      "version": __version__})
    yield
    logger.info("api stopped", extra={"event": "api_stopped"})


app = FastAPI(
    title=settings.APP_NAME,
    version=__version__,
    description="Turn a YouTube URL or an uploaded video into five captioned, vertical, 9:16 clips.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


def _error(request: Request, status: int, code: str, message: str, details=None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}, "request_id": request_id}},
    )


@app.exception_handler(StudioError)
async def studio_error_handler(request: Request, exc: StudioError):
    return _error(request, exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg", "")}
              for e in exc.errors()]
    return _error(request, 422, "validation_failed", "Some fields are invalid.", {"fields": errors})


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized"}.get(exc.status_code, "http_error")
    return _error(request, exc.status_code, code, str(exc.detail) if exc.detail else "Request failed.")


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.exception("unhandled error", extra={"event": "unhandled_error", "error_type": type(exc).__name__})
    return _error(request, 500, "internal_error", "Something went wrong. Please retry.")


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        logger.info("request", extra={"event": "http_request", "method": request.method,
                                      "path": request.url.path, "status": response.status_code,
                                      "duration_ms": round((time.monotonic() - started) * 1000, 1)})
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID", "X-Admin-Key"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)

app.include_router(health_router)
app.include_router(api_v1_router)
