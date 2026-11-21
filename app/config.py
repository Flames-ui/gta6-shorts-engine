"""Application configuration settings."""
import os
from typing import List, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Service Information
    APP_NAME: str = "GTA 6 Shorts Engine"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="production", description="Environment: development, test, production")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR")
    PORT: int = Field(default=8000, description="Server port")

    # Security & Authentication
    ENGINE_API_KEY: str = Field(default="", description="Secret API key required for /api/v1/ protected endpoints")
    CORS_ORIGINS: Union[str, List[str]] = Field(default=["*"], description="Allowed CORS origins (comma-separated or list)")

    # AI Models (Gemini preferred, OpenAI fallback)
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key for highlight detection & copywriting")
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash", description="Gemini model name")
    OPENAI_API_KEY: str = Field(default="", description="Optional OpenAI API key")
    OPENAI_MODEL: str = Field(default="gpt-4o-mini", description="OpenAI model name")
    LLM_PROVIDER: str = Field(default="gemini", description="Default LLM provider: gemini or openai")

    # Supabase Integration
    SUPABASE_URL: str = Field(default="", description="Supabase project URL")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(default="", description="Supabase service role secret key")
    SUPABASE_STORAGE_BUCKET: str = Field(default="gta6-shorts", description="Supabase storage bucket for rendered clips")

    # Queue & Worker (Redis + RQ)
    REDIS_URL: str = Field(default="redis://localhost:6379/0", description="Redis connection URL for task queue")
    QUEUE_NAME: str = Field(default="gta6-shorts", description="RQ queue name")
    MAX_CONCURRENT_JOBS: int = Field(default=2, description="Worker concurrency limit")
    JOB_TIMEOUT_SECONDS: int = Field(default=1800, description="Max job processing timeout in seconds (30m)")

    # Video Processing Limits & Defaults
    MAX_VIDEO_DURATION_SECONDS: int = Field(default=7200, description="Max input video duration in seconds (2 hours)")
    DEFAULT_NUM_CLIPS: int = Field(default=3, description="Default number of vertical clips to generate")
    DEFAULT_ASPECT_RATIO: str = Field(default="9:16", description="Default output aspect ratio")
    DEFAULT_DOWNLOAD_FORMAT: str = Field(default="720", description="Source video download resolution")
    LOCAL_WHISPER_MODEL: str = Field(default="base", description="Whisper model size: tiny, base, small, medium, large-v3")
    LOCAL_WHISPER_DEVICE: str = Field(default="auto", description="Whisper device: auto, cpu, cuda")
    LOCAL_OUTPUT_DIR: str = Field(default="output", description="Temporary local working directory")

    # YouTube OAuth & Publishing Preparation
    YOUTUBE_CLIENT_ID: str = Field(default="", description="Google OAuth Client ID for YouTube Data API")
    YOUTUBE_CLIENT_SECRET: str = Field(default="", description="Google OAuth Client Secret")
    YOUTUBE_REDIRECT_URI: str = Field(default="", description="Google OAuth Redirect URI")

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, description="API rate limit per minute per IP")

    # Legacy / Optional MuAPI
    MUAPI_API_KEY: str = Field(default="", description="Optional MuAPI key for legacy API mode")
    MUAPI_BASE_URL: str = Field(default="https://api.muapi.ai/api/v1", description="MuAPI base URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            if not v or v.strip() == "*":
                return ["*"]
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            return v
        return ["*"]


settings = Settings()
