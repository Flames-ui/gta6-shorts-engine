"""Pydantic request and response models."""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
from shorts_generator.url_validation import validate_youtube_url


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    HIGHLIGHT_DETECTION = "highlight_detection"
    CROPPING_CLIPS = "cropping_clips"
    UPLOADING_STORAGE = "uploading_storage"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishingStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


# --- Request Schemas ---

class JobCreateRequest(BaseModel):
    url: str = Field(
        ...,
        description="Source YouTube video URL (https://www.youtube.com/... or https://youtu.be/...)",
        examples=["https://www.youtube.com/watch?v=QdBZY2fkU-0"],
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
        description="Optional ISO language code for transcription (e.g. 'en')",
    )
    mode: str = Field(
        default="local",
        description="Processing mode: 'local' (FFmpeg + Whisper + Gemini/OpenAI) or 'api' (MuAPI)",
    )
    llm_provider: Optional[str] = Field(
        default="gemini",
        description="LLM for highlight scoring and copywriting: 'gemini' or 'openai'",
    )
    gta_preset: bool = Field(
        default=True,
        description="Apply GTA 6 gaming viral hook prompts, title presets, and tags",
    )
    title_prefix: Optional[str] = Field(
        default=None,
        description="Optional custom prefix for generated clip titles",
    )
    custom_prompt: Optional[str] = Field(
        default=None,
        description="Optional custom prompt instructions for highlight ranking",
    )
    force: bool = Field(
        default=False,
        description="Force job creation even if an active job exists for this URL",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        try:
            return validate_youtube_url(v)
        except ValueError as exc:
            raise ValueError(f"Invalid or unsafe URL: {exc}") from exc

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if v_clean not in ("local", "api"):
            raise ValueError("Mode must be 'local' or 'api'")
        return v_clean


class JobRetryRequest(BaseModel):
    reason: Optional[str] = Field(default="Manual retry requested via dashboard")


class JobCancelRequest(BaseModel):
    reason: Optional[str] = Field(default="Cancelled by user")


class ClipApproveRequest(BaseModel):
    approved_for_publishing: bool = Field(default=True)
    custom_title: Optional[str] = None
    custom_description: Optional[str] = None
    custom_hashtags: Optional[List[str]] = None


class ChannelCreateRequest(BaseModel):
    channel_url: str = Field(..., description="YouTube channel URL or handle")
    channel_name: str = Field(..., description="Display name for channel")
    auto_clip_new_videos: bool = Field(default=False, description="Whether to automatically queue new uploads")


# --- Response Schemas ---

class ClipResponse(BaseModel):
    id: str
    job_id: str
    title: str
    description: Optional[str] = None
    hook: Optional[str] = None
    score: Optional[int] = 0
    start_time: float
    end_time: float
    duration: float
    aspect_ratio: str = "9:16"
    clip_url: Optional[str] = None
    storage_path: Optional[str] = None
    thumbnail_url: Optional[str] = None
    captions: Optional[str] = None
    hashtags: List[str] = Field(default_factory=list)
    publishing_status: PublishingStatus = PublishingStatus.DRAFT
    created_at: str


class ProcessingLogEntry(BaseModel):
    timestamp: str
    stage: str
    message: str


class JobResponse(BaseModel):
    id: str
    url: str
    status: JobStatus
    stage: JobStage
    progress: int = Field(default=0, ge=0, le=100)
    num_clips: int
    video_title: Optional[str] = None
    video_duration: Optional[float] = None
    error: Optional[str] = None
    mode: str = "local"
    llm_provider: str = "gemini"
    gta_preset: bool = True
    retry_count: int = 0
    created_at: str
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    clips: List[ClipResponse] = Field(default_factory=list)
    logs: List[ProcessingLogEntry] = Field(default_factory=list)


class JobListResponse(BaseModel):
    items: List[JobResponse]
    total: int
    limit: int
    offset: int


class JobProgressResponse(BaseModel):
    id: str
    status: JobStatus
    stage: JobStage
    progress: int
    error: Optional[str] = None
    updated_at: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str
    environment: str
    timestamp: str
    redis: str
    supabase: str


class StatusResponse(BaseModel):
    status: str
    version: str
    active_jobs: int
    queued_jobs: int
    completed_jobs: int
    failed_jobs: int
    workers_online: int
    supported_modes: List[str]
    default_llm_provider: str
    storage_provider: str
    youtube_oauth_configured: bool


class ChannelResponse(BaseModel):
    id: str
    channel_url: str
    channel_name: str
    auto_clip_new_videos: bool
    created_at: str
