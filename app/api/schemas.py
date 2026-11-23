"""Request and response models. Owner ids, storage keys and secrets are never exposed."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..captions.timing import PRESETS

CaptionPreset = Literal["clean_minimal", "bold_creator", "high_contrast", "modern_short", "highlight_keywords"]


def _f(value: Any) -> Optional[float]:
    return None if value is None else float(value)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None


class SourceValidateIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class SourceValidateOut(BaseModel):
    valid: bool = True
    source_type: Literal["youtube"] = "youtube"
    video_id: str
    normalized_url: str
    retrieval_enabled: bool


class YouTubeSourceIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_type: str
    status: str
    title: Optional[str] = None
    channel_title: Optional[str] = None
    original_url: Optional[str] = None
    youtube_video_id: Optional[str] = None
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    has_audio: Optional[bool] = None
    container_format: Optional[str] = None
    file_size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "SourceOut":
        data = dict(row)
        data["duration_seconds"] = _f(data.get("duration_seconds"))
        return cls(**{k: data.get(k) for k in cls.model_fields})


class JobCreateIn(BaseModel):
    source_id: UUID
    clip_count: Literal[5] = 5
    caption_preset: CaptionPreset = "bold_creator"
    language: Optional[str] = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$", max_length=12)
    min_clip_seconds: int = Field(default=15, ge=5, le=180)
    max_clip_seconds: int = Field(default=90, ge=5, le=180)

    @model_validator(mode="after")
    def _range(self) -> "JobCreateIn":
        if self.min_clip_seconds >= self.max_clip_seconds:
            raise ValueError("min_clip_seconds must be smaller than max_clip_seconds")
        return self


class JobOut(BaseModel):
    id: UUID
    source_id: UUID
    status: str
    stage: str
    progress: int
    clip_count: int
    aspect_ratio: str
    caption_preset: str
    language: Optional[str] = None
    min_clip_seconds: int
    max_clip_seconds: int
    cancel_requested: bool
    attempt_count: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    queued_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "JobOut":
        return cls(**{k: row.get(k) for k in cls.model_fields})


class EventOut(BaseModel):
    event_type: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    stage: Optional[str] = None
    message: Optional[str] = None
    created_at: datetime


class JobDetailOut(JobOut):
    clips: List["ClipOut"] = Field(default_factory=list)
    highlight_count: int = 0
    events: List[EventOut] = Field(default_factory=list)


class JobListOut(BaseModel):
    items: List[JobOut]
    total: int
    limit: int
    offset: int


class ClipOut(BaseModel):
    id: UUID
    job_id: UUID
    clip_number: int
    title: str
    description: Optional[str] = None
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    width: Optional[int] = None
    height: Optional[int] = None
    render_status: str
    validation_status: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ClipOut":
        data = {k: row.get(k) for k in cls.model_fields}
        for key in ("start_seconds", "end_seconds", "duration_seconds"):
            data[key] = float(data[key])
        return cls(**data)


class CaptionInfo(BaseModel):
    format: Literal["srt", "vtt", "ass"]
    cue_count: int
    word_timestamps: bool
    sync_ok: bool


class ClipDetailOut(ClipOut):
    captions: List[CaptionInfo] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)


class DownloadUrlOut(BaseModel):
    url: str
    expires_at: int
    filename: str


class UsageOut(BaseModel):
    period_start: datetime
    period_end: datetime
    jobs: Dict[str, int]
    metrics: Dict[str, float]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: Dict[str, Any] = Field(default_factory=dict)


JobDetailOut.model_rebuild()
__all__ = ["PRESETS"]
