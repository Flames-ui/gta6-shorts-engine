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
        original_creator: Optional[str] = None,
        source_url: Optional[str] = None,
        permission_evidence: Optional[str] = None,
        derivative_rights_allowed: bool = True,
        revoked_at: Optional[datetime] = None,
    ):
        """
        Record verified permission for a content source and persist to database.
        FAIL-CLOSED: If database persistence fails, authorization is rejected and not cached.
        """
        status_val = status.value if isinstance(status, RightsStatus) else str(status)
        platforms = allowed_platforms or ["tiktok", "youtube_shorts"]
        expires_str = expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at
        revoked_str = revoked_at.isoformat() if isinstance(revoked_at, datetime) else revoked_at

        # No "id" here: source_permissions.id is a UUID primary key with DEFAULT gen_random_uuid().
        # The database keeps the existing id on conflict (source_id), so re-saving never changes it.
        record_data = {
            "source_id": source_id,
            "rights_status": status_val,
            "license_type": license_type,
            "allowed_platforms": platforms,
            "expires_at": expires_str,
            "verification_notes": notes,
            "verified_by": verified_by,
            "attribution_requirements": attribution_requirements,
            "original_creator": original_creator,
            "source_url": source_url,
            "permission_evidence": permission_evidence,
            "derivative_rights_allowed": derivative_rights_allowed,
            "revoked_at": revoked_str,
        }

        # Persist to database first (Fail-Closed)
        try:
            db.set_source_permission(source_id, record_data)
        except Exception as exc:
            logger.critical("Database failure while saving rights permission for source %s: %s", source_id, exc)
            self._source_permissions.pop(source_id, None)
            raise RuntimeError(f"Database failure saving rights permission for source '{source_id}'. Authorization blocked.") from exc

        # Update in-memory cache only after successful persistence
        self._source_permissions[source_id] = {
            "rights_status": status if isinstance(status, RightsStatus) else RightsStatus(status_val),
            "allowed_platforms": platforms,
            "license_type": license_type,
            "expires_at": expires_at,
            "notes": notes,
            "verified_by": verified_by,
            "attribution_requirements": attribution_requirements,
            "original_creator": original_creator,
            "source_url": source_url,
            "permission_evidence": permission_evidence,
            "derivative_rights_allowed": derivative_rights_allowed,
            "revoked_at": revoked_at,
            "updated_at": datetime.now(timezone.utc),
        }
        logger.info("Persisted verified rights for source %s: %s", source_id, status_val)

    def revoke_permission(self, source_id: str, reason: str = "Revoked by administrator", verified_by: Optional[str] = None):
        """Immediately revoke content reuse permission for a source (takes effect instantly)."""
        now = datetime.now(timezone.utc)
        self.set_permission(
            source_id=source_id,
            status=RightsStatus.REJECTED,
            notes=reason,
            verified_by=verified_by,
            derivative_rights_allowed=False,
            revoked_at=now,
        )
        logger.warning("Rights revoked immediately for source %s: %s", source_id, reason)

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

    def check_rights_for_production(self, source_id: Optional[str], target_platform: str = "tiktok") -> Tuple[bool, str]:
        """Check if source has verified rights for production (checking cache & database)."""
        if not source_id:
            return False, "Missing source ID: verified content rights require an authorized source identifier."
        perm = self.get_permission(source_id)
        if not perm:
            return False, f"No rights permission record found for source '{source_id}'."

        if perm.get("revoked_at"):
            return False, f"Content reuse authorization was revoked on {perm['revoked_at']}."

        if not perm.get("derivative_rights_allowed", True):
            return False, "Derivative editing rights are not authorized for this source."

        status = perm["rights_status"]
        if status == RightsStatus.REJECTED:
            return False, "Content reuse authorization was explicitly rejected or revoked."

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
