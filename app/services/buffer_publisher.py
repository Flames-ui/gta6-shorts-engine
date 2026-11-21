import secrets
"""Buffer social publishing integration service for TikTok, YouTube Shorts, and multi-channel distribution."""
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import requests
from pydantic import BaseModel, Field

from ..config import settings

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
        return token_data

    def _get_headers(self) -> Dict[str, str]:
        if not self.access_token:
            raise RuntimeError("Buffer access token is not configured.")
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_connected_profiles(self) -> List[BufferProfile]:
        """Discover connected social media channels in the user's Buffer account."""
        if not self.is_configured():
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

    def schedule_clip_publication(
        self,
        clip_record: Dict[str, Any],
        profile_ids: List[str],
        text: str,
        media_url: str,
        thumbnail_url: Optional[str] = None,
        scheduled_at: Optional[datetime] = None,
        now: bool = False,
    ) -> Dict[str, Any]:
        """
        Submit an approved clip to the Buffer queue for specified profiles (TikTok, YouTube, etc.).
        Enforces human approval before publishing.
        """
        if clip_record.get("publishing_status") != BufferPublishingStatus.APPROVED:
            raise ValueError("Clip must be approved by an authorized user before publishing can be initiated.")

        if not media_url:
            raise ValueError("A valid public media URL (from Supabase Storage) is required for Buffer publishing.")

        if not self.is_configured():
            raise RuntimeError("Buffer publishing is awaiting configuration. Configure BUFFER_ACCESS_TOKEN.")

        url = f"{BUFFER_API_BASE}/updates/create.json"

        # Build payload
        payload: Dict[str, Any] = {
            "text": text,
            "media[video]": media_url,
        }
        if thumbnail_url:
            payload["media[thumbnail]"] = thumbnail_url

        if now:
            payload["now"] = "true"
        elif scheduled_at:
            payload["scheduled_at"] = int(scheduled_at.timestamp())

        for idx, pid in enumerate(profile_ids):
            payload[f"profile_ids[{idx}]"] = pid

        try:
            resp = requests.post(url, data=payload, headers=self._get_headers(), timeout=20)
            if resp.status_code != 200:
                logger.error("Buffer update creation failed: HTTP %d %s", resp.status_code, resp.text)
                raise RuntimeError(f"Buffer API error: {resp.text}")

            result = resp.json()
            return {
                "success": True,
                "buffer_response": result,
                "status": BufferPublishingStatus.PUBLISHED if now else BufferPublishingStatus.SCHEDULED,
            }
        except Exception as exc:
            logger.error("Failed to schedule clip via Buffer: %s", exc)
            raise

    def get_update_status(self, update_id: str) -> Dict[str, Any]:
        """Fetch current publication status for a Buffer update."""
        url = f"{BUFFER_API_BASE}/updates/{update_id}.json"
        resp = requests.get(url, headers=self._get_headers(), timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch Buffer update status: {resp.text}")
        return resp.json()


buffer_publisher = BufferPublisherService()


# Compatibility and helper methods
def _get_authorization_url(self) -> Tuple[str, str]:
    state = secrets.token_urlsafe(16)
    return self.get_auth_url(state=state), state

def _exchange_code_for_token(self, code: str) -> Dict[str, Any]:
    return self.exchange_code(code)

def _get_user_profiles(self) -> List[Any]:
    return [p.model_dump() for p in self.get_connected_profiles()]

def _create_scheduled_post(self, profile_id: str, text: str, media_url: str, scheduled_at: Optional[str] = None) -> Dict[str, Any]:
    return self.schedule_clip_publication(profile_id=profile_id, caption=text, media_url=media_url, scheduled_at=scheduled_at)

BufferPublisherService.get_authorization_url = _get_authorization_url
BufferPublisherService.exchange_code_for_token = _exchange_code_for_token
BufferPublisherService.get_user_profiles = _get_user_profiles
BufferPublisherService.create_scheduled_post = _create_scheduled_post
