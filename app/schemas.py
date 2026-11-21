"""Comprehensive Pydantic request and response schemas for multi-category production engine."""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

from .services.security import validate_safe_url


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, Enum):
    PENDING = "pending"
    RIGHTS_CHECK = "rights_check"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    HIGHLIGHT_DETECTION = "highlight_detection"
    CROPPING_CLIPS = "cropping_clips"
    MULTITRACK_EDITING = "multitrack_editing"
    OUTRO_INJECTION = "outro_injection"
    UPLOADING_STORAGE = "uploading_storage"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishingStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


# -----------------------------------------------------------------------------
# Jobs Schemas
# -----------------------------------------------------------------------------

class JobCreateRequest(BaseModel):
    url: str = Field(
        ...,
        description="Source video URL or local media path",
        examples=["https://www.youtube.com/watch?v=QdBZY2fkU-0"],
    )
    category_id: str = Field(
        default="gaming",
        description="Content category: gaming, viral_entertainment, animals_nature, tech_ai, sports, or custom",
        examples=["gaming"],
    )
    num_clips: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of vertical shorts to produce (1-10)",
    )
    aspect_ratio: str = Field(
        default="9:16",
        description="Target aspect ratio ('9:16', '1:1')",
    )
    target_format: str = Field(
        default="720",
        description="Video download resolution ('360', '480', '720', '1080')",
    )
    language: Optional[str] = Field(
        default=None,
        description="Transcription language code (ISO 639-1) or auto-detect if None",
    )
    mode: str = Field(
        default="local",
        description="Execution mode: local (FFmpeg/faster-whisper) or api",
    )
    llm_provider: str = Field(
        default="gemini",
        description="LLM for highlight scoring: 'gemini' or 'openai'",
    )
    gta_preset: bool = Field(
        default=True,
        description="Enable GTA 6 gaming visual and hashtag presets",
    )
    title_prefix: Optional[str] = Field(
        default=None,
        description="Optional title prefix for generated shorts",
    )
    custom_prompt: Optional[str] = Field(
        default=None,
        description="Optional custom focus prompt for highlight detection",
    )
    force: bool = Field(
        default=False,
        description="Force processing even if duplicate active URL exists",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("URL cannot be empty.")
        if not cleaned.startswith("/"):
            validate_safe_url(cleaned)
        return cleaned


class JobRetryRequest(BaseModel):
    reason: Optional[str] = "User requested retry"


class JobCancelRequest(BaseModel):
    reason: Optional[str] = "User cancelled job"


class JobLogEntry(BaseModel):
    stage: str
    message: str
    timestamp: str


class ClipResponse(BaseModel):
    id: str
    job_id: str
    user_id: Optional[str] = None
    category_id: str = "gaming"
    title: str
    hook_sentence: Optional[str] = None
    virality_reason: Optional[str] = None
    score: float
    start_time: float
    end_time: float
    duration: float
    aspect_ratio: str
    storage_url: str
    storage_path: Optional[str] = None
    thumbnail_url: Optional[str] = None
    publishing_status: str
    suggested_tags: List[str] = []
    is_quality_approved: bool = True
    created_at: str
    updated_at: Optional[str] = None


class JobResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    url: str
    category_id: str = "gaming"
    status: JobStatus
    stage: JobStage
    progress: int
    num_clips: int
    aspect_ratio: str
    video_title: Optional[str] = None
    video_duration: Optional[float] = None
    mode: str = "local"
    llm_provider: str = "gemini"
    error: Optional[str] = None
    retry_count: int = 0
    created_at: str
    updated_at: str
    clips: List[ClipResponse] = []
    logs: List[JobLogEntry] = []


class JobListResponse(BaseModel):
    items: List[JobResponse]
    total: int
    limit: int
    offset: int


# -----------------------------------------------------------------------------
# Category Schemas
# -----------------------------------------------------------------------------

class CategoryResponse(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    is_active: bool
    scoring_weights: Dict[str, float]
    default_hashtags: List[str]
    presets: Any = {}
    publishing_preferences: Dict[str, Any]



class CategoryUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    is_active: Optional[bool] = None
    system_prompt: Optional[str] = None
    scoring_weights: Optional[Dict[str, float]] = None
    default_hashtags: Optional[List[str]] = None
    presets: Optional[Any] = None
    publishing_preferences: Optional[Dict[str, Any]] = None

class CategoryCreateRequest(BaseModel):
    id: str = Field(..., pattern=r"^[a-z0-9_]{3,30}$")
    name: str
    description: str
    icon: str = "sparkles"
    system_prompt: str
    scoring_weights: Optional[Dict[str, float]] = None
    default_hashtags: Optional[List[str]] = None
    presets: Optional[Any] = None


# -----------------------------------------------------------------------------
# Content Sources & Discovery Schemas
# -----------------------------------------------------------------------------

class SourceCreateRequest(BaseModel):
    source_type: str = Field(..., description="official_api, approved_rss, creator_submission, direct_upload")
    name: str
    feed_url: Optional[str] = None
    channel_url: Optional[str] = None
    category_id: str = "gaming"
    rights_status: str = "pending_verification"
    license_type: Optional[str] = None
    allowed_platforms: List[str] = ["tiktok", "youtube_shorts"]


class SourceUpdateRequest(BaseModel):
    name: Optional[str] = None
    feed_url: Optional[str] = None
    channel_url: Optional[str] = None
    is_active: Optional[bool] = None
    auto_clip_enabled: Optional[bool] = None


class SourceRightsUpdateRequest(BaseModel):
    rights_status: str = Field(..., description="authorized_editing, authorized_platforms, rejected, expired")
    allowed_platforms: List[str] = ["tiktok", "youtube_shorts"]
    license_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None


class DiscoveredItemResponse(BaseModel):
    id: str
    source_id: str
    title: str
    original_url: Optional[str] = None
    media_url: Optional[str] = None
    creator_name: Optional[str] = None
    category_id: str
    duration_seconds: Optional[float] = None
    rights_status: str
    discovery_time: str
    is_processed: bool = False


# -----------------------------------------------------------------------------
# Buffer & Publishing Schemas
# -----------------------------------------------------------------------------

class ClipApprovalRequest(BaseModel):
    approved: Optional[bool] = None
    approved_for_publishing: Optional[bool] = None
    title: Optional[str] = None
    custom_title: Optional[str] = None
    hook: Optional[str] = None
    hashtags: Optional[List[str]] = None


class ClipScheduleRequest(BaseModel):
    channel_id: str
    scheduled_for: datetime
    caption: Optional[str] = None
    override_quota: bool = False


class BufferCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None


class BufferConnectResponse(BaseModel):
    auth_url: str
    state: str


# -----------------------------------------------------------------------------
# Analytics & Scheduling Schemas
# -----------------------------------------------------------------------------

class ProductionScheduleUpdateRequest(BaseModel):
    daily_target_min: Optional[int] = Field(None, ge=1, le=100)
    daily_target_max: Optional[int] = Field(None, ge=1, le=100)
    timezone: Optional[str] = None
    is_paused: Optional[bool] = None
    category_distribution: Optional[Dict[str, float]] = None


# -----------------------------------------------------------------------------
# Channel Schemas
# -----------------------------------------------------------------------------

class ChannelCreateRequest(BaseModel):
    channel_name: str
    channel_url: str
    auto_clip_new_videos: bool = False


class ChannelResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    channel_name: str
    channel_url: str
    auto_clip_new_videos: bool = False
    created_at: str

# -----------------------------------------------------------------------------
# System Status Schemas
# -----------------------------------------------------------------------------

class StatusResponse(BaseModel):
    status: str = "operational"
    version: str = "2.0.0"
    active_jobs: int = 0
    queued_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    workers_online: int = 1
    supported_modes: List[str] = ["local", "api"]
    default_llm_provider: str = "gemini"
    storage_provider: str = "supabase_storage"
    youtube_oauth_configured: bool = False


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    timestamp: str
    redis: str
    supabase: str


class AuthMeResponse(BaseModel):
    user_id: str
    role: str
    is_admin: bool
    auth_method: str


class DashboardStatsResponse(BaseModel):
    total_jobs: int
    active_jobs: int
    completed_jobs: int
    failed_jobs: int
    total_clips: int
    approved_clips: int
    scheduled_clips: int
    published_clips: int
    total_sources: int
    active_categories: int
    workers_online: int


class AuditLogEntryResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details: Dict[str, Any] = {}
    ip_address: Optional[str] = None
    created_at: str
