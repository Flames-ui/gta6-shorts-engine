"""FastAPI application entrypoint for GTA 6 Shorts Studio."""
from .services.rate_limiter import rate_limiter
import logging
import os
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from .api.v1.endpoints import health
from .api.v1.router import api_v1_router
from .config import settings

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gta6_shorts.main")


from .services.security import validate_encryption_key_at_startup

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing %s v%s in %s mode", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)
    # Validate session encryption key at startup
    try:
        validate_encryption_key_at_startup()
    except Exception as exc:
        logger.error("Startup encryption validation error: %s", exc)
        if settings.ENVIRONMENT.lower() == "production":
            raise
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
# Security Middleware: Request Size Limits & Defensive Headers
# -----------------------------------------------------------------------------
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
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

    # CSRF protection for cookie-session authenticated mutating requests
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not (path.startswith("/api/v1/auth/login") or path.startswith("/api/v1/auth/logout") or path.endswith("/callback")):
            session_id = request.cookies.get("session_id")
            has_api_key = bool(request.headers.get("X-API-Key") or request.headers.get("Authorization"))
            if session_id and not has_api_key:
                from .services.supabase_client import db
                sess = db.get_session(session_id)
                if sess:
                    csrf_header = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token")
                    expected_csrf = sess.get("csrf_token")
                    if not csrf_header or not expected_csrf or not secrets.compare_digest(csrf_header, expected_csrf):
                        return JSONResponse(
                            status_code=status.HTTP_403_FORBIDDEN,
                            content={"detail": "CSRF validation failed: missing or invalid X-CSRF-Token header."},
                        )

    # Rate limiting on API paths (exempting load balancer health probes and documentation)
    path = request.url.path
    if not (path.startswith("/health") or path.startswith("/api/v1/health") or path.startswith("/docs") or path.startswith("/openapi") or path.startswith("/redoc")):
        auth_header = request.headers.get("X-API-Key") or request.headers.get("Authorization", "")
        client_ip = request.client.host if request.client else "unknown"
        rate_key = f"{client_ip}:{auth_header[:16]}"
        if not rate_limiter.check(rate_key):
            logger.warning("Rate limit exceeded for %s on %s", client_ip, path)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Please wait before submitting more requests."},
                headers={"Retry-After": "60"},
            )

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
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "Accept", "X-CSRF-Token"],
    expose_headers=["X-CSRF-Token", "Content-Length", "Retry-After"],
    max_age=600,
)

# -----------------------------------------------------------------------------
# Mount Routers
# -----------------------------------------------------------------------------
app.include_router(health.router)
app.include_router(api_v1_router)

from fastapi.staticfiles import StaticFiles

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/", include_in_schema=False)
async def serve_dashboard():
    """Serve GTA 6 Shorts Studio dashboard frontend."""
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file, media_type="text/html")
    return JSONResponse(status_code=404, content={"detail": "Dashboard not found"})
