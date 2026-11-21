"""Health and readiness check endpoints for Render and monitoring."""
from datetime import datetime, timezone
from fastapi import APIRouter, Response, status
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
            "ready": "/health/ready",
            "status": "/api/v1/status",
            "jobs": "/api/v1/jobs",
            "clips": "/api/v1/clips/{clip_id}",
            "channels": "/api/v1/channels",
            "categories": "/api/v1/categories",
            "discovery": "/api/v1/discovery/feed",
            "publishing": "/api/v1/publishing/channels",
        },
    }


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Render health check endpoint. Responds with status and dependency details."""
    is_prod = settings.ENVIRONMENT.lower() == "production"
    redis_status = "connected" if queue_manager.is_connected else ("unavailable" if is_prod else "fallback_local")
    supabase_status = "connected" if db.has_supabase else ("unavailable" if is_prod else "fallback_sqlite")

    # In production, report degraded if any mandatory dependency is not connected
    if is_prod and (not queue_manager.is_connected or not db.has_supabase):
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    return HealthResponse(
        status=overall_status,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        timestamp=datetime.now(timezone.utc).isoformat(),
        redis=redis_status,
        supabase=supabase_status,
    )


@router.get("/health/ready", tags=["Health"])
async def readiness_check(response: Response):
    """
    Readiness probe for Render and load balancers.
    Returns HTTP 200 when ready to accept traffic, or HTTP 503 if required dependencies are offline.
    """
    is_prod = settings.ENVIRONMENT.lower() == "production"
    dependencies_ok = True
    details = {}

    # Redis check
    redis_ok = queue_manager.is_connected
    details["redis"] = "connected" if redis_ok else "unavailable"
    if is_prod and not redis_ok:
        dependencies_ok = False

    # Supabase check
    supabase_ok = db.has_supabase
    details["supabase"] = "connected" if supabase_ok else ("unavailable" if is_prod else "fallback_sqlite")
    if is_prod and not supabase_ok:
        dependencies_ok = False

    if not dependencies_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "ready": False,
            "environment": settings.ENVIRONMENT,
            "dependencies": details,
        }

    return {
        "status": "healthy",
        "ready": True,
        "environment": settings.ENVIRONMENT,
        "dependencies": details,
    }


@router.get("/health/live", tags=["Health"])
async def liveness_check():
    """Liveness probe confirming web process is alive."""
    return {"status": "alive"}
