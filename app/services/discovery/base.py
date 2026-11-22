"""Base classes and interfaces for content discovery adapters."""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RightsStatus(str, Enum):
    PENDING_VERIFICATION = "pending_verification"
    AUTHORIZED_EDITING = "authorized_editing"
    AUTHORIZED_PLATFORMS = "authorized_platforms"
    REJECTED = "rejected"
    EXPIRED = "expired"


# Statuses that grant reuse rights. Every other status keeps the source inactive and blocks production.
AUTHORIZED_RIGHTS_STATUSES = frozenset({RightsStatus.AUTHORIZED_EDITING, RightsStatus.AUTHORIZED_PLATFORMS})


class SourceType(str, Enum):
    OFFICIAL_API = "official_api"
    APPROVED_RSS = "approved_rss"
    CREATOR_SUBMISSION = "creator_submission"
    DIRECT_UPLOAD = "direct_upload"
    CUSTOM = "custom"


class DiscoveredContentItem(BaseModel):
    id: str
    source_id: str
    source_type: SourceType
    title: str
    original_url: Optional[str] = None
    media_url: Optional[str] = None
    creator_name: Optional[str] = None
    creator_id: Optional[str] = None
    category: str = "gaming"
    publication_date: Optional[datetime] = None
    discovery_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    rights_status: RightsStatus = RightsStatus.PENDING_VERIFICATION
    license_type: Optional[str] = None
    allowed_platforms: List[str] = Field(default_factory=lambda: ["tiktok", "youtube_shorts"])
    rights_expires_at: Optional[datetime] = None
    attribution_text: Optional[str] = None
    duration_seconds: Optional[float] = None
    content_hash: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseSourceAdapter(ABC):
    """Abstract base class for all content discovery sources."""

    def __init__(self, adapter_type: SourceType):
        self.adapter_type = adapter_type

    @abstractmethod
    def discover_content(self, source_config: Dict[str, Any], limit: int = 10) -> List[DiscoveredContentItem]:
        """Discover new content from this source."""
        pass

    @abstractmethod
    def get_metadata(self, source_url_or_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve rich source metadata."""
        pass

    @abstractmethod
    def verify_rights(self, item: DiscoveredContentItem) -> RightsStatus:
        """Verify the legal reuse permissions for this item."""
        pass
