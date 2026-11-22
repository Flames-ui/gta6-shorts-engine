"""Authorized source-media ingestion.

Media is accepted only from a rights-holder-provided https file. Platform-hosted pages and streams
(YouTube and its video CDN) are never used as media, whatever the source rights say. A download is
streamed with a size cap, hashed while written, checked against its declared length and content
type, then validated with FFprobe and a decode of its first and last seconds. Any failure removes
the file and returns an explicit code; nothing is repaired or guessed.
"""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlsplit

import requests

from ..security import SecurityValidationError, validate_safe_url

logger = logging.getLogger("gta6_shorts.discovery.media")

MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
MIN_MEDIA_BYTES = 64 * 1024
MAX_SOURCE_DURATION_SECONDS = 4 * 3600
DOWNLOAD_TIMEOUT = (10, 300)
MAX_REDIRECTS = 3
USER_AGENT = "GTA6-Shorts-Media/2.0"
ALLOWED_CONTENT_TYPES = frozenset({
    "video/mp4", "video/quicktime", "video/webm", "video/x-matroska", "application/octet-stream",
})
PLATFORM_MEDIA_DOMAINS = ("youtube.com", "youtu.be", "googlevideo.com", "ytimg.com")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


class MediaIngestionError(Exception):
    """A media step failed. `code` is stable and stored on the item as the reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def is_platform_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in PLATFORM_MEDIA_DOMAINS)


def check_authorized_media_url(url: Optional[str]) -> str:
    """Return the URL when it is an https file location the rights holder controls, else raise."""
    raw = (url or "").strip()
    if not raw:
        raise MediaIngestionError("media_url_missing", "No authorized media URL is recorded for this item.")
    try:
        safe = validate_safe_url(raw, require_https=True)
    except SecurityValidationError as exc:
        raise MediaIngestionError("media_url_rejected", f"Media URL failed the safety check: {exc}")
    if is_platform_host(urlsplit(safe).hostname):
        raise MediaIngestionError(
            "platform_media_not_authorized",
            "Platform-hosted video pages and streams cannot be used as media. "
            "Provide the file from the rights holder through an authorized URL.",
        )
    return safe


def _open_authorized_stream(url: str):
    """Follow redirects by hand so every hop passes the same URL checks."""
    current = check_authorized_media_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        try:
            resp = requests.get(
                current, stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "video/*"},
            )
        except requests.RequestException as exc:
            raise MediaIngestionError("download_failed", f"Media download failed ({type(exc).__name__}).")
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise MediaIngestionError("download_http_error", "Media host redirected without a Location header.")
            current = check_authorized_media_url(urljoin(current, location))
            continue
        return current, resp
    raise MediaIngestionError("too_many_redirects", f"Media download exceeded {MAX_REDIRECTS} redirects.")


def download_authorized_media(url: str, dest_dir: str, item_id: str, expected_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Stream the file to dest_dir, hashing as it is written. Returns path, sha256, bytes and content type."""
    os.makedirs(dest_dir, exist_ok=True)
    safe_id = _SAFE_ID_RE.sub("_", item_id)[:80] or "item"
    partial = os.path.join(dest_dir, f"{safe_id}.partial")
    _, resp = _open_authorized_stream(url)
    try:
        if resp.status_code != 200:
            raise MediaIngestionError("download_http_error", f"Media host returned HTTP {resp.status_code}.")
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise MediaIngestionError(
                "content_type_rejected",
                f"Media content type '{content_type or 'missing'}' is not an accepted video type.",
            )
        declared_raw = resp.headers.get("Content-Length")
        declared = int(declared_raw) if declared_raw and declared_raw.isdigit() else None
        if declared is not None and declared > MAX_MEDIA_BYTES:
            raise MediaIngestionError("media_too_large", f"Declared size {declared} bytes exceeds the limit.")

        digest = hashlib.sha256()
        total = 0
        with open(partial, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    raise MediaIngestionError("media_too_large", "Download exceeded the size limit.")
                digest.update(chunk)
                fh.write(chunk)
        if total < MIN_MEDIA_BYTES:
            raise MediaIngestionError("media_too_small", f"Downloaded {total} bytes, below the minimum.")
        if declared is not None and total != declared:
            raise MediaIngestionError("incomplete_download", f"Received {total} of {declared} declared bytes.")

        sha = digest.hexdigest()
        if expected_sha256 and sha != expected_sha256:
            raise MediaIngestionError("sha256_mismatch", "Downloaded file differs from the previously validated file.")
        final = os.path.join(dest_dir, f"{safe_id}-{sha[:16]}.video")
        os.replace(partial, final)
        logger.info("discovery.media.downloaded %s", json.dumps({"item_id": item_id, "bytes": total, "sha256": sha}))
        return {"path": final, "sha256": sha, "bytes": total, "content_type": content_type}
    except MediaIngestionError:
        _remove_quietly(partial)
        raise
    except requests.RequestException as exc:
        _remove_quietly(partial)
        raise MediaIngestionError("download_failed", f"Media download interrupted ({type(exc).__name__}).")
    except OSError as exc:
        _remove_quietly(partial)
        raise MediaIngestionError("storage_failed", f"Media could not be written to disk ({type(exc).__name__}).")
    finally:
        resp.close()


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        logger.warning("discovery.media.cleanup_failed %s", json.dumps({"path": os.path.basename(path)}))


def file_sha256(path: str) -> Dict[str, Any]:
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
            total += len(chunk)
    return {"sha256": digest.hexdigest(), "bytes": total}


def _as_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if number == number else None  # rejects NaN
    except (TypeError, ValueError):
        return None


def _as_rate(value: Any) -> Optional[float]:
    if not value or value in ("0/0",):
        return None
    if "/" in str(value):
        num, _, den = str(value).partition("/")
        den_value = _as_float(den)
        num_value = _as_float(num)
        if not num_value or not den_value:
            return None
        return num_value / den_value
    return _as_float(value)


def probe_file(path: str, timeout: int = 60) -> Dict[str, Any]:
    """Return FFprobe's JSON for the file. Raises MediaIngestionError when it cannot be read."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise MediaIngestionError("ffprobe_unavailable", "ffprobe is not installed on this server.")
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MediaIngestionError("ffprobe_timeout", "ffprobe timed out reading the file.")
    if res.returncode != 0:
        raise MediaIngestionError("ffprobe_failed", f"ffprobe could not read the file (exit {res.returncode}).")
    try:
        return json.loads(res.stdout or "{}")
    except ValueError:
        raise MediaIngestionError("ffprobe_failed", "ffprobe returned unreadable output.")


def summarize_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the measured facts the pipeline needs. Raises when the file is not usable footage."""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaIngestionError("no_video_stream", "The file has no video stream.")
    duration = _as_float(fmt.get("duration")) or _as_float(video.get("duration"))
    if not duration or duration <= 0:
        raise MediaIngestionError("duration_unknown", "FFprobe reported no usable duration.")
    if duration > MAX_SOURCE_DURATION_SECONDS:
        raise MediaIngestionError("duration_too_long", f"Duration {duration:.0f}s exceeds the source limit.")
    width = int(_as_float(video.get("width")) or 0)
    height = int(_as_float(video.get("height")) or 0)
    if width <= 0 or height <= 0:
        raise MediaIngestionError("invalid_dimensions", "FFprobe reported no usable frame size.")
    fps = _as_rate(video.get("avg_frame_rate")) or _as_rate(video.get("r_frame_rate"))
    return {
        "format_name": fmt.get("format_name"),
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "video_codec": video.get("codec_name"),
        "fps": round(fps, 3) if fps else None,
        "has_audio": audio is not None,
        "audio_codec": audio.get("codec_name") if audio else None,
    }


def decode_check(path: str, seconds: int = 10, timeout: int = 180) -> None:
    """Decode the first and last seconds of video. Any error or warning fails the file."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise MediaIngestionError("ffmpeg_unavailable", "ffmpeg is not installed on this server.")
    head = [ffmpeg, "-v", "error", "-xerror", "-i", path, "-t", str(seconds), "-map", "0:v:0", "-f", "null", "-"]
    tail = [ffmpeg, "-v", "error", "-xerror", "-sseof", f"-{seconds}", "-i", path, "-map", "0:v:0", "-f", "null", "-"]
    for label, cmd in (("start", head), ("end", tail)):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise MediaIngestionError("decode_timeout", f"Decoding the {label} of the file timed out.")
        if res.returncode != 0 or (res.stderr or "").strip():
            first_line = (res.stderr or "").strip().splitlines()[:1]
            detail = first_line[0][:200] if first_line else f"exit {res.returncode}"
            raise MediaIngestionError("decode_failed", f"The {label} of the file does not decode: {detail}")


def validate_file(path: str, expected_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Re-hash the file, probe it and decode it. Returns the facts stored on the item."""
    if not os.path.isfile(path):
        raise MediaIngestionError("media_missing", "The media file is not on disk.")
    measured = file_sha256(path)
    if expected_sha256 and measured["sha256"] != expected_sha256:
        raise MediaIngestionError("sha256_mismatch", "The file on disk no longer matches its recorded SHA256.")
    summary = summarize_probe(probe_file(path))
    decode_check(path)
    return {"sha256": measured["sha256"], "bytes": measured["bytes"], "probe": summary}


def ingest_authorized_media(url: str, item_id: str, dest_dir: str, expected_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Download from an authorized URL and validate. The file is removed if validation fails."""
    downloaded = download_authorized_media(url, dest_dir, item_id, expected_sha256=expected_sha256)
    try:
        validated = validate_file(downloaded["path"], expected_sha256=downloaded["sha256"])
    except MediaIngestionError:
        _remove_quietly(downloaded["path"])
        raise
    return {"path": downloaded["path"], **validated, "content_type": downloaded["content_type"]}
