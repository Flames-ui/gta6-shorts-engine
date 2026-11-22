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
BUFFER_GRAPHQL_ENDPOINT = "https://api.buffer.com"


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


    def _resolve_token(self, user_id: Optional[str] = None) -> Optional[str]:
        """Retrieve decrypted access token for specific user, or fallback to system token."""
        if user_id:
            acc = db.get_publishing_account_for_user(user_id, "buffer")
            if acc and acc.get("access_token"):
                return acc["access_token"]
        return self.access_token

    def _get_headers_for(self, user_id: Optional[str] = None, token_override: Optional[str] = None) -> Dict[str, str]:
        token = token_override or self._resolve_token(user_id)
        if not token:
            raise RuntimeError("Buffer access token is not configured or user account is not connected.")
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def validate_platform_spec(platform: str, duration_seconds: float, aspect_ratio: str = "9:16") -> Tuple[bool, str]:
        """Validate video specifications against target platform limits."""
        p = platform.lower()
        if "tiktok" in p and duration_seconds > 60.0:
            return False, f"TikTok video duration ({duration_seconds:.1f}s) exceeds 60s Shorts limit."
        if "youtube" in p and duration_seconds > 60.0:
            return False, f"YouTube Shorts video duration ({duration_seconds:.1f}s) exceeds 60s limit."
        return True, "Compliant with platform specifications."

    def _get_headers(self) -> Dict[str, str]:
        if not self.access_token:
            raise RuntimeError("Buffer access token is not configured.")
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_connected_profiles(self, user_id: Optional[str] = None) -> List[BufferProfile]:
        """Discover connected social media channels in the user's Buffer account."""
        token = self._resolve_token(user_id)
        if not token:
            return []

        url = f"{BUFFER_API_BASE}/profiles.json"
        try:
            resp = requests.get(url, headers=self._get_headers_for(user_id=user_id), timeout=15)
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

    def get_user_profiles(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return connected profiles as dictionaries for specific user."""
        return [p.model_dump() for p in self.get_connected_profiles(user_id=user_id)]

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
        user_id: Optional[str] = None,
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
            if status not in (BufferPublishingStatus.APPROVED, "approved"):
                raise ValueError("Clip must be approved before publishing can be initiated. Draft clips cannot be scheduled or published.")
            if not media_url:
                media_url = clip_record.get("storage_url")
            if not effective_text:
                effective_text = clip_record.get("title") or ""

        if not media_url:
            raise ValueError("A valid public media URL is required for Buffer publishing.")

        token = self._resolve_token(user_id)
        if not token:
            return {
                "success": False,
                "error": "Buffer publishing is awaiting authorization. Connect your Buffer account via /api/v1/publishing/buffer/connect first.",
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
            resp = requests.post(url, data=payload, headers=self._get_headers_for(user_id=user_id), timeout=20)
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



    def sync_publishing_item_status(self, queue_item_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Check remote Buffer update status and update publishing_queue and generated_clips accordingly."""
        item = db.get_publishing_queue_item(queue_item_id)

        if not item or not item.get("platform_update_id"):
            return {"status": "unknown", "synced": False, "error": "Queue item or platform_update_id not found"}

        update_id = item["platform_update_id"]
        token = self._resolve_token(user_id or item.get("user_id"))
        if not token:
            return {"status": item.get("status"), "synced": False, "error": "Awaiting Buffer authorization"}

        try:
            url = f"{BUFFER_API_BASE}/updates/{update_id}.json"
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=12)
            if resp.status_code != 200:
                return {"status": item.get("status"), "synced": False, "error": f"Buffer HTTP {resp.status_code}"}

            data = resp.json()
            buffer_status = data.get("status", "buffer").lower()
            new_status = "scheduled"
            if buffer_status == "sent":
                new_status = "published"
            elif buffer_status in ("failed", "error"):
                new_status = "failed"

            db.update_publishing_queue_item(queue_item_id, {"status": new_status})
            if new_status == "published" and item.get("clip_id"):
                db.update_clip(item["clip_id"], {"publishing_status": "published"})
            return {"status": new_status, "synced": True, "buffer_status": buffer_status}
        except Exception as exc:
            return {"status": item.get("status"), "synced": False, "error": str(exc)}



    # -------------------------------------------------------------------------
    # Buffer GraphQL Integration (Official API for Personal Automations)
    # -------------------------------------------------------------------------
    def execute_graphql(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        timeout: int = 15,
    ) -> Dict[str, Any]:
        """Execute a GraphQL query or mutation against Buffer's official GraphQL API."""
        resolved_token = token or self.access_token
        if not resolved_token:
            raise RuntimeError("Buffer API key / access token is not configured.")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {resolved_token}",
            "User-Agent": "GTA6-Shorts-Engine/2.0",
        }
        payload = {
            "query": query,
            "variables": variables or {},
        }
        try:
            resp = requests.post(BUFFER_GRAPHQL_ENDPOINT, json=payload, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                logger.error("Buffer GraphQL HTTP %d error: %s", resp.status_code, resp.text)
                return {
                    "errors": [{"message": f"Buffer GraphQL API HTTP {resp.status_code}: {resp.text}"}],
                    "data": None,
                }
            return resp.json()
        except requests.RequestException as e:
            logger.error("Buffer GraphQL request failed: %s", e)
            return {"errors": [{"message": f"Network error contacting Buffer: {str(e)}"}], "data": None}

    def verify_and_fetch_account_info(self, api_key: str) -> Dict[str, Any]:
        """
        Validate a supplied Buffer API key and retrieve authenticated account,
        organizations, and connected channels using Buffer's GraphQL API.
        """
        if not api_key or not api_key.strip():
            return {"valid": False, "error": "API key cannot be empty."}

        clean_key = api_key.strip()

        # Step 1: Query account and organizations
        account_query = """
        query GetAccountAndOrganizations {
          account {
            id
            email
            name
            organizations {
              id
              name
              ownerEmail
            }
          }
        }
        """
        result = self.execute_graphql(account_query, token=clean_key)
        errors = result.get("errors")
        if errors:
            err_msg = errors[0].get("message", "Unknown GraphQL error")
            logger.warning("Buffer API key verification failed: %s", err_msg)
            return {"valid": False, "error": err_msg}

        data = result.get("data")
        if not data or not data.get("account"):
            return {"valid": False, "error": "Invalid Buffer credentials or empty account returned."}

        account = data["account"]
        orgs_raw = account.get("organizations") or []
        organizations = []
        all_channels = []

        # Step 2: Fetch channels for each organization
        channels_query = """
        query GetChannels($organizationId: String!) {
          channels(input: { organizationId: $organizationId }) {
            id
            name
            displayName
            service
            avatar
            isQueuePaused
          }
        }
        """

        for org in orgs_raw:
            org_id = org.get("id")
            org_channels = []
            if org_id:
                ch_result = self.execute_graphql(channels_query, variables={"organizationId": org_id}, token=clean_key)
                if ch_result.get("data") and ch_result["data"].get("channels"):
                    for ch in ch_result["data"]["channels"]:
                        channel_info = {
                            "id": ch.get("id"),
                            "name": ch.get("name") or "Unnamed Channel",
                            "display_name": ch.get("displayName") or ch.get("name"),
                            "service": (ch.get("service") or "unknown").lower(),
                            "avatar": ch.get("avatar"),
                            "is_queue_paused": bool(ch.get("isQueuePaused", False)),
                            "organization_id": org_id,
                            "organization_name": org.get("name"),
                            "capabilities": {
                                "video_upload": True,
                                "scheduling": True,
                                "auto_posting": not bool(ch.get("isQueuePaused", False)),
                            },
                        }
                        org_channels.append(channel_info)
                        all_channels.append(channel_info)

            organizations.append({
                "id": org_id,
                "name": org.get("name") or "Personal Workspace",
                "owner_email": org.get("ownerEmail"),
                "channels": org_channels,
            })

        return {
            "valid": True,
            "account": {
                "id": account.get("id"),
                "email": account.get("email"),
                "name": account.get("name"),
            },
            "organizations": organizations,
            "channels": all_channels,
        }

    def connect_with_api_key(self, api_key: str, user_id: str = "system_admin") -> Dict[str, Any]:
        """
        Validate, encrypt, and store a Buffer API key, associating retrieved
        channels with the specified operator. Never logs or leaks the raw key.
        """
        verification = self.verify_and_fetch_account_info(api_key)
        if not verification.get("valid"):
            return {
                "success": False,
                "error": verification.get("error", "Failed to validate Buffer API key with GraphQL API."),
            }

        account = verification["account"]
        channels = verification["channels"]
        organizations = verification["organizations"]
        now_iso = datetime.now(timezone.utc).isoformat()

        # Update in-memory token
        self.access_token = api_key.strip()

        # Securely persist publishing account with encrypted token
        account_record = {
            "id": f"buffer_{account['id']}",
            "user_id": user_id,
            "service": "buffer",
            "account_identifier": account.get("email") or account.get("id"),
            "access_token": api_key.strip(),  # Automatically encrypted by db.save_publishing_account
            "is_active": True,
            "metadata": {
                "account_id": account["id"],
                "name": account.get("name"),
                "email": account.get("email"),
                "organizations": organizations,
                "channels": channels,
                "channels_count": len(channels),
                "api_type": "graphql",
                "last_sync_at": now_iso,
            },
        }
        db.save_publishing_account(account_record)

        # Upsert connected channels into database
        saved_channels = []
        for ch in channels:
            ch_data = {
                "id": f"buffer_{ch['id']}",
                "user_id": user_id,
                "service": "buffer",
                "account_id": f"buffer_{account['id']}",
                "channel_identifier": ch["id"],
                "name": ch["display_name"] or ch["name"],
                "status": "connected" if not ch["is_queue_paused"] else "paused",
                "metadata": {
                    "service_type": ch["service"],
                    "avatar": ch.get("avatar"),
                    "organization_id": ch.get("organization_id"),
                    "organization_name": ch.get("organization_name"),
                    "is_queue_paused": ch["is_queue_paused"],
                    "capabilities": ch["capabilities"],
                },
            }
            db.create_channel(ch_data)
            saved_channels.append(ch_data)

        logger.info("Successfully connected Buffer account %s with %d channels", account.get("email"), len(channels))

        return {
            "success": True,
            "connected": True,
            "status": "connected",
            "account": account,
            "account_id": account.get("id"),
            "account_name": account.get("name"),
            "account_email": account.get("email"),
            "organizations": organizations,
            "channels": channels,
            "channel_count": len(channels),
            "channels_discovered": len(channels),
            "total_channels": len(channels),
            "last_sync_at": now_iso,
        }

    def get_connection_status(self, user_id: str = "system_admin") -> Dict[str, Any]:
        """
        Retrieve truthful connection status, authenticated account identity,
        and connected channels for Buffer. Never returns raw secret keys.
        """
        acc = db.get_publishing_account_for_user(user_id, "buffer")
        if not acc and self.access_token:
            # Check if environment or memory has access_token configured
            acc = {"account_identifier": "environment_configured", "metadata": {"api_type": "bearer_token"}}

        if not acc:
            return {
                "connected": False,
                "status": "disconnected",
                "account_id": None,
                "account_name": None,
                "channel_count": 0,
                "channels_count": 0,
                "account": None,
                "organizations": [],
                "channels": [],
                "last_sync_at": None,
            }

        metadata = acc.get("metadata") or {}
        channels_db = db.list_channels(user_id=user_id)
        buffer_channels = [c for c in channels_db if c.get("service") == "buffer"]

        # Format channels for response
        channels_formatted = []
        if buffer_channels:
            for c in buffer_channels:
                meta = c.get("metadata") or {}
                channels_formatted.append({
                    "id": c.get("channel_identifier") or c.get("id"),
                    "name": c.get("name") or c.get("channel_name"),
                    "service": meta.get("service_type") or "unknown",
                    "avatar": meta.get("avatar"),
                    "status": c.get("status", "connected"),
                    "is_queue_paused": meta.get("is_queue_paused", False),
                    "capabilities": meta.get("capabilities", {"video_upload": True, "scheduling": True}),
                })
        else:
            channels_formatted = metadata.get("channels") or []

        account_id_val = metadata.get("account_id") or (metadata.get("account") or {}).get("id") or acc.get("account_identifier")
        account_name_val = metadata.get("name") or (metadata.get("account") or {}).get("name") or "Buffer User"
        ch_count = len(channels_formatted)

        return {
            "connected": True,
            "status": "connected",
            "account_id": account_id_val,
            "account_name": account_name_val,
            "channel_count": ch_count,
            "channels_count": ch_count,
            "account": {
                "id": account_id_val,
                "email": metadata.get("email") or acc.get("account_identifier"),
                "name": account_name_val,
            },
            "organizations": metadata.get("organizations") or [],
            "channels": channels_formatted,
            "last_sync_at": metadata.get("last_sync_at"),
            "api_type": metadata.get("api_type", "graphql"),
        }

    def disconnect(self, user_id: str = "system_admin") -> Dict[str, Any]:
        """Disconnect Buffer integration and deactivate stored credentials."""
        self.access_token = None
        acc = db.get_publishing_account_for_user(user_id, "buffer")
        if acc:
            db.delete_publishing_account(acc["id"])

        # Also remove associated buffer channels
        channels_db = db.list_channels(user_id=user_id)
        for c in channels_db:
            if c.get("service") == "buffer":
                db.delete_channel(c["id"])

        logger.info("Buffer integration disconnected for user %s", user_id)
        return {"success": True, "connected": False, "status": "disconnected"}


buffer_publisher = BufferPublisherService()

