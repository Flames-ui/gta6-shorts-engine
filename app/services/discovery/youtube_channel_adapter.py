"""YouTube channel discovery for content sources.

A channel URL is resolved to a channel ID and read from YouTube's public upload feed
(https://www.youtube.com/feeds/videos.xml?channel_id=...). That feed needs no API key and does
not need a separate RSS URL on the source.

Handle and legacy username URLs (/@name, /c/name, /user/name) cannot be turned into a channel ID
without the YouTube Data API, so they require YOUTUBE_API_KEY. Without it they fail with an explicit
error recorded on the source; they are never skipped silently.
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from ...config import settings
from ..security import safe_requests_get
from .base import BaseSourceAdapter, DiscoveredContentItem, RightsStatus, SourceType

logger = logging.getLogger("gta6_shorts.discovery.youtube")

FEED_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
DATA_API_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
USER_AGENT = "GTA6-Shorts-Discovery/2.0"
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
ATOM_NS = "{http://www.w3.org/2005/Atom}"
YT_NS = "{http://www.youtube.com/xml/schemas/2015}"

# Source types that are YouTube channels. Other types are never routed to the YouTube adapter.
YOUTUBE_SOURCE_TYPES = frozenset({"youtube", "youtube_channel"})


class YouTubeDiscoveryError(Exception):
    """A channel could not be polled. `code` is a stable reason for logs and source health."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_channel_url(channel_url: Optional[str]) -> Tuple[str, str]:
    """Return ("channel_id", "UC..."), ("handle", "@name") or ("username", "name")."""
    raw = (channel_url or "").strip()
    if not raw:
        raise YouTubeDiscoveryError("missing_channel_url", "The source has no channel URL.")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise YouTubeDiscoveryError("unsupported_channel_url", f"Channel URL cannot be parsed: {exc}") from exc

    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in YOUTUBE_HOSTS:
        raise YouTubeDiscoveryError("unsupported_channel_url", "Channel URL must be a youtube.com channel URL.")

    segments = [s for s in parts.path.split("/") if s]
    if len(segments) >= 2 and segments[0] == "channel" and CHANNEL_ID_RE.match(segments[1]):
        return ("channel_id", segments[1])
    if segments and segments[0].startswith("@") and len(segments[0]) > 1:
        return ("handle", segments[0])
    if len(segments) >= 2 and segments[0] in ("c", "user"):
        return ("username", segments[1])
    raise YouTubeDiscoveryError(
        "unsupported_channel_url",
        "Channel URL must look like youtube.com/channel/UC... or youtube.com/@handle.",
    )


def resolve_channel_id(kind: str, value: str) -> str:
    """Return the channel ID. Only /channel/UC... URLs resolve without the Data API."""
    if kind == "channel_id":
        return value

    api_key = settings.YOUTUBE_API_KEY
    if not api_key:
        raise YouTubeDiscoveryError(
            "channel_id_unresolved",
            f"{value} is a handle URL and needs YOUTUBE_API_KEY to resolve its channel ID. "
            "Use a youtube.com/channel/UC... URL or configure the key.",
        )

    params = {"part": "id", "key": api_key}
    params["forHandle" if kind == "handle" else "forUsername"] = value
    try:
        resp = requests.get(DATA_API_CHANNELS_URL, params=params, timeout=10)
    except requests.RequestException as exc:
        # Report the exception type only: the message can contain the request URL, which carries the key.
        raise YouTubeDiscoveryError("channel_lookup_failed", f"YouTube Data API request failed ({type(exc).__name__}).")
    if resp.status_code != 200:
        raise YouTubeDiscoveryError("channel_lookup_failed", f"YouTube Data API returned HTTP {resp.status_code}.")

    items = (resp.json() or {}).get("items") or []
    channel_id = (items[0] or {}).get("id") if items else None
    if not channel_id or not CHANNEL_ID_RE.match(str(channel_id)):
        raise YouTubeDiscoveryError("channel_id_unresolved", f"No YouTube channel was found for {value}.")
    return str(channel_id)


def fetch_channel_feed(channel_id: str, timeout: int = 12) -> bytes:
    url = FEED_URL_TEMPLATE.format(channel_id=channel_id)
    try:
        resp = safe_requests_get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    except Exception as exc:
        raise YouTubeDiscoveryError("feed_request_failed", f"Channel feed request failed ({type(exc).__name__}).")
    if resp.status_code == 404:
        raise YouTubeDiscoveryError("channel_not_found", f"Channel feed for {channel_id} was not found (HTTP 404).")
    if resp.status_code != 200:
        raise YouTubeDiscoveryError("feed_http_error", f"Channel feed returned HTTP {resp.status_code}.")
    return resp.content


def _parse_published(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_channel_feed(xml_bytes: bytes, limit: int = 10) -> Tuple[str, List[Dict[str, Any]]]:
    """Return (channel_title, videos). Entries without a valid video ID are skipped."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise YouTubeDiscoveryError("feed_parse_error", f"Channel feed is not valid XML ({exc}).")
    if root.tag != f"{ATOM_NS}feed":
        raise YouTubeDiscoveryError("feed_parse_error", "Channel feed is not an Atom feed.")

    channel_title = (root.findtext(f"{ATOM_NS}author/{ATOM_NS}name") or "Unknown YouTube Creator").strip()
    videos: List[Dict[str, Any]] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        video_id = (entry.findtext(f"{YT_NS}videoId") or "").strip()
        if not VIDEO_ID_RE.match(video_id):
            continue
        videos.append({
            "video_id": video_id,
            "title": (entry.findtext(f"{ATOM_NS}title") or "Untitled video").strip(),
            "published": _parse_published(entry.findtext(f"{ATOM_NS}published")),
        })
        if len(videos) >= limit:
            break
    return channel_title, videos


class YouTubeChannelAdapter(BaseSourceAdapter):
    """Discovers recent uploads of a YouTube channel from its channel URL."""

    def __init__(self):
        super().__init__(adapter_type=SourceType.OFFICIAL_API)

    def resolve_channel_id(self, source_config: Dict[str, Any]) -> str:
        kind, value = parse_channel_url(source_config.get("channel_url"))
        return resolve_channel_id(kind, value)

    def discover_content(
        self,
        source_config: Dict[str, Any],
        limit: int = 10,
        rights_status: RightsStatus = RightsStatus.PENDING_VERIFICATION,
    ) -> List[DiscoveredContentItem]:
        """Raise YouTubeDiscoveryError when the channel cannot be read. Never returns fake items."""
        channel_id = self.resolve_channel_id(source_config)
        channel_title, videos = parse_channel_feed(fetch_channel_feed(channel_id), limit=limit)

        items: List[DiscoveredContentItem] = []
        for video in videos:
            url = f"https://www.youtube.com/watch?v={video['video_id']}"
            items.append(DiscoveredContentItem(
                id=f"yt_{video['video_id']}",
                source_id=str(source_config.get("id")),
                source_type=SourceType.OFFICIAL_API,
                title=video["title"],
                original_url=url,
                media_url=None,
                creator_name=channel_title,
                category=source_config.get("category_id") or "gaming",
                publication_date=video["published"],
                rights_status=rights_status,
                attribution_text=f"Source: {channel_title} ({url})",
                metadata={"channel_id": channel_id, "youtube_video_id": video["video_id"]},
            ))
        return items

    def get_metadata(self, source_url_or_id: str) -> Optional[Dict[str, Any]]:
        return {"adapter": "youtube_channel", "source": source_url_or_id}

    def verify_rights(self, item: DiscoveredContentItem) -> RightsStatus:
        return item.rights_status
