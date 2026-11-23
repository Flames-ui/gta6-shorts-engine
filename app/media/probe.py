"""ffprobe-based inspection, source limits, decode integrity and silence analysis."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..config import settings
from ..errors import MediaValidationError
from .ffmpeg import FFMPEG, ffprobe, run


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    format_name: str
    width: int
    height: int
    video_codec: str
    audio_codec: Optional[str]
    has_audio: bool
    fps: float
    rotation: int
    size_bytes: int


def _rate(value: str) -> float:
    try:
        num, den = value.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str) -> MediaInfo:
    result = ffprobe(["-print_format", "json", "-show_format", "-show_streams", path], timeout=120)
    if result.returncode != 0:
        raise MediaValidationError("The file could not be read as a video. It may be corrupt.", code="unreadable_media")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaValidationError("The file could not be read as a video.", code="unreadable_media") from exc

    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaValidationError("The file has no video stream.", code="no_video_stream")

    duration_raw = fmt.get("duration") or video.get("duration")
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        raise MediaValidationError("The video duration could not be determined.", code="duration_unknown")
    if duration <= 0:
        raise MediaValidationError("The video duration could not be determined.", code="duration_unknown")

    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(side["rotation"]) % 360
    tag_rotate = (video.get("tags") or {}).get("rotate")
    if tag_rotate and not rotation:
        rotation = int(tag_rotate) % 360
    if rotation in (90, 270):
        width, height = height, width
    if width <= 0 or height <= 0:
        raise MediaValidationError("The video dimensions could not be determined.", code="unreadable_media")

    return MediaInfo(
        duration=round(duration, 3),
        format_name=str(fmt.get("format_name", "")),
        width=width,
        height=height,
        video_codec=str(video.get("codec_name", "")),
        audio_codec=audio.get("codec_name") if audio else None,
        has_audio=audio is not None,
        fps=_rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"),
        rotation=rotation,
        size_bytes=int(fmt.get("size") or 0),
    )


def check_source_limits(info: MediaInfo, *, max_seconds: Optional[int] = None,
                        min_seconds: Optional[int] = None) -> None:
    max_s = max_seconds or settings.MAX_SOURCE_DURATION_SECONDS
    min_s = min_seconds or settings.MIN_SOURCE_DURATION_SECONDS
    if info.duration > max_s:
        raise MediaValidationError(
            f"The video is {info.duration / 60:.1f} minutes long. The limit is {max_s // 60} minutes.",
            code="duration_exceeds_limit",
        )
    if info.duration < min_s:
        raise MediaValidationError(
            f"The video is too short ({info.duration:.0f} s). Use at least {min_s} seconds.",
            code="duration_below_minimum",
        )


def verify_decodable(path: str, timeout: int = 900) -> None:
    """Decode every video frame (and audio when present) to null. Any error means the file is not trustworthy."""
    result = run(
        FFMPEG,
        ["-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", path,
         "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"],
        timeout=timeout,
        label="decode_check",
    )
    if result.returncode != 0 or result.stderr.strip():
        raise MediaValidationError(
            "The video is damaged or cannot be fully decoded. Re-export it and upload again.",
            code="corrupt_media",
        )


_SILENCE_START = re.compile(r"silence_start: (-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end: (-?\d+(?:\.\d+)?)")


def silence_intervals(path: str, duration: float, noise_db: int = -35, min_seconds: float = 1.5,
                      timeout: int = 900) -> List[Tuple[float, float]]:
    """Return silent spans using FFmpeg silencedetect. Used to reject clips that are mostly quiet."""
    result = run(
        FFMPEG,
        ["-hide_banner", "-nostdin", "-v", "info", "-i", path, "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_seconds}", "-f", "null", "-"],
        timeout=timeout,
        label="silencedetect",
    )
    starts = [float(m.group(1)) for m in _SILENCE_START.finditer(result.stderr)]
    ends = [float(m.group(1)) for m in _SILENCE_END.finditer(result.stderr)]
    spans: List[Tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        spans.append((max(0.0, s), min(duration, e)))
    return spans


def silent_fraction(spans: List[Tuple[float, float]], start: float, end: float) -> float:
    window = max(end - start, 1e-6)
    covered = 0.0
    for s, e in spans:
        overlap = min(e, end) - max(s, start)
        if overlap > 0:
            covered += overlap
    return min(1.0, covered / window)
