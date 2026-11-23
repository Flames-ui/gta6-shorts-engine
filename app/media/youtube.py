"""YouTube retrieval through yt-dlp, gated by policy.

Retrieval only runs when YOUTUBE_RETRIEVAL_ENABLED=true, which operators should
enable only for videos whose rights they hold. This module never attempts to
bypass age gates, sign-in, DRM, CAPTCHA or other access controls: when yt-dlp
reports one of those, the job fails with an explicit category.
"""
from __future__ import annotations

import glob
import logging
import os
import time
from typing import Any, Callable, Dict, Optional, Tuple

from ..config import settings
from ..errors import PipelineError, RetrievalError
from .urls import YouTubeRef

logger = logging.getLogger("studio.youtube")

# (substring in yt-dlp message, category, retryable)
_CLASSIFY = [
    ("private video", "private", False),
    ("this video is private", "private", False),
    ("premium members", "members_only", False),
    ("members-only content", "members_only", False),
    ("members-only", "members_only", False),
    ("members only", "members_only", False),
    ("age-restricted", "age_restricted", False),
    ("confirm your age", "age_restricted", False),
    ("sign in to confirm", "access_restricted", False),
    ("not a bot", "access_restricted", False),
    ("not available in your country", "geo_restricted", False),
    ("in your country", "geo_restricted", False),
    ("geo", "geo_restricted", False),
    ("this live event", "live_stream", False),
    ("premieres", "live_stream", False),
    ("is live", "live_stream", False),
    ("video unavailable", "unavailable", False),
    ("has been removed", "unavailable", False),
    ("been terminated", "unavailable", False),
    ("copyright", "copyright_restricted", False),
    ("http error 429", "rate_limited", True),
    ("timed out", "network_timeout", True),
    ("timeout", "network_timeout", True),
    ("connection", "network_error", True),
    ("temporarily", "network_error", True),
]


def classify_ytdlp_error(message: str) -> Tuple[str, bool]:
    text = (message or "").lower()
    for needle, category, retryable in _CLASSIFY:
        if needle in text:
            return category, retryable
    return "unknown_retrieval_error", False


def ensure_enabled() -> None:
    if not settings.YOUTUBE_RETRIEVAL_ENABLED:
        raise RetrievalError(
            "YouTube retrieval is turned off on this server. Upload the video file instead.",
            category="retrieval_disabled",
        )


def _ydl_options(extra: Dict[str, Any]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": settings.YTDLP_RETRIES,
        "fragment_retries": settings.YTDLP_RETRIES,
        "extractor_retries": 1,
        "ignoreerrors": False,
        "no_color": True,
    }
    opts.update(extra)
    return opts


def inspect(ref: YouTubeRef) -> Dict[str, Any]:
    """Fetch metadata only (no media download) and enforce duration and availability policy."""
    ensure_enabled()
    import yt_dlp  # imported lazily so the rest of the app runs without it

    try:
        with yt_dlp.YoutubeDL(_ydl_options({"skip_download": True})) as ydl:
            info = ydl.extract_info(ref.normalized_url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        category, retryable = classify_ytdlp_error(str(exc))
        raise RetrievalError(_retrieval_message(category), category=category, retryable=retryable) from exc
    except Exception as exc:  # unexpected extractor failure
        raise RetrievalError("The video could not be read right now. Try again later.",
                             category="network_error", retryable=True) from exc

    if not info:
        raise RetrievalError(_retrieval_message("unavailable"), category="unavailable")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise RetrievalError(_retrieval_message("live_stream"), category="live_stream")
    duration = info.get("duration")
    if not duration:
        raise RetrievalError("The video length could not be determined.", category="duration_unknown")
    if float(duration) > settings.MAX_SOURCE_DURATION_SECONDS:
        raise RetrievalError(
            f"This video is {float(duration) / 60:.0f} minutes long. The limit is "
            f"{settings.MAX_SOURCE_DURATION_SECONDS // 60} minutes.",
            category="duration_exceeds_limit",
        )
    return {
        "video_id": info.get("id") or ref.video_id,
        "title": (info.get("title") or "")[:300] or None,
        "channel_title": (info.get("channel") or info.get("uploader") or "")[:200] or None,
        "duration": float(duration),
        "availability": info.get("availability"),
        "thumbnail": info.get("thumbnail"),
    }


def _retrieval_message(category: str) -> str:
    return {
        "private": "This video is private, so it cannot be retrieved.",
        "members_only": "This video is members-only, so it cannot be retrieved.",
        "age_restricted": "This video is age-restricted, so it cannot be retrieved automatically.",
        "access_restricted": "YouTube requires sign-in or verification for this video. Upload the file instead.",
        "geo_restricted": "This video is not available in the server's region.",
        "live_stream": "Live streams and upcoming premieres cannot be processed. Try again after the stream ends.",
        "unavailable": "This video is unavailable or has been removed.",
        "copyright_restricted": "This video is blocked for copyright reasons.",
        "rate_limited": "YouTube is rate-limiting requests. Please retry shortly.",
        "network_timeout": "The video could not be reached in time. Please retry.",
        "network_error": "A network error interrupted retrieval. Please retry.",
    }.get(category, "The video could not be retrieved.")


def download(ref: YouTubeRef, dest_dir: str, *, progress: Optional[Callable[[float], None]] = None,
             cancel_check: Optional[Callable[[], bool]] = None) -> str:
    """Download the best MP4-compatible stream up to YTDLP_MAX_HEIGHT, into dest_dir. Returns the file path."""
    ensure_enabled()
    import yt_dlp

    started = time.monotonic()
    budget = settings.YTDLP_TIMEOUT_SECONDS
    last_cancel = {"t": 0.0, "cancelled": False}

    class _Cancelled(Exception):
        pass

    def hook(d: Dict[str, Any]) -> None:
        if time.monotonic() - started > budget:
            raise PipelineError("retrieval_timeout", "Retrieval exceeded its time limit.", retryable=True)
        if cancel_check and time.monotonic() - last_cancel["t"] > 2.0:
            last_cancel["t"] = time.monotonic()
            if cancel_check():
                raise _Cancelled()
        if d.get("status") == "downloading" and progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    h = settings.YTDLP_MAX_HEIGHT
    opts = _ydl_options({
        "format": f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/b[height<={h}]",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(dest_dir, "source.%(ext)s"),
        "progress_hooks": [hook],
        "max_filesize": settings.MAX_UPLOAD_BYTES,
    })
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([ref.normalized_url])
    except _Cancelled as exc:
        from .ffmpeg import CancelledError
        raise CancelledError() from exc
    except yt_dlp.utils.DownloadError as exc:
        category, retryable = classify_ytdlp_error(str(exc))
        raise RetrievalError(_retrieval_message(category), category=category, retryable=retryable) from exc

    candidates = sorted(glob.glob(os.path.join(dest_dir, "source.*")))
    candidates = [c for c in candidates if not c.endswith((".part", ".ytdl"))]
    if not candidates:
        raise RetrievalError("The download produced no file.", category="unknown_retrieval_error", retryable=True)
    return candidates[0]
