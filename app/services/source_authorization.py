"""Source-video authorization service managing permissions and derivative rights."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..schemas import SourceVerificationStatus
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.source_authorization")


class SourceAuthorizationService:
    """
    Manages source footage permissions, licensing evidence, and campaign associations.
    Guarantees that no footage enters production without documented permission and
    explicit operator verification.
    """

    def register_source(
        self,
        campaign_id: str,
        source_url: str,
        original_creator: str,
        source_type: str,
        permission_evidence: str,
        allowed_derivative_uses: Optional[List[str]] = None,
        authorized_platforms: Optional[List[str]] = None,
        permission_expiry: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Register a new source footage record associated with a campaign.
        Strict Safety Rule: ALL sources begin as 'pending_verification'.
        No automatic verification based on source_type or label.
        """
        source_id = f"csrc_{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()

        # Enforce non-vague evidence input
        evidence_str = (permission_evidence or "").strip()
        if len(evidence_str) < 5:
            logger.warning("Source %s registered with vague or minimal permission evidence", source_id)

        source_record = {
            "id": source_id,
            "campaign_id": campaign_id,
            "source_url": source_url,
            "original_creator": original_creator,
            "source_type": source_type,
            "permission_evidence": evidence_str,
            "allowed_derivative_uses": allowed_derivative_uses or ["short_form_clipping"],
            "authorized_platforms": authorized_platforms or ["tiktok"],
            "permission_expiry": permission_expiry,
            "source_verification_status": SourceVerificationStatus.PENDING_VERIFICATION.value,
            "verified_by": None,
            "verified_at": None,
            "notes": notes,
            "created_at": now_str,
            "updated_at": now_str,
        }

        created = db.create_campaign_source(source_record)
        logger.info(
            "Registered source %s for campaign %s (type: %s, status: %s). Requires operator verification.",
            source_id, campaign_id, source_type, SourceVerificationStatus.PENDING_VERIFICATION.value
        )
        return created

    def operator_verify_source(
        self,
        source_id: str,
        verified_by: str,
        notes: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Explicit operator verification of source footage.
        Validates evidence quality, platform authorizations, and expiry before granting 'verified'.
        """
        source = db.get_campaign_source(source_id)
        if not source:
            return False, f"Source footage record {source_id} not found.", None

        # 1. Evidence Quality Check
        evidence = str(source.get("permission_evidence") or "").strip()
        if len(evidence) < 10 or evidence.lower() in ["none", "n/a", "ok", "yes", "trailer", "public"]:
            return False, "Verification Rejected: Permission evidence is missing, vague, or insufficient.", source

        # 2. Platforms Check
        platforms = source.get("authorized_platforms") or []
        if not platforms:
            return False, "Verification Rejected: No authorized platforms specified for this footage.", source

        # 3. Derivative Uses Check
        uses = source.get("allowed_derivative_uses") or []
        if "short_form_clipping" not in uses and "clipping" not in uses:
            return False, "Verification Rejected: Footage does not permit derivative short-form clipping.", source

        # 4. Expiration Check
        expiry = source.get("permission_expiry")
        if expiry:
            try:
                now = datetime.now(timezone.utc)
                expiry_dt = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
                if expiry_dt < now:
                    return False, f"Verification Rejected: Source permission has already expired on {expiry}.", source
            except Exception:
                pass

        now_str = datetime.now(timezone.utc).isoformat()
        updates = {
            "source_verification_status": SourceVerificationStatus.VERIFIED.value,
            "verified_by": verified_by,
            "verified_at": now_str,
            "updated_at": now_str,
        }
        if notes:
            existing_notes = source.get("notes") or ""
            updates["notes"] = f"{existing_notes} [Verified by {verified_by}: {notes}]".strip()

        updated = db.update_campaign_source(source_id, updates)
        logger.info("Source %s verified by operator %s", source_id, verified_by)
        return True, "Source footage verified by operator.", updated

    def verify_source(
        self,
        source_id: str,
        campaign_id: str,
        target_platform: str = "tiktok",
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Runtime check that source footage has active operator-verified permission
        for the given campaign and target platform.
        """
        source = db.get_campaign_source(source_id)
        if not source:
            return False, f"Source footage record {source_id} not found.", {}

        # 1. Campaign Association Check
        if str(source.get("campaign_id")) != str(campaign_id):
            return False, f"Source {source_id} is associated with campaign {source.get('campaign_id')}, not {campaign_id}.", source

        # 2. Verification Status Check
        status = source.get("source_verification_status")
        if status != SourceVerificationStatus.VERIFIED.value:
            return False, f"Source permission has status '{status}' (operator verification required).", source

        # 3. Platform Authorization Check
        authorized_platforms = [p.lower() for p in (source.get("authorized_platforms") or [])]
        if target_platform.lower() not in authorized_platforms:
            return False, f"Source does not authorize clipping for platform '{target_platform}'.", source

        # 4. Derivative Use Permission Check
        allowed_uses = source.get("allowed_derivative_uses") or []
        if "short_form_clipping" not in allowed_uses and "clipping" not in allowed_uses:
            return False, "Source does not authorize derivative short-form clipping.", source

        # 5. Expiration Check
        expiry = source.get("permission_expiry")
        if expiry:
            try:
                now = datetime.now(timezone.utc)
                expiry_dt = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
                if expiry_dt < now:
                    return False, f"Source permission expired on {expiry}.", source
            except Exception:
                pass

        return True, "Source footage verified and authorized for production.", source


source_authorization_service = SourceAuthorizationService()
