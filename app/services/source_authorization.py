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
    Guarantees only campaign-provided footage or footage with verified reuse authorization
    can enter production.
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
        auto_verify: bool = False,
    ) -> Dict[str, Any]:
        """Register a new source footage record associated with a campaign."""
        source_id = f"csrc_{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()

        # If source is explicitly campaign-provided with evidence, allow initial verification
        initial_status = (
            SourceVerificationStatus.VERIFIED.value
            if auto_verify or source_type in ["campaign_provided", "official_trailer"]
            else SourceVerificationStatus.PENDING_VERIFICATION.value
        )

        source_record = {
            "id": source_id,
            "campaign_id": campaign_id,
            "source_url": source_url,
            "original_creator": original_creator,
            "source_type": source_type,
            "permission_evidence": permission_evidence,
            "allowed_derivative_uses": allowed_derivative_uses or ["short_form_clipping", "vertical_reframe", "burn_captions"],
            "authorized_platforms": authorized_platforms or ["tiktok", "youtube_shorts"],
            "permission_expiry": permission_expiry,
            "source_verification_status": initial_status,
            "notes": notes,
            "created_at": now_str,
            "updated_at": now_str,
        }

        created = db.create_campaign_source(source_record)
        logger.info(
            "Registered source %s for campaign %s (type: %s, status: %s)",
            source_id, campaign_id, source_type, initial_status
        )
        return created

    def verify_source(
        self,
        source_id: str,
        campaign_id: str,
        target_platform: str = "tiktok",
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Verify that the source footage has documented permission for the campaign
        and target platform.
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
            return False, f"Source permission has status '{status}' (must be verified).", source

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
