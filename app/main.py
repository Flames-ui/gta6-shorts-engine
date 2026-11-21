"""FastAPI application entrypoint for GTA 6 & Multi-Category Viral Shorts Engine."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.v1.router import api_v1_router
from .config import settings
from .queue import job_queue
from .services.supabase_client import db

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gta6_shorts.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing %s v%s in %s mode", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)
    yield
    logger.info("Shutting down %s", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Production backend for multi-category viral short-form video discovery, AI editing, and publishing.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# -----------------------------------------------------------------------------
# Security Middleware: Request Size Limits & Headers
# -----------------------------------------------------------------------------
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.MAX_REQUEST_BODY_SIZE_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": "Request payload exceeds maximum permissible limit (100MB)."},
                )
        except ValueError:
            pass

    response: Response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# -----------------------------------------------------------------------------
# Strict CORS Configuration
# -----------------------------------------------------------------------------
effective_origins = settings.get_effective_cors_origins()
logger.info("Configured CORS allowlist: %s", effective_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=effective_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "Accept"],
    max_age=600,
)


# -----------------------------------------------------------------------------
# Root & Health Check Endpoints (For Render & Monitoring)
# -----------------------------------------------------------------------------
@app.get("/", tags=["System"])
async def root():
    """Service metadata and status summary."""
    return {
        "service": "GTA 6 Shorts Engine & Viral Video Pipeline",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "status": "online",
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "api_v1": "/api/v1",
        },
    }


@app.get("/health", tags=["System"])
async def health():
    """Health check endpoint used by Render and container orchestrators."""
    return {
        "status": "healthy",
        "service": "GTA 6 Shorts Engine",
        "version": settings.APP_VERSION,
        "redis": "connected" if job_queue.has_redis else "offline",
        "supabase": "connected" if db.has_supabase else "local_sqlite",
    }


# -----------------------------------------------------------------------------
# Mount API v1 Master Router
# -----------------------------------------------------------------------------
app.include_router(api_v1_router)
