"""YouTube Data API v3 OAuth integration and upload service."""
import logging
import os
import secrets
from typing import Any, Dict, Optional
from ..config import settings
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.youtube")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


class YouTubePublisherService:
    def __init__(self):
        self.client_id = settings.YOUTUBE_CLIENT_ID
        self.client_secret = settings.YOUTUBE_CLIENT_SECRET
        self.redirect_uri = settings.YOUTUBE_REDIRECT_URI

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def get_auth_url(self, state: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, str]:
        """Generate Google OAuth 2.0 authorization URL with single-use persisted state."""
        if not self.is_configured():
            raise RuntimeError(
                "YouTube OAuth is not configured. Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, "
                "and YOUTUBE_REDIRECT_URI in environment variables."
            )

        if not state:
            state = secrets.token_urlsafe(32)
            try:
                db.save_oauth_state(
                    state=state,
                    user_id=user_id,
                    service="youtube",
                    redirect_uri=self.redirect_uri,
                    expires_in_seconds=600,
                )
            except Exception as e:
                logger.warning("Could not persist YouTube OAuth state: %s", e)

        from google_auth_oauthlib.flow import Flow

        client_config = {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.redirect_uri],
            }
        }
        flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=self.redirect_uri)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        return {"authorization_url": auth_url, "state": state}

    def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for refresh and access tokens."""
        if not self.is_configured():
            raise RuntimeError("YouTube OAuth is not configured.")

        from google_auth_oauthlib.flow import Flow

        client_config = {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.redirect_uri],
            }
        }
        flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=self.redirect_uri)
        flow.fetch_token(code=code)
        creds = flow.credentials

        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }
        logger.info("Successfully exchanged YouTube OAuth code for credentials")
        return token_data

    def publish_clip(
        self,
        clip_id: str,
        video_file_path: str,
        credentials_dict: Dict[str, Any],
        privacy_status: str = "private",
    ) -> Dict[str, Any]:
        """
        Uploads a generated short to YouTube.
        Always defaults to 'private' privacy status. Requires explicit user approval.
        """
        clip = db.get_clip(clip_id)
        if not clip:
            raise ValueError(f"Clip {clip_id} not found")

        if clip.get("publishing_status") != "approved":
            raise ValueError("Clip must be manually approved before publishing to YouTube.")

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = Credentials(
            token=credentials_dict.get("token"),
            refresh_token=credentials_dict.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=SCOPES,
        )

        youtube = build("youtube", "v3", credentials=creds)

        title = clip.get("title", "GTA 6 Short")[:100]
        description = (clip.get("description", "") + "\n\n" + " ".join(clip.get("hashtags", [])))[:5000]
        tags = [t.lstrip("#") for t in clip.get("hashtags", [])]

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "20",  # Gaming
            },
            "status": {
                "privacyStatus": privacy_status,  # Default 'private'
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(video_file_path, chunksize=-1, resumable=True, mimetype="video/mp4")

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        db.update_clip(clip_id, {"publishing_status": "publishing"})

        response = request.execute()
        yt_video_id = response.get("id")
        published_url = f"https://www.youtube.com/shorts/{yt_video_id}"

        db.update_clip(clip_id, {
            "publishing_status": "published",
            "clip_url": published_url,
        })

        return {
            "clip_id": clip_id,
            "youtube_id": yt_video_id,
            "url": published_url,
            "privacy_status": privacy_status,
        }



    def get_channel_identity(self, credentials_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Retrieve verified channel identity from YouTube Data API v3."""
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials(
                token=credentials_dict.get("token"),
                refresh_token=credentials_dict.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=SCOPES,
            )
            youtube = build("youtube", "v3", credentials=creds)
            res = youtube.channels().list(part="snippet,status,contentDetails", mine=True).execute()
            items = res.get("items", [])
            if not items:
                return {"verified": False, "error": "No YouTube channel found for authenticated Google account."}
            channel = items[0]
            snippet = channel.get("snippet", {})
            return {
                "verified": True,
                "channel_id": channel.get("id"),
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "custom_url": snippet.get("customUrl"),
                "published_at": snippet.get("publishedAt"),
                "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                "privacy_status": channel.get("status", {}).get("privacyStatus", "public"),
            }
        except Exception as e:
            logger.error("Failed to retrieve YouTube channel identity: %s", e)
            return {"verified": False, "error": str(e)}

    def check_processing_status(self, video_id: str, credentials_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Reconcile YouTube video upload and processing status via videos.list."""
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials(
                token=credentials_dict.get("token"),
                refresh_token=credentials_dict.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=SCOPES,
            )
            youtube = build("youtube", "v3", credentials=creds)
            res = youtube.videos().list(part="snippet,status,processingDetails", id=video_id).execute()
            items = res.get("items", [])
            if not items:
                return {"found": False, "status": "not_found", "video_id": video_id}
            vid = items[0]
            status_obj = vid.get("status", {})
            proc_obj = vid.get("processingDetails", {})

            upload_status = status_obj.get("uploadStatus")
            processing_status = proc_obj.get("processingStatus")
            privacy_status = status_obj.get("privacyStatus")

            is_ready = upload_status == "processed" or processing_status == "succeeded"
            is_failed = upload_status in ("rejected", "failed") or processing_status in ("failed", "terminated")

            return {
                "found": True,
                "video_id": video_id,
                "upload_status": upload_status,
                "processing_status": processing_status,
                "privacy_status": privacy_status,
                "rejection_reason": status_obj.get("rejectionReason"),
                "failure_reason": status_obj.get("failureReason"),
                "is_ready": is_ready,
                "is_failed": is_failed,
                "title": vid.get("snippet", {}).get("title"),
            }
        except Exception as e:
            logger.error("Failed to check YouTube video processing status: %s", e)
            return {"found": False, "error": str(e), "video_id": video_id}

    def get_status(self, user_id: str = "system_admin") -> Dict[str, Any]:
        """Return truthful connection and readiness status for YouTube integration."""
        acc = db.get_publishing_account_for_user(user_id, "youtube")

        is_configured = self.is_configured()

        if not acc:
            return {
                "connected": False,
                "status": "disconnected",
                "oauth_configured": is_configured,
                "channel_id": None,
                "channel_title": None,
                "upload_authorized": False,
                "upload_permission_granted": False,
                "has_refresh_token": False,
                "scopes": [],
                "readiness": {
                    "oauth_ready": is_configured,
                    "channel_discovered": False,
                    "upload_permission": False,
                    "refresh_token_valid": False,
                },
            }

        meta = acc.get("metadata") or {}
        scopes = meta.get("scopes", [])
        has_upload_scope = any("youtube.upload" in s for s in scopes) or any("youtube" in s for s in scopes)
        has_refresh = bool(acc.get("refresh_token_encrypted") or acc.get("refresh_token"))

        return {
            "connected": True,
            "status": "connected",
            "oauth_configured": is_configured,
            "account_id": acc.get("id"),
            "channel_id": meta.get("channel_id") or acc.get("account_identifier"),
            "channel_title": meta.get("channel_title") or acc.get("account_identifier") or "YouTube Channel",
            "custom_url": meta.get("custom_url"),
            "thumbnail": meta.get("thumbnail"),
            "upload_authorized": has_upload_scope,
            "upload_permission_granted": has_upload_scope,
            "has_refresh_token": has_refresh,
            "default_privacy": "private",
            "scopes": scopes,
            "readiness": {
                "oauth_ready": True,
                "channel_discovered": bool(meta.get("channel_id") or acc.get("account_identifier")),
                "upload_permission": has_upload_scope,
                "refresh_token_valid": has_refresh,
            },
        }
    def disconnect(self, user_id: str = "system_admin") -> Dict[str, Any]:
        """Disconnect YouTube integration."""
        acc = db.get_publishing_account_for_user(user_id, "youtube")
        if acc:
            db.delete_publishing_account(acc["id"])
        channels_db = db.list_channels(user_id=user_id)
        for c in channels_db:
            if c.get("service") == "youtube":
                db.delete_channel(c["id"])
        return {"success": True, "connected": False, "status": "disconnected"}


youtube_publisher = YouTubePublisherService()
