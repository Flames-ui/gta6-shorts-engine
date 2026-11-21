"""API v1 master router."""
from fastapi import APIRouter
from .endpoints import channels, clips, jobs, status

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(status.router)
api_v1_router.include_router(jobs.router)
api_v1_router.include_router(clips.router)
api_v1_router.include_router(channels.router)
