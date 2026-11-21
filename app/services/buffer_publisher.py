"""Buffer social publishing integration service for TikTok, YouTube Shorts, and multi-channel distribution."""
import logging
import secrets
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import requests
from pydantic import BaseModel, Field

from ..config import settings
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.buffer")

BUFFER_API_BASE = "https://api.bufferapp.com/1"


class BufferProfile(BaseModel):
    id: str
    service: str  # tiktok, youtube, instagram, twitter, facebook, etc.
    formatted_username: str
    avatar: Optional[str] = None
    is_supported: bool = True
    disabled: bool = False


class BufferPublishingStatus:
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BufferPublisherService:
    """Manages publishing queue, account synchronization, and scheduling via Buffer API."""

    def __init__(self):
        self.client_id = settings.BUFFER_CLIENT_ID
        self.client_secret = settings.BUFFER_CLIENT_SECRET
        self.access_token = settings.BUFFER_ACCESS_TOKEN
        self.redirect_uri = settings.BUFFER_REDIRECT_URI

    def is_configured(self) -> bool:
        """Verify whether Buffer credentials are ready for operation."""
        return bool(self.access_token or (self.client_id and self.client_secret))

    def get_auth_url(self, state: str = "buffer_auth") -> str:
        """Generate OAuth 2.0 authorization URL for Buffer."""
        if not self.client_id or not self.redirect_uri:
            raise RuntimeError("BUFFER_CLIENT_ID and BUFFER_REDIRECT_URI must be configured.")

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
        }
        return f"https://buffer.com/oauth2/authorize?{urllib.parse.urlencode(params)}"

    def get_authorization_url(self, user_id: Optional[str] = None) -> Tuple[str, str]:
        """Generate cryptographically random single-use OAuth state and authorization URL."""
        state = secrets.token_urlsafe(32)
        try:
            db.save_oauth_state(
                state=state,
                user_id=user_id,
                service="buffer",
                redirect_uri=self.redirect_uri,
                expires_in_seconds=600,
            )
        except Exception as e:
            logger.warning("Could not persist OAuth state to database: %s", e)

        return self.get_auth_url(state=state), state

    def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for Buffer access token."""
        url = "https://api.bufferapp.com/1/oauth2/token.json"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        }
        resp = requests.post(url, data=data, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Buffer OAuth exchange failed: {resp.text}")
        token_data = resp.json()
        self.access_token = token_data.get("access_token")
        token_data["success"] = True
        return token_data

    def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """Wrapper method for exchange_code providing consistent success/error dict."""
        try:
            return self.exchange_code(code)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _get_headers(self) -> Dict[str, str]:
        if not self.access_token:
            raise RuntimeError("Buffer access token is not configured.")
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_connected_profiles(self) -> List[BufferProfile]:
        """Discover connected social media channels in the user's Buffer account."""
        if not self.is_configured() or not self.access_token:
            return []

        url = f"{BUFFER_API_BASE}/profiles.json"
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=15)
            if resp.status_code != 200:
                logger.error("Buffer profiles request failed: HTTP %d %s", resp.status_code, resp.text)
                return []

            profiles_data = resp.json()
            results = []
            for p in profiles_data:
                service_name = p.get("service", "unknown").lower()
                is_supported = service_name in ("tiktok", "youtube", "instagram", "facebook", "twitter")
                results.append(BufferProfile(
                    id=p.get("id"),
                    service=service_name,
                    formatted_username=p.get("formatted_username", "Unknown Channel"),
                    avatar=p.get("avatar"),
                    is_supported=is_supported,
                    disabled=p.get("disabled", False),
                ))
            return results
        except Exception as exc:
            logger.error("Error retrieving Buffer profiles: %s", exc)
            return []

    def get_user_profiles(self) -> List[Dict[str, Any]]:
        """Return connected profiles as dictionaries."""
        return [p.model_dump() for p in self.get_connected_profiles()]

    def schedule_clip_publication(
        self,
        profile_ids: Union[str, List[str]],
        text: Optional[str] = None,
        media_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        scheduled_at: Optional[Union[datetime, str]] = None,
        now: bool = False,
        clip_record: Optional[Dict[str, Any]] = None,
        caption: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit an approved clip to the Buffer queue for specified profiles (TikTok, YouTube, etc.).
        Handles flexible parameter forms and verifies approval.
        """
        effective_text = text or caption or ""
        effective_profiles: List[str] = []

        if isinstance(profile_ids, list):
            effective_profiles = profile_ids
        elif isinstance(profile_ids, str):
            effective_profiles = [profile_ids]

        if profile_id and profile_id not in effective_profiles:
            effective_profiles.append(profile_id)

        if clip_record:
            status = clip_record.get("publishing_status")
            if status not in (BufferPublishingStatus.APPROVED, "approved", BufferPublishingStatus.DRAFT, "draft"):
                raise ValueError("Clip must be approved before publishing can be initiated.")
            if not media_url:
                media_url = clip_record.get("storage_url")
            if not effective_text:
                effective_text = clip_record.get("title") or ""

        if not media_url:
            raise ValueError("A valid public media URL is required for Buffer publishing.")

        if not self.is_configured() or not self.access_token:
            # Report truthful status instead of faking success
            return {
                "success": False,
                "error": "Buffer publishing is awaiting configuration. Configure BUFFER_ACCESS_TOKEN or connect via OAuth.",
            }

        url = f"{BUFFER_API_BASE}/updates/create.json"

        # Build payload
        payload: Dict[str, Any] = {
            "text": effective_text,
            "media[video]": media_url,
        }
        if thumbnail_url:
            payload["media[thumbnail]"] = thumbnail_url

        if now:
            payload["now"] = "true"
        elif scheduled_at:
            if isinstance(scheduled_at, datetime):
                payload["scheduled_at"] = int(scheduled_at.timestamp())
            elif isinstance(scheduled_at, str):
                try:
                    dt = datetime.fromisoformat(scheduled_at)
                    payload["scheduled_at"] = int(dt.timestamp())
                except Exception:
                    payload["scheduled_at"] = scheduled_at

        for idx, pid in enumerate(effective_profiles):
            payload[f"profile_ids[{idx}]"] = pid

        try:
            resp = requests.post(url, data=payload, headers=self._get_headers(), timeout=20)
            if resp.status_code != 200:
                logger.error("Buffer update creation failed: HTTP %d %s", resp.status_code, resp.text)
                return {
                    "success": False,
                    "error": f"Buffer API error: {resp.text}",
                }

            result = resp.json()
            updates = result.get("updates", [])
            update_id = updates[0].get("id") if updates else result.get("id")
            return {
                "success": True,
                "update_id": update_id,
                "buffer_response": result,
                "status": BufferPublishingStatus.PUBLISHED if now else BufferPublishingStatus.SCHEDULED,
            }
        except Exception as exc:
            logger.error("Failed to schedule clip via Buffer: %s", exc)
            return {
                "success": False,
                "error": str(exc),
            }

    def create_scheduled_post(
        self,
        profile_id: str,
        text: str,
        media_url: str,
        scheduled_at: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Convenience method for scheduling a post for a single channel profile."""
        return self.schedule_clip_publication(
            profile_ids=[profile_id],
            text=text,
            media_url=media_url,
            scheduled_at=scheduled_at,
            **kwargs,
        )

    def get_update_status(self, update_id: str) -> Dict[str, Any]:
        """Fetch current publication status for a Buffer update."""
        url = f"{BUFFER_API_BASE}/updates/{update_id}.json"
        resp = requests.get(url, headers=self._get_headers(), timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch Buffer update status: {resp.text}")
        return resp.json()


buffer_publisher = BufferPublisherService()
