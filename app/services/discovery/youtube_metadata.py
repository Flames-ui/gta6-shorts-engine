"""Resolve YouTube video metadata through the official YouTube Data API v3.

This module reads metadata only. It never returns a media URL and never fetches video files.
Without YOUTUBE_API_KEY, metadata cannot be resolved; the caller records that explicitly and
no duration, title or availability is invented.
"""
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import requests

from ...config import settings

logger = logging.getLogger("gta6_shorts.discovery.youtube_metadata")

VIDEOS_API_URL = "https://www.googleapis.com/youtube/v3/videos"
USER_AGENT = "GTA6-Shorts-Discovery/2.0"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_IDS_PER_REQUEST = 50
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
_WATCH_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})


class YouTubeMetadataError(Exception):
    """Metadata could not be resolved. `code` is a stable reason for source and item status."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def video_id_from_url(url: Optional[str]) -> Optional[str]:
    """Return the 11-character video ID from a watch, short-link or shorts URL, else None."""
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    candidate = None
    if host in _WATCH_HOSTS:
        if parts.path == "/watch":
            candidate = (parse_qs(parts.query).get("v") or [None])[0]
        else:
            segments = [s for s in parts.path.split("/") if s]
            if len(segments) >= 2 and segments[0] in ("shorts", "embed", "live"):
                candidate = segments[1]
    elif host in _SHORT_HOSTS:
        segments = [s for s in parts.path.split("/") if s]
        candidate = segments[0] if segments else None
    if candidate and VIDEO_ID_RE.match(candidate):
        return candidate
    return None


def parse_iso8601_duration(value: Optional[str]) -> float:
    """Convert an ISO 8601 duration such as PT1M5.5S into seconds. Raises ValueError when unreadable."""
    match = _DURATION_RE.match((value or "").strip())
    if not match or not any(match.groupdict().values()):
        raise ValueError(f"Unreadable duration: {value!r}")
    parts = {k: float(v) if v else 0.0 for k, v in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def _best_thumbnail(snippet: Dict[str, Any]) -> Optional[str]:
    thumbs = snippet.get("thumbnails") or {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        entry = thumbs.get(key)
        if isinstance(entry, dict) and entry.get("url"):
            return str(entry["url"])
    return None


def parse_video_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Map one videos.list item to the fields the pipeline stores. Duration is None when unreadable."""
    snippet = item.get("snippet") or {}
    content = item.get("contentDetails") or {}
    status = item.get("status") or {}
    try:
        duration: Optional[float] = parse_iso8601_duration(content.get("duration"))
    except ValueError:
        duration = None
    blocked = (content.get("regionRestriction") or {}).get("blocked") or []
    return {
        "youtube_video_id": item.get("id"),
        "title": (snippet.get("title") or "").strip() or None,
        "channel_title": (snippet.get("channelTitle") or "").strip() or None,
        "channel_id": snippet.get("channelId"),
        "published_at": snippet.get("publishedAt"),
        "thumbnail_url": _best_thumbnail(snippet),
        "duration_seconds": duration,
        "privacy_status": status.get("privacyStatus"),
        "upload_status": status.get("uploadStatus"),
        "embeddable": status.get("embeddable"),
        "region_blocked_count": len(blocked),
    }


def availability_for(meta: Dict[str, Any]) -> str:
    """Classify whether YouTube is currently serving the video to the public."""
    if meta.get("upload_status") not in (None, "processed"):
        return "unavailable"
    privacy = meta.get("privacy_status")
    if privacy == "private":
        return "private"
    if privacy in ("public", "unlisted"):
        return "available"
    return "unknown"


def fetch_video_metadata(
    video_ids: List[str],
    api_key: Optional[str] = None,
    timeout: int = 10,
) -> Dict[str, Dict[str, Any]]:
    """Return {video_id: parsed metadata} for the videos YouTube reports. Missing IDs are absent."""
    key = settings.YOUTUBE_API_KEY if api_key is None else api_key
    if not key:
        raise YouTubeMetadataError(
            "youtube_api_key_missing",
            "YOUTUBE_API_KEY is not configured, so YouTube video metadata cannot be resolved.",
        )
    ids = [v for v in video_ids if VIDEO_ID_RE.match(str(v))]
    if not ids:
        raise YouTubeMetadataError("invalid_video_id", "No valid YouTube video ID was provided.")
    if len(ids) > MAX_IDS_PER_REQUEST:
        raise YouTubeMetadataError("too_many_video_ids", f"At most {MAX_IDS_PER_REQUEST} IDs per request.")

    params = {"part": "snippet,contentDetails,status", "id": ",".join(ids), "key": key}
    try:
        resp = requests.get(VIDEOS_API_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException as exc:
        # Report the exception type only: its message can contain the request URL, which carries the key.
        raise YouTubeMetadataError("metadata_request_failed", f"YouTube Data API request failed ({type(exc).__name__}).")
    if resp.status_code != 200:
        raise YouTubeMetadataError("metadata_http_error", f"YouTube Data API returned HTTP {resp.status_code}.")

    payload = resp.json() or {}
    found: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("items") or []:
        video_id = item.get("id")
        if video_id:
            parsed = parse_video_item(item)
            parsed["availability"] = availability_for(parsed)
            found[str(video_id)] = parsed
    logger.info("discovery.metadata.fetched %s", {"requested": len(ids), "returned": len(found)})
    return found
