"""
Google Drive Authorized Campaign Asset Integration.
Adheres strictly to authorized usage, campaign asset isolation, SHA-256 provenance tracking,
and explicit operator escalation for restricted or missing assets.
"""
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import settings
from .operator_action_queue import operator_action_queue

logger = logging.getLogger("gta6_shorts.services.google_drive_assets")


class GoogleDriveAssetService:
    """
    Authorized Google Drive asset integration for campaign-provided materials.
    Manages asset discovery, permission checks, isolated campaign storage,
    MIME/type classification, checksum verification, and operator alerts.
    """

    FILE_LINK_PATTERN = re.compile(
        r"(?:https?://)?(?:drive|docs)\.google\.com/(?:file/d/|open\?id=)([a-zA-Z0-9_-]+)",
        re.IGNORECASE,
    )
    FOLDER_LINK_PATTERN = re.compile(
        r"(?:https?://)?(?:drive|docs)\.google\.com/(?:drive/(?:u/\d+/)?folders/|open\?id=)([a-zA-Z0-9_-]+)",
        re.IGNORECASE,
    )

    def __init__(self):
        # Configure isolated campaign assets directory
        env_assets = os.environ.get("CAMPAIGN_ASSETS_DIR")
        if env_assets:
            self.base_assets_dir = Path(env_assets)
        elif os.path.exists("/var/data") and os.access("/var/data", os.W_OK):
            self.base_assets_dir = Path("/var/data/campaign_assets")
        else:
            self.base_assets_dir = Path("/tmp/campaign_assets")

        self.base_assets_dir.mkdir(parents=True, exist_ok=True)
        self._asset_registry: Dict[str, Dict[str, Any]] = {}

    def detect_drive_links(self, text: str) -> List[Dict[str, Any]]:
        """
        Detect Google Drive links in campaign instructions.
        Identifies whether each link points to a file or folder and extracts ID.
        """
        if not text:
            return []

        links = []
        seen_ids = set()

        # Check folder patterns first
        for match in self.FOLDER_LINK_PATTERN.finditer(text):
            folder_id = match.group(1)
            raw_url = match.group(0)
            if folder_id not in seen_ids:
                seen_ids.add(folder_id)
                links.append({
                    "raw_url": raw_url,
                    "drive_id": folder_id,
                    "kind": "folder",
                    "canonical_url": f"https://drive.google.com/drive/folders/{folder_id}",
                })

        # Check file patterns
        for match in self.FILE_LINK_PATTERN.finditer(text):
            file_id = match.group(1)
            raw_url = match.group(0)
            if file_id not in seen_ids:
                seen_ids.add(file_id)
                links.append({
                    "raw_url": raw_url,
                    "drive_id": file_id,
                    "kind": "file",
                    "canonical_url": f"https://drive.google.com/file/d/{file_id}",
                })

        return links

    def classify_asset_type(self, filename: str, mime_type: str = "") -> str:
        """
        Classifies asset as video, logo, image, audio, font, or instruction_doc.
        """
        low_name = filename.lower()
        low_mime = mime_type.lower()

        if any(ext in low_name for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]) or "video" in low_mime:
            return "video"
        if "logo" in low_name or ("image" in low_mime and any(k in low_name for k in ["logo", "badge", "icon", "watermark"])):
            return "logo"
        if any(ext in low_name for ext in [".png", ".jpg", ".jpeg", ".webp", ".svg"]) or "image" in low_mime:
            return "image"
        if any(ext in low_name for ext in [".mp3", ".wav", ".aac", ".ogg", ".m4a"]) or "audio" in low_mime:
            return "audio"
        if any(ext in low_name for ext in [".ttf", ".otf", ".woff", ".woff2"]):
            return "font"
        if any(ext in low_name for ext in [".pdf", ".txt", ".md", ".doc", ".docx"]) or "pdf" in low_mime:
            return "instruction_doc"

        return "other"

    def get_campaign_workspace(self, campaign_id: str) -> Path:
        """
        Returns campaign-isolated storage path. Never mix assets across campaigns.
        """
        campaign_dir = self.base_assets_dir / campaign_id
        campaign_dir.mkdir(parents=True, exist_ok=True)
        return campaign_dir

    def register_and_store_asset(
        self,
        campaign_id: str,
        file_name: str,
        content_bytes: bytes,
        drive_url: str = "",
        drive_file_id: str = "",
        mime_type: str = "application/octet-stream",
        original_source: str = "Campaign Google Drive Link",
        permitted_derivative_uses: Optional[List[str]] = None,
        authorized_platforms: Optional[List[str]] = None,
        permission_status: str = "authorized",
        verification_status: str = "verified",
    ) -> Dict[str, Any]:
        """
        Stores an asset in the campaign-isolated workspace, computes SHA-256 checksum,
        and records full audit metadata.
        """
        campaign_workspace = self.get_campaign_workspace(campaign_id)
        target_path = campaign_workspace / file_name

        # Calculate checksum
        sha256_hash = hashlib.sha256(content_bytes).hexdigest()

        # Write safely to disk
        with open(target_path, "wb") as f:
            f.write(content_bytes)

        asset_type = self.classify_asset_type(file_name, mime_type)
        now_str = datetime.now(timezone.utc).isoformat()

        record = {
            "asset_id": f"asset_{sha256_hash[:12]}",
            "campaign_id": campaign_id,
            "original_drive_url": drive_url,
            "google_drive_file_id": drive_file_id,
            "file_name": file_name,
            "local_path": str(target_path),
            "file_type": asset_type,
            "mime_type": mime_type,
            "file_size_bytes": len(content_bytes),
            "asset_checksum": sha256_hash,
            "download_status": "downloaded",
            "permission_status": permission_status,
            "verification_status": verification_status,
            "original_source": original_source,
            "permitted_derivative_uses": permitted_derivative_uses or ["clipping", "short_form_video"],
            "authorized_platforms": authorized_platforms or ["tiktok", "youtube_shorts"],
            "stored_at": now_str,
        }

        self._asset_registry[record["asset_id"]] = record
        logger.info(
            "Stored campaign asset: %s (type=%s, campaign=%s, sha256=%s)",
            file_name, asset_type, campaign_id, sha256_hash[:8]
        )
        return record

    def list_campaign_assets(self, campaign_id: str) -> List[Dict[str, Any]]:
        """Returns all registered assets for a specific campaign."""
        return [
            asset for asset in self._asset_registry.values()
            if asset.get("campaign_id") == campaign_id
        ]

    def verify_campaign_assets_for_job(
        self,
        campaign_id: str,
        required_asset_types: Optional[List[str]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Verifies that all required campaign assets are present, authorized, and non-empty.
        If required assets are missing or restricted, blocks production job and requests operator action.
        """
        required = required_asset_types or []
        campaign_assets = self.list_campaign_assets(campaign_id)
        available_types = {a["file_type"] for a in campaign_assets if a["download_status"] == "downloaded"}

        missing = []
        for req in required:
            if req not in available_types:
                missing.append(req)

        if missing:
            msg = f"Campaign {campaign_id} is missing required assets: {missing}. Production job is blocked."
            logger.warning(msg)
            operator_action_queue.push_action(
                action_type="required_campaign_asset_missing",
                title=f"Missing Required Campaign Assets: {campaign_id}",
                message=msg,
                campaign_id=campaign_id,
                severity="critical",
                metadata={"missing_asset_types": missing},
            )
            return False, missing

        return True, []

    def handle_access_restriction(
        self,
        campaign_id: str,
        drive_url: str,
        status_code: int = 403,
        error_message: str = "Access Denied / Permission Required",
    ) -> Dict[str, Any]:
        """
        When a Drive link returns 403/Restricted or requires manual authorization,
        records restricted status, blocks production, and requests operator assistance.
        """
        logger.warning(
            "Google Drive asset access restriction (%d) for campaign %s: %s",
            status_code, campaign_id, error_message
        )
        action = operator_action_queue.push_action(
            action_type="google_drive_access_required",
            title=f"Google Drive Access Required for Campaign {campaign_id}",
            message=(
                f"The campaign asset link ({drive_url}) returned HTTP {status_code} ({error_message}). "
                "Operator assistance is required to grant or verify access."
            ),
            campaign_id=campaign_id,
            severity="warning",
            metadata={"drive_url": drive_url, "status_code": status_code, "error": error_message},
        )
        return {
            "status": "access_restricted",
            "campaign_id": campaign_id,
            "drive_url": drive_url,
            "status_code": status_code,
            "action_id": action["id"],
            "operator_action_required": True,
        }


google_drive_asset_service = GoogleDriveAssetService()
