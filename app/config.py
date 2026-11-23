"""Application configuration.

All limits, timeouts and provider settings come from environment variables.
`validate_settings()` reports every missing or invalid value at once so a
misconfigured deployment fails at startup with a clear message.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__("Invalid configuration: " + "; ".join(problems))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Runtime -----------------------------------------------------------
    APP_NAME: str = "AI Video Editing Studio"
    ENVIRONMENT: str = Field(default="development", description="development | test | production")
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = Field(default="http://localhost:5173", description="Comma-separated allowed origins")

    # --- Database (independent PostgreSQL) ---------------------------------
    DATABASE_URL: str = Field(default="", description="postgresql://user:pass@host:5432/db")

    # --- Authentication ----------------------------------------------------
    AUTH_JWT_SECRET: str = Field(default="", description="HS256 secret used to verify bearer tokens")
    AUTH_JWT_AUDIENCE: str = "authenticated"
    AUTH_TOKEN_LEEWAY_SECONDS: int = 30
    ADMIN_USER_IDS: str = Field(default="", description="Comma-separated user IDs with admin rights")
    ADMIN_API_KEY: str = Field(default="", description="Optional operator key for diagnostics endpoints")

    # --- Queue & workers ---------------------------------------------------
    REDIS_URL: str = Field(default="", description="redis://host:6379/0")
    QUEUE_NAME: str = "studio-jobs"
    JOB_TIMEOUT_SECONDS: int = 7200
    MAX_JOB_ATTEMPTS: int = 3
    STALE_HEARTBEAT_SECONDS: int = 300
    HEARTBEAT_INTERVAL_SECONDS: int = 15
    MAX_PARALLEL_RENDERS: int = Field(default=2, ge=1, le=5)

    # --- Storage -----------------------------------------------------------
    STORAGE_BACKEND: str = Field(default="local", description="local | supabase")
    STORAGE_LOCAL_ROOT: str = "/var/data/studio-storage"
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "studio-private"
    SIGNED_URL_TTL_SECONDS: int = 300
    TEMP_DIR: str = "/tmp/studio"
    SOURCE_RETENTION_DAYS: int = 30
    TEMP_RETENTION_HOURS: int = 6

    # --- Upload and source limits -----------------------------------------
    MAX_UPLOAD_BYTES: int = Field(default=524_288_000, description="Max upload size (500 MiB)")
    MAX_SOURCE_DURATION_SECONDS: int = Field(default=1800, description="Default maximum source length (30 min)")
    MIN_SOURCE_DURATION_SECONDS: int = 20

    # --- YouTube retrieval (yt-dlp) ---------------------------------------
    YOUTUBE_RETRIEVAL_ENABLED: bool = Field(
        default=False,
        description="Only enable for sources you have rights to. Disabled by default.",
    )
    YTDLP_TIMEOUT_SECONDS: int = 900
    YTDLP_RETRIES: int = 2
    YTDLP_MAX_HEIGHT: int = 1080

    # --- AI and transcription ---------------------------------------------
    AI_PROVIDER: str = Field(default="gemini", description="Highlight analysis provider: gemini")
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    AI_TIMEOUT_SECONDS: int = 120
    AI_MAX_RETRIES: int = 3
    WHISPER_MODEL: str = "base"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"

    # --- Clip selection and rendering -------------------------------------
    CLIP_COUNT: int = 5  # fixed by product requirement; validated below
    CLIP_MIN_SECONDS: int = 15
    CLIP_MAX_SECONDS: int = 90
    MIN_CLIP_SCORE: int = Field(default=75, ge=0, le=100)
    OUTPUT_WIDTH: int = 1080
    OUTPUT_HEIGHT: int = 1920
    FFMPEG_TIMEOUT_SECONDS: int = 900
    FFMPEG_THREADS: int = 0
    CAPTION_PRESET: str = "bold_creator"

    # --- API protection ----------------------------------------------------
    RATE_LIMIT_PER_MINUTE: int = 120
    REQUEST_TIMEOUT_SECONDS: int = 60

    @field_validator("ENVIRONMENT")
    @classmethod
    def _env(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"development", "test", "production"}:
            raise ValueError("ENVIRONMENT must be development, test or production")
        return v

    @field_validator("STORAGE_BACKEND")
    @classmethod
    def _storage(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"local", "supabase"}:
            raise ValueError("STORAGE_BACKEND must be local or supabase")
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip() and o.strip() != "*"]

    @property
    def admin_user_ids(self) -> set:
        return {x.strip() for x in self.ADMIN_USER_IDS.split(",") if x.strip()}


def _problems(s: Settings) -> List[str]:
    problems: List[str] = []
    if s.CLIP_COUNT != 5:
        problems.append("CLIP_COUNT must be 5: the product always renders exactly five clips")
    if s.CLIP_MIN_SECONDS < 5 or s.CLIP_MAX_SECONDS > 180 or s.CLIP_MIN_SECONDS >= s.CLIP_MAX_SECONDS:
        problems.append("CLIP_MIN_SECONDS must be >=5, CLIP_MAX_SECONDS <=180 and smaller than the maximum")
    if s.MAX_SOURCE_DURATION_SECONDS < 60:
        problems.append("MAX_SOURCE_DURATION_SECONDS must be at least 60")
    if s.MAX_UPLOAD_BYTES < 1_000_000:
        problems.append("MAX_UPLOAD_BYTES must be at least 1 MB")
    if s.OUTPUT_WIDTH % 2 or s.OUTPUT_HEIGHT % 2:
        problems.append("OUTPUT_WIDTH and OUTPUT_HEIGHT must be even numbers")
    if not s.DATABASE_URL.startswith(("postgresql://", "postgres://")):
        problems.append("DATABASE_URL must be a postgresql:// URL")
    if s.STORAGE_BACKEND == "supabase":
        if not s.SUPABASE_URL.startswith("https://"):
            problems.append("SUPABASE_URL must be an https URL when STORAGE_BACKEND=supabase")
        if not s.SUPABASE_SERVICE_ROLE_KEY:
            problems.append("SUPABASE_SERVICE_ROLE_KEY is required when STORAGE_BACKEND=supabase")
    if s.is_production:
        if len(s.AUTH_JWT_SECRET) < 32:
            problems.append("AUTH_JWT_SECRET must be at least 32 characters in production")
        if not s.REDIS_URL.startswith(("redis://", "rediss://")):
            problems.append("REDIS_URL is required in production (redis:// or rediss://)")
        if s.STORAGE_BACKEND == "local" and not s.STORAGE_LOCAL_ROOT.startswith("/"):
            problems.append("STORAGE_LOCAL_ROOT must be an absolute path in production")
        if s.AI_PROVIDER.lower() == "gemini" and not s.GEMINI_API_KEY:
            problems.append("GEMINI_API_KEY is required for highlight analysis")
    if s.AI_PROVIDER.lower() not in {"gemini", "fake"}:
        problems.append("AI_PROVIDER must be gemini")
    if s.AI_PROVIDER.lower() == "fake" and s.is_production:
        problems.append("AI_PROVIDER=fake is not allowed in production")
    return problems


def validate_database_settings(s: Optional[Settings] = None) -> Settings:
    """Validate only what the migration step needs, so a release job does not require provider keys."""
    s = s or get_settings()
    if not s.DATABASE_URL.startswith(("postgresql://", "postgres://")):
        raise ConfigError(["DATABASE_URL must be a postgresql:// URL"])
    return s


def validate_settings(s: Optional[Settings] = None) -> Settings:
    """Return validated settings or raise ConfigError listing every problem."""
    s = s or get_settings()
    problems = _problems(s)
    if problems:
        raise ConfigError(problems)
    return s


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
