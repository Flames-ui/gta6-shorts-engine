"""Rights status verification and compliance enforcement."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from .base import DiscoveredContentItem, RightsStatus

logger = logging.getLogger("gta6_shorts.rights")


class RightsManager:
    """Enforces legal reuse compliance before content can enter processing."""

    def __init__(self):
        # In-memory permissions store by source_id
        self._source_permissions: Dict[str, Dict[str, Any]] = {}

    def set_permission(
        self,
        source_id: str,
        status: RightsStatus,
        allowed_platforms: Optional[List[str]] = None,
        license_type: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        notes: Optional[str] = None,
    ):
        """Record verified permission for a content source."""
        self._source_permissions[source_id] = {
            "rights_status": status,
            "allowed_platforms": allowed_platforms or ["tiktok", "youtube_shorts"],
            "license_type": license_type,
            "expires_at": expires_at,
            "notes": notes,
            "updated_at": datetime.now(timezone.utc),
        }
        logger.info("Updated rights permission for source %s: %s", source_id, status.value)

    def check_rights_for_production(self, source_id: str, target_platform: str = "tiktok") -> Tuple[bool, str]:
        """Check if source has verified rights for production."""
        perm = self._source_permissions.get(source_id)
        if not perm:
            return False, f"No rights permission record found for source '{source_id}'."

        status = perm["rights_status"]
        if status == RightsStatus.REJECTED:
            return False, "Content reuse authorization was explicitly rejected."

        if status == RightsStatus.PENDING_VERIFICATION:
            return False, "Content is pending verification. Cannot process without confirmed authorization."

        if perm.get("expires_at"):
            if perm["expires_at"] < datetime.now(timezone.utc):
                return False, f"Content licensing authorization expired on {perm['expires_at'].isoformat()}."

        if status == RightsStatus.AUTHORIZED_PLATFORMS:
            allowed = [p.lower() for p in perm.get("allowed_platforms", [])]
            if target_platform.lower() not in allowed:
                return False, f"Platform '{target_platform}' is not authorized in licensing."

        if status in (RightsStatus.AUTHORIZED_EDITING, RightsStatus.AUTHORIZED_PLATFORMS):
            return True, "Authorized for video editing and derivative production."

        return False, f"Unrecognized rights status: '{status}'."

    @staticmethod
    def is_authorized_for_production(item: DiscoveredContentItem, target_platform: str = "tiktok") -> Tuple[bool, str]:
        """
        Validate whether content item possesses verified reuse rights for production.
        Rejects unverified, expired, or rejected content.
        """
        if item.rights_status == RightsStatus.REJECTED:
            return False, "Content reuse authorization was explicitly rejected."

        if item.rights_status == RightsStatus.PENDING_VERIFICATION:
            return False, "Content is awaiting rights verification. Cannot process without confirmed authorization."

        if item.rights_expires_at:
            now_utc = datetime.now(timezone.utc)
            if item.rights_expires_at < now_utc:
                return False, f"Content licensing authorization expired on {item.rights_expires_at.isoformat()}."

        if item.rights_status == RightsStatus.AUTHORIZED_PLATFORMS:
            if target_platform.lower() not in [p.lower() for p in item.allowed_platforms]:
                return False, f"Content authorization does not include target platform '{target_platform}'."

        if item.rights_status in (RightsStatus.AUTHORIZED_EDITING, RightsStatus.AUTHORIZED_PLATFORMS):
            return True, "Authorized for video editing and short-form derivative production."

        return False, f"Unrecognized rights status: '{item.rights_status}'."


rights_manager = RightsManager()
