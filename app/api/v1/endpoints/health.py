"""Health check endpoints for Render and monitoring."""
from datetime import datetime, timezone
from fastapi import APIRouter
from ....config import settings
from ....queue import queue_manager
from ....schemas import HealthResponse
from ....services.supabase_client import db

router = APIRouter()


@router.get("/", tags=["Health"])
async def root():
    """Root service information and quick diagnostic."""
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "online",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
        "endpoints": {
            "health": "/health",
            "status": "/api/v1/status",
            "jobs": "/api/v1/jobs",
            "clips": "/api/v1/clips/{clip_id}",
            "channels": "/api/v1/channels",
        },
    }


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Render health check endpoint. Responds with 200 OK and dependency statuses."""
    redis_status = "connected" if queue_manager.is_connected else "fallback_local"
    supabase_status = "connected" if db.has_supabase else "fallback_sqlite"

    return HealthResponse(
        status="healthy",
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        timestamp=datetime.now(timezone.utc).isoformat(),
        redis=redis_status,
        supabase=supabase_status,
    )
