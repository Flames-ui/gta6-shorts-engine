"""Authorized direct media upload adapter."""
import os
import uuid
from typing import Any, Dict, List, Optional
from ..security import sanitize_filename
from .base import BaseSourceAdapter, DiscoveredContentItem, RightsStatus, SourceType


class DirectUploadAdapter(BaseSourceAdapter):
    """Handles directly ingested video files stored in persistent storage."""

    def __init__(self):
        super().__init__(adapter_type=SourceType.DIRECT_UPLOAD)

    def discover_content(self, source_config: Dict[str, Any], limit: int = 10) -> List[DiscoveredContentItem]:
        return []

    def ingest_uploaded_file(
        self,
        filename: str,
        storage_url: str,
        user_id: str,
        category: str = "gaming",
        rights_granted: bool = True,
        title: Optional[str] = None,
    ) -> DiscoveredContentItem:
        safe_name = sanitize_filename(filename)
        item_id = f"upload_{uuid.uuid4().hex[:10]}"
        return DiscoveredContentItem(
            id=item_id,
            source_id=f"user_{user_id}",
            source_type=SourceType.DIRECT_UPLOAD,
            title=title or os.path.splitext(safe_name)[0],
            media_url=storage_url,
            category=category,
            rights_status=RightsStatus.AUTHORIZED_EDITING if rights_granted else RightsStatus.PENDING_VERIFICATION,
            attribution_text="Direct Upload by Verified User",
        )

    def get_metadata(self, source_url_or_id: str) -> Optional[Dict[str, Any]]:
        return {"adapter": "direct_upload", "id": source_url_or_id}

    def verify_rights(self, item: DiscoveredContentItem) -> RightsStatus:
        return item.rights_status
