"""FastAPI application entry point."""
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.v1.endpoints.health import router as health_router
from .api.v1.router import api_v1_router
from .config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gta6_shorts.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing %s v%s in %s environment", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)
    # Ensure local output directory exists
    os.makedirs(os.path.join(settings.LOCAL_OUTPUT_DIR, "clips"), exist_ok=True)
    yield
    logger.info("Shutting down %s", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="High-performance backend engine for transforming long-form GTA 6 and gaming videos into viral 9:16 Shorts with Gemini AI highlighting and Supabase storage.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount local static file serving for fallback clip preview
clips_dir = os.path.join(settings.LOCAL_OUTPUT_DIR, "clips")
os.makedirs(clips_dir, exist_ok=True)
app.mount("/static/clips", StaticFiles(directory=clips_dir), name="static-clips")

# Include Routers
app.include_router(health_router)
app.include_router(api_v1_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server error processing %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "path": request.url.path,
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=False)
