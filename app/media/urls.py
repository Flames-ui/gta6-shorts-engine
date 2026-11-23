"""YouTube URL validation and normalisation.

Only HTTPS URLs on known YouTube hosts with a well-formed 11-character video id
are accepted. Playlists, channels, and every other host are rejected before
any network access happens, which is the SSRF guard for ingestion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from ..errors import StudioError

MAX_URL_LENGTH = 2048
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
WATCH_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
NOCOOKIE_HOSTS = {"youtube-nocookie.com", "www.youtube-nocookie.com"}
SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
PATH_PREFIXES = ("shorts", "embed", "live", "v")

_INVALID = "Enter a full YouTube video link, for example https://www.youtube.com/watch?v=VIDEO_ID."


@dataclass(frozen=True)
class YouTubeRef:
    video_id: str
    normalized_url: str


def _invalid(message: str = _INVALID, code: str = "invalid_youtube_url") -> StudioError:
    return StudioError(message, code=code, status_code=422)


def parse_youtube_url(raw: str) -> YouTubeRef:
    if not isinstance(raw, str):
        raise _invalid()
    value = raw.strip()
    if not value or len(value) > MAX_URL_LENGTH or any(ord(c) < 33 for c in value):
        raise _invalid()
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise _invalid() from exc

    if parts.scheme.lower() != "https" or parts.username or parts.password or port not in (None, 443):
        raise _invalid()

    video_id = None
    path = parts.path or "/"
    segments = [s for s in path.split("/") if s]

    if host in SHORT_HOSTS:
        if len(segments) == 1:
            video_id = segments[0]
    elif host in WATCH_HOSTS:
        if path.rstrip("/") in ("/watch", "") or segments == ["watch"]:
            query = parse_qs(parts.query, keep_blank_values=False)
            values = query.get("v", [])
            video_id = values[0] if len(values) == 1 else None
        elif len(segments) == 2 and segments[0] in PATH_PREFIXES:
            video_id = segments[1]
    elif host in NOCOOKIE_HOSTS:
        if len(segments) == 2 and segments[0] == "embed":
            video_id = segments[1]
    else:
        raise _invalid("Only YouTube video links are supported.", code="unsupported_host")

    if video_id is None or not VIDEO_ID_RE.match(video_id):
        if host in WATCH_HOSTS and segments and segments[0] == "playlist":
            raise _invalid("Playlist links are not supported. Paste a single video link.", code="unsupported_url")
        raise _invalid()

    return YouTubeRef(video_id=video_id, normalized_url=f"https://www.youtube.com/watch?v={video_id}")
