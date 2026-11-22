"""API v1 master router aggregating all domain sub-routers."""
from fastapi import APIRouter
from .endpoints import (
    analytics,
    audit_logs,
    campaigns,
    marketplace_operator,
    auth,
    autonomous,
    categories,
    channels,
    clips,
    dashboard,
    discovery,
    health,
    integrations,
    jobs,
    outro,
    publishing,
    quotas,
    schedules,
    status,
    studio,
)

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth.router)
api_v1_router.include_router(dashboard.router)
api_v1_router.include_router(status.router)
api_v1_router.include_router(health.router)
api_v1_router.include_router(integrations.router)
api_v1_router.include_router(jobs.router)
api_v1_router.include_router(clips.router)
api_v1_router.include_router(studio.router)
api_v1_router.include_router(autonomous.router)
api_v1_router.include_router(outro.router)
api_v1_router.include_router(categories.router)
api_v1_router.include_router(discovery.router)
api_v1_router.include_router(publishing.router)
api_v1_router.include_router(analytics.router)
api_v1_router.include_router(schedules.router)
api_v1_router.include_router(quotas.router)
api_v1_router.include_router(channels.router)
api_v1_router.include_router(audit_logs.router)
api_v1_router.include_router(campaigns.router)
api_v1_router.include_router(marketplace_operator.router)
