"""Comprehensive Pydantic request and response schemas for multi-category production engine."""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from .services.discovery.base import AUTHORIZED_RIGHTS_STATUSES, RightsStatus
from .services.security import validate_safe_url

RIGHTS_STATUS_VALUES = tuple(status.value for status in RightsStatus)
_RIGHTS_STATUS_HINTS = {
    "authorized": "use 'authorized_editing' for footage authorized for editing, or 'authorized_platforms' with allowed_platforms",
}


def _validate_rights_status(value: str) -> str:
    """Accept only the RightsStatus values; anything else is rejected with a 422 listing the valid ones."""
    if value in RIGHTS_STATUS_VALUES:
        return value
    hint = _RIGHTS_STATUS_HINTS.get(value)
    message = f"rights_status must be one of: {', '.join(RIGHTS_STATUS_VALUES)}; got '{value}'"
    raise ValueError(f"{message}. {hint.capitalize()}." if hint else message)


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
    campaign_id: Optional[str] = Field(
        default=None,
        description="Associated campaign ID for campaign-aware production",
    )
    source_id: Optional[str] = Field(
        default=None,
        description="Associated authorized source ID for campaign-aware production",
    )
    target_platform: str = Field(
        default="tiktok",
        description="Target campaign platform: 'tiktok' or 'youtube_shorts'",
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

    @field_validator("rights_status")
    @classmethod
    def _check_rights_status(cls, value: str) -> str:
        return _validate_rights_status(value)


class SourceUpdateRequest(BaseModel):
    name: Optional[str] = None
    feed_url: Optional[str] = None
    channel_url: Optional[str] = None
    is_active: Optional[bool] = None
    auto_clip_enabled: Optional[bool] = None


class SourceRightsUpdateRequest(BaseModel):
    rights_status: str = Field(
        ...,
        description="One of: pending_verification, authorized_editing, authorized_platforms, rejected, expired",
    )
    allowed_platforms: List[str] = ["tiktok", "youtube_shorts"]
    license_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("rights_status")
    @classmethod
    def _check_rights_status(cls, value: str) -> str:
        return _validate_rights_status(value)

    @field_validator("allowed_platforms")
    @classmethod
    def _normalize_platforms(cls, value: List[str]) -> List[str]:
        seen: List[str] = []
        for platform in value:
            name = (platform or "").strip().lower()
            if name and name not in seen:
                seen.append(name)
        return seen

    @model_validator(mode="after")
    def _check_authorization_fields(self):
        status_value = RightsStatus(self.rights_status)
        if status_value in AUTHORIZED_RIGHTS_STATUSES:
            if status_value == RightsStatus.AUTHORIZED_PLATFORMS and not self.allowed_platforms:
                raise ValueError("allowed_platforms must list at least one platform for 'authorized_platforms'")
            if self.expires_at is not None:
                expires = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
                if expires <= datetime.now(timezone.utc):
                    raise ValueError("expires_at must be in the future for an authorized rights status")
        return self


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
    metadata_status: Optional[str] = None
    media_status: Optional[str] = None
    analysis_status: Optional[str] = None
    qualifying_moment_count: Optional[int] = None


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
    workers_online: int = 0
    ffmpeg_available: bool = False


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


# =============================================================================
# Milestone 1: Campaign-Aware Gaming Clipping Engine Schemas
# =============================================================================

class CampaignStatus(str, Enum):
    DISCOVERED = "discovered"
    ANALYZING = "analyzing"
    ELIGIBLE = "eligible"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"


class CampaignPlatform(str, Enum):
    TIKTOK = "tiktok"
    YOUTUBE_SHORTS = "youtube_shorts"


class CampaignRewardModel(str, Enum):
    CPM = "cpm"
    PRIZE_POOL = "prize_pool"
    FIXED_PER_CLIP = "fixed_per_clip"
    BOUNTY = "bounty"


class SourceVerificationStatus(str, Enum):
    VERIFIED = "verified"
    PENDING_VERIFICATION = "pending_verification"
    REJECTED = "rejected"
    EXPIRED = "expired"


class CampaignRuleAnalysis(BaseModel):
    is_confident: bool = Field(default=True, description="True if all essential rules could be confidently interpreted")
    permitted_content: List[str] = Field(default_factory=list)
    accepted_games: List[str] = Field(default_factory=list)
    accepted_platforms: List[str] = Field(default_factory=list)
    allowed_footage: List[str] = Field(default_factory=list)
    permitted_editing: List[str] = Field(default_factory=list)
    min_clip_duration_seconds: Optional[int] = None
    max_clip_duration_seconds: Optional[int] = None
    required_hashtags: List[str] = Field(default_factory=list)
    required_mentions: List[str] = Field(default_factory=list)
    required_captions: Optional[str] = None
    reward_model: str = Field(default="cpm")
    cpm: Optional[float] = None
    payout_amount: Optional[float] = None
    reward_per_view: Optional[float] = None
    view_qualification_rules: Dict[str, Any] = Field(default_factory=dict)
    disqualification_reasons: List[str] = Field(default_factory=list)
    ambiguities: List[str] = Field(default_factory=list)
    security_warnings: List[str] = Field(default_factory=list)


class CampaignCreateRequest(BaseModel):
    marketplace_id: str = Field(default="content_rewards", description="Marketplace identifier")
    marketplace_name: str = Field(default="Content Rewards", description="Marketplace display name")
    campaign_external_id: Optional[str] = None
    name: str = Field(..., description="Campaign name")
    description: Optional[str] = None
    url: str = Field(..., description="Campaign landing page URL")
    raw_rules: str = Field(..., description="Full original campaign rules text")
    deadline: Optional[str] = None
    submission_deadline: Optional[str] = None
    accepted_platforms: List[str] = Field(default=["tiktok", "youtube_shorts"])
    eligible_games: List[str] = Field(default=["gta6"])
    content_categories: List[str] = Field(default=["gaming"])
    required_hashtags: List[str] = Field(default_factory=list)
    required_mentions: List[str] = Field(default_factory=list)
    required_captions: Optional[str] = None
    min_clip_duration_seconds: int = 15
    max_clip_duration_seconds: int = 60
    resolution_requirements: str = "1080x1920"
    aspect_ratio: str = "9:16"
    reward_model: str = "cpm"
    payout_amount: float = 0.0
    cpm: float = 0.0
    reward_per_view: float = 0.0
    view_qualification_rules: Dict[str, Any] = Field(default_factory=dict)
    max_eligible_views: int = 0
    max_payout_per_clip: float = 0.0
    geographic_eligibility: List[str] = Field(default=["ALL"])
    account_eligibility: Dict[str, Any] = Field(default_factory=dict)
    source_video_requirements: Optional[str] = None
    content_restrictions: List[str] = Field(default_factory=list)
    prohibited_editing_techniques: List[str] = Field(default_factory=list)
    required_attribution: Optional[str] = None
    submission_instructions: Optional[str] = None


class CampaignImportRequest(BaseModel):
    url: str = Field(..., description="Campaign URL on marketplace")
    raw_rules: Optional[str] = Field(default=None, description="Optional operator-provided raw rules text")
    marketplace_id: str = Field(default="content_rewards", description="Target marketplace adapter")
    auto_analyze: bool = Field(default=True, description="Automatically run rule reader upon import")


class CampaignUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    deadline: Optional[str] = None
    submission_deadline: Optional[str] = None
    accepted_platforms: Optional[List[str]] = None
    eligible_games: Optional[List[str]] = None
    required_hashtags: Optional[List[str]] = None
    required_mentions: Optional[List[str]] = None
    required_captions: Optional[str] = None
    min_clip_duration_seconds: Optional[int] = None
    max_clip_duration_seconds: Optional[int] = None
    cpm: Optional[float] = None
    payout_amount: Optional[float] = None
    reward_model: Optional[str] = None
    source_video_requirements: Optional[str] = None
    content_restrictions: Optional[List[str]] = None
    review_notes: Optional[str] = None


class CampaignReviewRequest(BaseModel):
    decision: str = Field(..., description="'approve' or 'reject'")
    reason: Optional[str] = Field(default=None, description="Reason for approval or rejection")
    notes: Optional[str] = Field(default=None, description="Operator review notes")


class CampaignResponse(BaseModel):
    id: str
    marketplace_id: str
    marketplace_name: str
    campaign_external_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    url: str
    status: str
    deadline: Optional[str] = None
    submission_deadline: Optional[str] = None
    accepted_platforms: List[str] = Field(default_factory=list)
    eligible_games: List[str] = Field(default_factory=list)
    content_categories: List[str] = Field(default_factory=list)
    required_hashtags: List[str] = Field(default_factory=list)
    required_mentions: List[str] = Field(default_factory=list)
    required_captions: Optional[str] = None
    min_clip_duration_seconds: Optional[int] = None
    max_clip_duration_seconds: Optional[int] = None
    resolution_requirements: Optional[str] = None
    aspect_ratio: Optional[str] = None
    reward_model: Optional[str] = None
    payout_amount: Optional[float] = None
    cpm: Optional[float] = None
    reward_per_view: Optional[float] = None
    view_qualification_rules: Dict[str, Any] = Field(default_factory=dict)
    max_eligible_views: Optional[int] = None
    max_payout_per_clip: Optional[float] = None
    campaign_budget: Optional[float] = None
    remaining_budget: Optional[float] = None
    geographic_eligibility: List[str] = Field(default_factory=list)
    account_eligibility: Dict[str, Any] = Field(default_factory=dict)
    source_video_requirements: Optional[str] = None
    content_restrictions: List[str] = Field(default_factory=list)
    prohibited_editing_techniques: List[str] = Field(default_factory=list)
    required_attribution: Optional[str] = None
    submission_instructions: Optional[str] = None
    raw_rules: str = ""
    operator_approved: bool = False
    rejection_reason: Optional[str] = None
    review_notes: Optional[str] = None
    ambiguities: List[str] = Field(default_factory=list)
    date_last_verified: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CampaignListResponse(BaseModel):
    items: List[CampaignResponse]
    total: int
    offset: int
    limit: int


class CampaignSourceRequest(BaseModel):
    source_url: str = Field(..., description="URL of source video or asset")
    original_creator: str = Field(..., description="Creator, rights holder, or streamer")
    source_type: str = Field(default="campaign_provided", description="campaign_provided, creator_authorized, official_trailer, streamer_vod")
    permission_evidence: str = Field(..., description="Clause, license statement, or proof authorizing clipping")
    allowed_derivative_uses: List[str] = Field(default=["short_form_clipping", "vertical_reframe", "burn_captions"])
    authorized_platforms: List[str] = Field(default=["tiktok", "youtube_shorts"])
    permission_expiry: Optional[str] = None
    notes: Optional[str] = None


class CampaignSourceResponse(BaseModel):
    id: str
    campaign_id: str
    source_url: str
    original_creator: str
    source_type: str
    permission_evidence: str
    allowed_derivative_uses: List[str] = Field(default_factory=list)
    authorized_platforms: List[str] = Field(default_factory=list)
    permission_expiry: Optional[str] = None
    source_verification_status: str
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SourceVerifyRequest(BaseModel):
    notes: Optional[str] = Field(default=None, description="Operator verification audit notes")


class CampaignEligibilityResult(BaseModel):
    campaign_id: str
    is_eligible: bool
    status: str
    eligible_for_production: bool
    reasons: List[str]
    checks: Dict[str, bool]


class ClipComplianceResult(BaseModel):
    clip_id: str
    campaign_id: str
    target_platform: str
    is_compliant: bool
    submission_ready: bool
    checks: Dict[str, bool]
    failure_reasons: List[str]
    details: Dict[str, Any]


class ClipSubmissionRequest(BaseModel):
    clip_id: str = Field(..., description="Clip ID")
    platform: str = Field(default="tiktok", description="Target platform: 'tiktok' or 'youtube_shorts'")
    submission_url: str = Field(..., description="Live published post URL on platform")
    submission_post_id: Optional[str] = Field(default=None, description="Platform video identifier")
    submission_evidence: Dict[str, Any] = Field(default_factory=dict, description="Screenshot URL, confirmation code, etc.")
    operator_notes: Optional[str] = None


class ClipSubmissionResponse(BaseModel):
    id: str
    clip_id: str
    campaign_id: str
    platform: str
    submitted_at: str
    submission_url: Optional[str] = None
    submission_post_id: Optional[str] = None
    submission_evidence: Dict[str, Any]
    status: str
    eligible_views: int
    estimated_rewards: float = 0.0
    pending_rewards: float
    approved_rewards: float
    rejected_rewards: float
    withdrawable_earnings: float
    evidence_source: Optional[str] = None
    evidence_url: Optional[str] = None
    verified_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    last_verified_at: Optional[str] = None
    operator_notes: Optional[str] = None
    created_at: Optional[str] = None


class ClipMetricsUpdateRequest(BaseModel):
    evidence_source: str = Field(..., description="Verified evidence source (e.g. 'content_rewards_api', 'manual_operator_audit')")
    evidence_url: Optional[str] = None
    eligible_views: Optional[int] = Field(default=None, ge=0)
    estimated_rewards: Optional[float] = Field(default=None, ge=0.0)
    pending_rewards: Optional[float] = Field(default=None, ge=0.0)
    approved_rewards: Optional[float] = Field(default=None, ge=0.0)
    rejected_rewards: Optional[float] = Field(default=None, ge=0.0)
    withdrawable_earnings: Optional[float] = Field(default=None, ge=0.0)
    status: Optional[str] = None
    rejection_reason: Optional[str] = None
    operator_notes: Optional[str] = None


class EarningsSummaryResponse(BaseModel):
    target_amount: float = 500.00
    period_start: str
    period_end: str
    total_submitted_clips: int
    total_eligible_views: int
    total_pending_rewards: float
    total_approved_rewards: float
    total_rejected_rewards: float
    total_withdrawable_earnings: float
    progress_percent: float
    days_remaining: int


# =============================================================================
# Milestone 2: Quality-First Clipping Engine & Operator Schemas
# =============================================================================

class OperatorOverviewResponse(BaseModel):
    marketplace_session: Dict[str, Any]
    campaigns: Dict[str, int]
    production_pipeline: Dict[str, Any]
    financial_metrics: Dict[str, Any]
    operator_action_queue: Dict[str, Any]


class CandidateQualityScoreRequest(BaseModel):
    candidates: List[Dict[str, Any]]
    campaign_id: Optional[str] = None
    min_select: int = 3
    max_select: int = 5


class EditingPlanRequest(BaseModel):
    campaign_id: str
    candidate: Dict[str, Any]
    source_footage_path: str = "/app/storage/footage.mp4"
    platform: str = "tiktok"
    source_authorized: bool = True
    creator_attribution: str = "Gameplay footage used with creator authorization"


class PublicationPackageRequest(BaseModel):
    campaign_id: str
    video_path: str = "/app/output/clip.mp4"
    candidate_metadata: Dict[str, Any]
    editing_plan: Dict[str, Any]
    target_platform: str = "tiktok"


class DashboardLoginRequest(BaseModel):
    api_key: Optional[str] = None
    token: Optional[str] = None


class ConfirmRewardRequest(BaseModel):
    submission_id: str
    reward_amount: float
    marketplace_ref: str
    eligible_views: Optional[int] = None
    notes: Optional[str] = None
