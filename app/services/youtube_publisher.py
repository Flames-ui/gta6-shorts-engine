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


youtube_publisher = YouTubePublisherService()
