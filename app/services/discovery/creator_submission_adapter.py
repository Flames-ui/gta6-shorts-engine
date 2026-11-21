"""Creator-authorized submission adapter with explicit licensing terms."""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from .base import BaseSourceAdapter, DiscoveredContentItem, RightsStatus, SourceType


class CreatorSubmissionAdapter(BaseSourceAdapter):
    """Handles content submitted directly by creators with licensing terms."""

    def __init__(self):
        super().__init__(adapter_type=SourceType.CREATOR_SUBMISSION)

    def discover_content(self, source_config: Dict[str, Any], limit: int = 10) -> List[DiscoveredContentItem]:
        # Submissions are typically event-driven or queried from DB
        return []

    def create_submission(self, submission_data: Dict[str, Any]) -> DiscoveredContentItem:
        item_id = f"sub_{uuid.uuid4().hex[:10]}"
        return DiscoveredContentItem(
            id=item_id,
            source_id=submission_data.get("source_id", "creator_portal"),
            source_type=SourceType.CREATOR_SUBMISSION,
            title=submission_data["title"],
            original_url=submission_data.get("original_url"),
            media_url=submission_data.get("media_url"),
            creator_name=submission_data.get("creator_name", "Anonymous Creator"),
            creator_id=submission_data.get("creator_id"),
            category=submission_data.get("category", "gaming"),
            rights_status=RightsStatus.AUTHORIZED_EDITING if submission_data.get("terms_accepted") else RightsStatus.PENDING_VERIFICATION,
            license_type=submission_data.get("license_type", "non_exclusive_commercial"),
            allowed_platforms=submission_data.get("allowed_platforms", ["tiktok", "youtube_shorts"]),
            rights_expires_at=submission_data.get("rights_expires_at"),
            attribution_text=f"Original Creator: {submission_data.get('creator_name', 'Creator')}",
        )

    def get_metadata(self, source_url_or_id: str) -> Optional[Dict[str, Any]]:
        return {"adapter": "creator_submission", "id": source_url_or_id}

    def verify_rights(self, item: DiscoveredContentItem) -> RightsStatus:
        return item.rights_status
