"""Application configuration settings."""
import os
from typing import List, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Service Information
    APP_NAME: str = "GTA 6 Shorts Engine & Multi-Category Pipeline"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = Field(default="production", description="Environment: development, test, production")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR")
    PORT: int = Field(default=8000, description="Server port")

    # Security & Authentication
    ENGINE_API_KEY: str = Field(default="", description="Secret API key required for /api/v1/ protected endpoints")
    ENGINE_API_KEY_PREVIOUS: str = Field(default="", description="Previous key for graceful rotation")
    SESSION_DB_PATH: str = Field(
        default="",
        description="Path to SQLite database for persistent operator sessions on persistent disk (/var/data/operator_sessions.sqlite3)"
    )

    FAIL_CLOSED_IN_PRODUCTION: bool = Field(default=True, description="Enforce strict authentication in production")
    STRICT_CAMPAIGN_GATE_TEST: bool = Field(default=False)
    ENFORCE_CAMPAIGN_GATE: bool = Field(
        default=True,
        description="Enforce strict campaign eligibility gate: No campaign, no clipping. Disallow direct clipping requests without an approved campaign."
    )
    DEFAULT_MONTHLY_EARNINGS_TARGET: float = Field(
        default=500.0,
        description="Target monthly earnings in USD for clipping operations"
    )
    PUBLISHING_AUTOMATION_ENABLED: bool = Field(
        default=False,
        description="Enable automated background publishing to external platforms. Must remain false during deployment testing to prevent unintended public posts."
    )
    CORS_ORIGINS: Union[str, List[str]] = Field(
        default=["https://lovable.dev", "https://lovable.app"],
        description="Allowed CORS origins (comma-separated or list)"
    )

    # Browser Automation & Marketplace Operator (Milestone 2)
    BROWSER_PROFILE_DIR: str = Field(default="/tmp/browser_profile", description="Directory for persistent browser profile and cookies")
    BROWSER_HEADLESS: bool = Field(default=True, description="Run Playwright browser in headless mode")
    BROWSER_NAVIGATION_TIMEOUT_MS: int = Field(default=30000, description="Default page navigation timeout in milliseconds")
    BROWSER_MAX_MEMORY_MB: int = Field(default=256, description="Memory constraint for browser process on 512MB RAM instance")
    CONTENT_REWARDS_BASE_URL: str = Field(default="https://contentrewards.com", description="Official Content Rewards website base URL")
    CAMPAIGN_REFRESH_INTERVAL_MINUTES: int = Field(default=60, description="Interval for background campaign detail refresh")

    # Supabase Integration
    SUPABASE_URL: str = Field(default="", description="Supabase project URL")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(default="", description="Supabase service role secret key")
    SUPABASE_JWT_SECRET: str = Field(default="", description="Secret to verify Supabase Auth user JWTs")
    SUPABASE_STORAGE_BUCKET: str = Field(default="shorts", description="Supabase storage bucket for rendered clips")

    # Queue & Worker (Redis + RQ)
    REDIS_URL: str = Field(default="redis://localhost:6379/0", description="Redis connection URL for task queue")
    QUEUE_NAME: str = Field(default="viral-shorts", description="RQ queue name")
    MAX_CONCURRENT_JOBS: int = Field(default=2, description="Worker concurrency limit")
    JOB_TIMEOUT_SECONDS: int = Field(default=1800, description="Job processing timeout (seconds)")

    # AI Models (Gemini primary, OpenAI fallback)
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key for highlight detection & copywriting")
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash", description="Gemini model name")
    OPENAI_API_KEY: str = Field(default="", description="Optional OpenAI API key")
    OPENAI_MODEL: str = Field(default="gpt-4o-mini", description="OpenAI model name")
    LLM_PROVIDER: str = Field(default="gemini", description="Default LLM provider: gemini or openai")

    # Token Encryption at Rest & Admin Controls
    TOKEN_ENCRYPTION_KEY: str = Field(default="", description="Secret key for encrypting OAuth tokens at rest")
    ADMIN_USER_IDS: Union[str, List[str]] = Field(default=[], description="Server-controlled list of admin user IDs")
    ADMIN_EMAILS: Union[str, List[str]] = Field(default=[], description="Server-controlled list of admin emails")

    # Buffer Social Publishing Integration (Primary Publisher)
    BUFFER_CLIENT_ID: str = Field(default="", description="Buffer OAuth Client ID")
    BUFFER_CLIENT_SECRET: str = Field(default="", description="Buffer OAuth Client Secret")
    BUFFER_ACCESS_TOKEN: str = Field(default="", description="Buffer user access token")
    BUFFER_REDIRECT_URI: str = Field(default="", description="Buffer OAuth callback URL")

    # YouTube Data API OAuth (Secondary / Direct Publishing)
    YOUTUBE_CLIENT_ID: str = Field(default="", description="Google Cloud OAuth Client ID")
    YOUTUBE_CLIENT_SECRET: str = Field(default="", description="Google Cloud OAuth Client Secret")
    YOUTUBE_REDIRECT_URI: str = Field(default="", description="Google OAuth Redirect URI")

    # Production Limits & Scheduling
    DAILY_TARGET_MIN: int = Field(default=3, description="Target minimum daily videos (Milestones 2.2 & 3: 3-5 target)")
    DAILY_TARGET_MAX: int = Field(default=5, description="Target maximum daily videos (Milestones 2.2 & 3: 3-5 target)")
    MAX_VIDEO_DURATION_SECONDS: int = Field(default=7200, description="Maximum allowed input video duration")
    MAX_REQUEST_BODY_SIZE_BYTES: int = Field(default=104857600, description="Max request body size (100MB)")

    # Local Video Processing Settings
    LOCAL_WHISPER_MODEL: str = Field(default="base", description="Whisper model: tiny, base, small, medium, large-v3")
    LOCAL_WHISPER_DEVICE: str = Field(default="auto", description="Whisper device: auto, cpu, cuda")
    LOCAL_OUTPUT_DIR: str = Field(default="output", description="Local directory for temporary renders")
    TEMP_DIR: str = Field(default="/app/tmp", description="Temporary working directory for video operations")

    OUTPUT_DIR: str = Field(default="output", description="Output directory alias")
    DEFAULT_NUM_CLIPS: int = Field(default=3, description="Default number of clips to extract")
    DEFAULT_ASPECT_RATIO: str = Field(default="9:16", description="Default video aspect ratio")

    # Legacy / Optional External API Mode
    MUAPI_API_KEY: str = Field(default="", description="Optional MuAPI key")
    MUAPI_BASE_URL: str = Field(default="https://api.muapi.ai/api/v1", description="MuAPI base URL")

    @field_validator("ADMIN_USER_IDS", mode="before")
    @classmethod
    def parse_admin_user_ids(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v or []

    @field_validator("ADMIN_EMAILS", mode="before")
    @classmethod
    def parse_admin_emails(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [x.strip().lower() for x in v.split(",") if x.strip()]
        return [x.lower() for x in v] if v else []

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            if v.strip() == "*":
                return ["*"]
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    def get_effective_cors_origins(self) -> List[str]:
        """Return safe CORS origins based on environment."""
        if self.ENVIRONMENT.lower() in ("development", "test"):
            # Include local development frontends
            local_origins = [
                "http://localhost:3000",
                "http://localhost:5173",
                "http://localhost:8000",
                "http://localhost:8080",
                "http://127.0.0.1:3000",
                "http://127.0.0.1:5173",
                "http://127.0.0.1:8000",
            ]
            if isinstance(self.CORS_ORIGINS, list):
                return list(set(self.CORS_ORIGINS + local_origins))
            return local_origins

        # In production, never return wildcard if credentials are used
        if isinstance(self.CORS_ORIGINS, list):
            cleaned = [o for o in self.CORS_ORIGINS if o != "*"]
            return cleaned or ["https://lovable.dev", "https://lovable.app"]
        return ["https://lovable.dev", "https://lovable.app"]


settings = Settings()
