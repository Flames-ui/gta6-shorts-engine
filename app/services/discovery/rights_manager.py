"""Rights status verification and compliance enforcement with persistent storage."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..supabase_client import db
from .base import DiscoveredContentItem, RightsStatus

logger = logging.getLogger("gta6_shorts.rights")


class RightsManager:
    """Enforces legal reuse compliance before content can enter processing."""

    def __init__(self):
        # In-memory permissions cache by source_id
        self._source_permissions: Dict[str, Dict[str, Any]] = {}

    def set_permission(
        self,
        source_id: str,
        status: RightsStatus,
        allowed_platforms: Optional[List[str]] = None,
        license_type: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        notes: Optional[str] = None,
        verified_by: Optional[str] = None,
        attribution_requirements: Optional[str] = None,
    ):
        """Record verified permission for a content source and persist to database."""
        status_val = status.value if isinstance(status, RightsStatus) else str(status)
        platforms = allowed_platforms or ["tiktok", "youtube_shorts"]
        expires_str = expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at

        # Update in-memory cache
        self._source_permissions[source_id] = {
            "rights_status": status if isinstance(status, RightsStatus) else RightsStatus(status_val),
            "allowed_platforms": platforms,
            "license_type": license_type,
            "expires_at": expires_at,
            "notes": notes,
            "verified_by": verified_by,
            "attribution_requirements": attribution_requirements,
            "updated_at": datetime.now(timezone.utc),
        }

        # Persist to database
        try:
            db.set_source_permission(source_id, {
                "id": f"perm_{source_id}",
                "source_id": source_id,
                "rights_status": status_val,
                "license_type": license_type,
                "allowed_platforms": platforms,
                "expires_at": expires_str,
                "verification_notes": notes,
                "verified_by": verified_by,
                "attribution_requirements": attribution_requirements,
            })
            logger.info("Persisted rights permission for source %s: %s", source_id, status_val)
        except Exception as e:
            logger.error("Failed to persist rights permission for source %s: %s", source_id, e)

    def get_permission(self, source_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve permission from cache or persistent database."""
        if source_id in self._source_permissions:
            return self._source_permissions[source_id]

        try:
            stored = db.get_source_permission(source_id)
            if stored:
                raw_st = stored.get("rights_status", "pending_verification")
                try:
                    enum_st = RightsStatus(raw_st)
                except ValueError:
                    enum_st = RightsStatus.PENDING_VERIFICATION

                exp_dt = None
                if stored.get("expires_at"):
                    try:
                        exp_dt = datetime.fromisoformat(stored["expires_at"])
                    except Exception:
                        pass

                cached = {
                    "rights_status": enum_st,
                    "allowed_platforms": stored.get("allowed_platforms", ["tiktok", "youtube_shorts"]),
                    "license_type": stored.get("license_type"),
                    "expires_at": exp_dt,
                    "notes": stored.get("verification_notes"),
                    "verified_by": stored.get("verified_by"),
                    "attribution_requirements": stored.get("attribution_requirements"),
                    "updated_at": datetime.now(timezone.utc),
                }
                self._source_permissions[source_id] = cached
                return cached
        except Exception as e:
            logger.error("Error retrieving rights permission for source %s: %s", source_id, e)

        return None

    def check_rights_for_production(self, source_id: str, target_platform: str = "tiktok") -> Tuple[bool, str]:
        """Check if source has verified rights for production (checking cache & database)."""
        perm = self.get_permission(source_id)
        if not perm:
            return False, f"No rights permission record found for source '{source_id}'."

        status = perm["rights_status"]
        if status == RightsStatus.REJECTED:
            return False, "Content reuse authorization was explicitly rejected."

        if status == RightsStatus.PENDING_VERIFICATION:
            return False, "Content is pending verification. Cannot process without confirmed authorization."

        if perm.get("expires_at"):
            exp = perm["expires_at"]
            if isinstance(exp, str):
                try:
                    exp = datetime.fromisoformat(exp)
                except Exception:
                    pass
            if isinstance(exp, datetime):
                # Ensure timezone awareness
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < datetime.now(timezone.utc):
                    return False, f"Content licensing authorization expired on {exp.isoformat()}."

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
            exp = item.rights_expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                return False, f"Content licensing authorization expired on {exp.isoformat()}."

        if item.rights_status == RightsStatus.AUTHORIZED_PLATFORMS:
            if target_platform.lower() not in [p.lower() for p in item.allowed_platforms]:
                return False, f"Content authorization does not include target platform '{target_platform}'."

        if item.rights_status in (RightsStatus.AUTHORIZED_EDITING, RightsStatus.AUTHORIZED_PLATFORMS):
            return True, "Authorized for video editing and short-form derivative production."

        return False, f"Unrecognized rights status: '{item.rights_status}'."


rights_manager = RightsManager()
