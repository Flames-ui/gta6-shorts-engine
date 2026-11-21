"""API v1 master router aggregating all domain sub-routers."""
from fastapi import APIRouter
from .endpoints import (
    analytics,
    categories,
    channels,
    clips,
    discovery,
    jobs,
    publishing,
    schedules,
    status,
)

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(status.router)
api_v1_router.include_router(jobs.router)
api_v1_router.include_router(clips.router)
api_v1_router.include_router(categories.router)
api_v1_router.include_router(discovery.router)
api_v1_router.include_router(publishing.router)
api_v1_router.include_router(analytics.router)
api_v1_router.include_router(schedules.router)
api_v1_router.include_router(channels.router)
