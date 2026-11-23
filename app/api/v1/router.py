"""Versioned API router: everything lives under /api/v1."""
from fastapi import APIRouter

from . import admin, clips, files, jobs, sources, usage

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(sources.router)
api_v1_router.include_router(jobs.router)
api_v1_router.include_router(clips.router)
api_v1_router.include_router(files.router)
api_v1_router.include_router(usage.router)
api_v1_router.include_router(admin.router)
