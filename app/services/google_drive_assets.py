"""
Google Drive Authorized Campaign Asset Integration.
Adheres strictly to authorized usage, campaign asset isolation, SHA-256 provenance tracking,
real Google Drive API v3 operations, path-traversal prevention, and explicit operator escalation.
"""
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..config import settings
from .operator_action_queue import operator_action_queue
from .security import sanitize_filename

logger = logging.getLogger("gta6_shorts.services.google_drive_assets")


class GoogleDriveAssetService:
    """
    Authorized Google Drive asset integration for campaign-provided materials.
    Manages authentic Drive API v3 discovery, folder listing, real file downloads,
    permission checks, isolated campaign storage, checksum verification, and operator alerts.
    """

    FILE_LINK_PATTERN = re.compile(
        r"(?:https?://)?(?:drive|docs)\.google\.com/(?:file/d/|open\?id=)([a-zA-Z0-9_-]+)",
        re.IGNORECASE,
    )
    FOLDER_LINK_PATTERN = re.compile(
        r"(?:https?://)?(?:drive|docs)\.google\.com/(?:drive/(?:u/\d+/)?folders/|open\?id=)([a-zA-Z0-9_-]+)",
        re.IGNORECASE,
    )

    DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
    OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

    def __init__(self):
        env_assets = os.environ.get("CAMPAIGN_ASSETS_DIR")
        if env_assets:
            self.base_assets_dir = Path(env_assets)
        elif os.path.exists("/var/data") and os.access("/var/data", os.W_OK):
            self.base_assets_dir = Path("/var/data/campaign_assets")
        else:
            self.base_assets_dir = Path("/tmp/campaign_assets")

        self.base_assets_dir.mkdir(parents=True, exist_ok=True)
        self._asset_registry: Dict[str, Dict[str, Any]] = {}

    def get_drive_access_token(self) -> Optional[str]:
        """
        Retrieves valid Google Drive OAuth access token from environment,
        configured project secret, or refresh token exchange.
        """
        direct_token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN")
        if direct_token:
            return direct_token.strip()

        client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID")
        client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET")
        refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN")

        if client_id and client_secret and refresh_token:
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(
                        self.OAUTH_TOKEN_URL,
                        data={
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "refresh_token": refresh_token,
                            "grant_type": "refresh_token",
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        return data.get("access_token")
                    logger.warning("Drive OAuth token refresh failed (%d): %s", resp.status_code, resp.text)
            except Exception as exc:
                logger.error("Exception refreshing Google Drive token: %s", exc)

        return None

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
        Classifies asset as video, logo, image, audio, font, instruction_doc, or other.
        """
        low_name = filename.lower()
        low_mime = (mime_type or "").lower()

        if any(ext in low_name for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]) or "video" in low_mime:
            return "video"
        if "logo" in low_name or ("image" in low_mime and any(k in low_name for k in ["logo", "badge", "icon", "watermark"])):
            return "logo"
        if any(ext in low_name for ext in [".png", ".jpg", ".jpeg", ".webp", ".svg"]) or "image" in low_mime:
            return "image"
        if any(ext in low_name for ext in [".mp3", ".wav", ".aac", ".ogg", ".m4a"]) or "audio" in low_mime:
            return "audio"
        if any(ext in low_name for ext in [".ttf", ".otf", ".woff", ".woff2"]) or "font" in low_mime:
            return "font"
        if any(ext in low_name for ext in [".pdf", ".txt", ".md", ".doc", ".docx"]) or "pdf" in low_mime:
            return "instruction_doc"

        return "other"

    def get_campaign_workspace(self, campaign_id: str) -> Path:
        """
        Returns campaign-isolated storage path. Never mix assets across campaigns.
        """
        safe_camp_id = sanitize_filename(campaign_id)
        campaign_dir = self.base_assets_dir / safe_camp_id
        campaign_dir.mkdir(parents=True, exist_ok=True)
        return campaign_dir

    def sanitize_asset_filename(self, file_name: str, campaign_id: str) -> Tuple[str, Path]:
        """
        Strictly sanitizes asset filename and prevents path traversal attacks.
        Ensures destination path is contained entirely within the campaign workspace.
        """
        if not file_name or not file_name.strip():
            raise ValueError("Asset filename cannot be empty.")

        raw_name = file_name.strip()
        if ".." in raw_name or "/" in raw_name or "\\" in raw_name or raw_name.startswith("/"):
            raise ValueError(f"Path traversal attempt detected in asset filename: {file_name}")

        clean_name = sanitize_filename(raw_name)
        if not clean_name or clean_name in (".", ".."):
            clean_name = f"asset_{int(datetime.now().timestamp())}.bin"

        workspace = self.get_campaign_workspace(campaign_id).resolve()
        target_path = (workspace / clean_name).resolve()

        if os.path.commonpath([str(workspace), str(target_path)]) != str(workspace):
            raise ValueError(f"Path traversal attempt detected in asset filename: {file_name}")

        return clean_name, target_path

    def list_folder_contents(
        self,
        folder_id: str,
        campaign_id: str,
        access_token: Optional[str] = None,
    ) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """
        Lists actual Google Drive folder contents using Google Drive API v3.
        Returns (success, files_list, error_message).
        """
        token = access_token or self.get_drive_access_token()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.DRIVE_API_BASE}/files"
        params = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "files(id, name, mimeType, size, md5Checksum)",
            "pageSize": 100,
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                res = client.get(url, headers=headers, params=params)

                if res.status_code in (401, 403):
                    err_msg = f"Google Drive API access restricted ({res.status_code})"
                    self.handle_access_restriction(
                        campaign_id=campaign_id,
                        drive_url=f"https://drive.google.com/drive/folders/{folder_id}",
                        status_code=res.status_code,
                        error_message=res.text[:200],
                    )
                    return False, [], err_msg

                if res.status_code != 200:
                    err_msg = f"Failed to list folder contents ({res.status_code}): {res.text[:200]}"
                    logger.warning(err_msg)
                    return False, [], err_msg

                data = res.json()
                items = []
                for f in data.get("files", []):
                    f_name = f.get("name", "unnamed_asset")
                    m_type = f.get("mimeType", "")
                    items.append({
                        "file_id": f.get("id"),
                        "name": f_name,
                        "mime_type": m_type,
                        "file_type": self.classify_asset_type(f_name, m_type),
                        "size_bytes": int(f.get("size", 0)),
                        "md5_checksum": f.get("md5Checksum"),
                    })

                logger.info("Listed %d assets from Drive folder %s for campaign %s", len(items), folder_id, campaign_id)
                return True, items, None

        except Exception as exc:
            err_msg = f"Error querying Drive folder {folder_id}: {exc}"
            logger.error(err_msg)
            return False, [], err_msg

    def download_drive_file(
        self,
        campaign_id: str,
        file_id: str,
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        permission_evidence: Optional[Dict[str, Any]] = None,
        access_token: Optional[str] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Downloads real authorized file content via Google Drive API v3.
        Stores real source metadata, file IDs, SHA-256 checksums and permission evidence.
        Never fabricates downloaded bytes.
        """
        token = access_token or self.get_drive_access_token()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # 1. Fetch metadata if file_name or mime_type is omitted
        actual_name = file_name
        actual_mime = mime_type
        if not actual_name or not actual_mime:
            try:
                with httpx.Client(timeout=15.0) as client:
                    meta_res = client.get(
                        f"{self.DRIVE_API_BASE}/files/{file_id}?fields=id,name,mimeType,size",
                        headers=headers,
                    )
                    if meta_res.status_code == 200:
                        mdata = meta_res.json()
                        actual_name = actual_name or mdata.get("name")
                        actual_mime = actual_mime or mdata.get("mimeType")
            except Exception as exc:
                logger.warning("Could not pre-fetch metadata for Drive file %s: %s", file_id, exc)

        actual_name = actual_name or f"drive_asset_{file_id}.bin"
        actual_mime = actual_mime or "application/octet-stream"

        # 2. Download actual binary media
        download_url = f"{self.DRIVE_API_BASE}/files/{file_id}?alt=media"
        drive_link = f"https://drive.google.com/file/d/{file_id}"

        try:
            with httpx.Client(timeout=60.0) as client:
                res = client.get(download_url, headers=headers)

                if res.status_code in (401, 403):
                    self.handle_access_restriction(
                        campaign_id=campaign_id,
                        drive_url=drive_link,
                        status_code=res.status_code,
                        error_message=res.text[:200],
                    )
                    return False, None, f"Google Drive download restricted (HTTP {res.status_code})"

                if res.status_code != 200:
                    err_msg = f"Failed to download Drive file {file_id} (HTTP {res.status_code})"
                    logger.warning(err_msg)
                    return False, None, err_msg

                content_bytes = res.content
                if not content_bytes:
                    return False, None, f"Drive file {file_id} is empty (0 bytes received)."

                # 3. Store through verified storage routine
                record = self.register_and_store_asset(
                    campaign_id=campaign_id,
                    file_name=actual_name,
                    content_bytes=content_bytes,
                    drive_url=drive_link,
                    drive_file_id=file_id,
                    mime_type=actual_mime,
                    original_source=f"Google Drive File {file_id}",
                    permission_evidence=permission_evidence,
                )
                return True, record, None

        except Exception as exc:
            err_msg = f"Exception downloading Google Drive file {file_id}: {exc}"
            logger.error(err_msg)
            return False, None, err_msg

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
        permission_evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Stores genuine asset bytes in the campaign-isolated workspace.
        Computes SHA-256 checksum and preserves permission evidence.
        Does not automatically mark assets authorized without evidence.
        """
        if not content_bytes or len(content_bytes) == 0:
            raise ValueError("Cannot register an empty or fabricated asset. Real content bytes are required.")

        clean_filename, target_path = self.sanitize_asset_filename(file_name, campaign_id)

        # Write to isolated workspace
        with open(target_path, "wb") as f:
            f.write(content_bytes)

        sha256_hash = hashlib.sha256(content_bytes).hexdigest()
        asset_type = self.classify_asset_type(clean_filename, mime_type)
        now_str = datetime.now(timezone.utc).isoformat()

        # Permission verification check:
        # Never default to authorized without evidence!
        is_verified = False
        perm_status = "unverified"
        verif_status = "pending_verification"

        if permission_evidence and isinstance(permission_evidence, dict):
            if permission_evidence.get("has_explicit_permission") or permission_evidence.get("license_type"):
                is_verified = True
                perm_status = "authorized"
                verif_status = "verified"

        record = {
            "asset_id": f"asset_{sha256_hash[:12]}",
            "campaign_id": campaign_id,
            "original_drive_url": drive_url,
            "google_drive_file_id": drive_file_id,
            "file_name": clean_filename,
            "local_path": str(target_path),
            "file_type": asset_type,
            "mime_type": mime_type,
            "file_size_bytes": len(content_bytes),
            "asset_checksum": sha256_hash,
            "download_status": "downloaded",
            "permission_status": perm_status,
            "verification_status": verif_status,
            "is_authorized": is_verified,
            "permission_evidence": permission_evidence or {},
            "original_source": original_source,
            "permitted_derivative_uses": permitted_derivative_uses or ["clipping", "short_form_video"],
            "authorized_platforms": authorized_platforms or ["tiktok", "youtube_shorts"],
            "stored_at": now_str,
        }

        self._asset_registry[record["asset_id"]] = record
        logger.info(
            "Registered authentic campaign asset: %s (type=%s, campaign=%s, size=%d bytes, sha256=%s, verified=%s)",
            clean_filename, asset_type, campaign_id, len(content_bytes), sha256_hash[:8], is_verified
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
        require_verified_permission: bool = True,
    ) -> Tuple[bool, List[str]]:
        """
        Verifies that all required campaign assets are present on disk, non-empty,
        and have verified permissions.
        If required assets are missing, unverified, or inaccessible, blocks production job
        and requests operator action.
        """
        required = required_asset_types or []
        campaign_assets = self.list_campaign_assets(campaign_id)

        available_types = set()
        unverified_types = set()

        for a in campaign_assets:
            lpath = a.get("local_path")
            if lpath and os.path.isfile(lpath) and os.path.getsize(lpath) > 0:
                if require_verified_permission and not a.get("is_authorized"):
                    unverified_types.add(a.get("file_type"))
                else:
                    available_types.add(a.get("file_type"))

        missing = []
        for req in required:
            if req not in available_types:
                if req in unverified_types:
                    missing.append(f"{req} (unverified permission)")
                else:
                    missing.append(f"{req} (missing from campaign workspace)")

        if missing:
            msg = f"Campaign {campaign_id} cannot enter production: required assets missing or unverified: {missing}."
            logger.warning(msg)
            operator_action_queue.push_action(
                action_type="required_campaign_asset_missing",
                title=f"Missing/Unverified Campaign Assets: {campaign_id}",
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
