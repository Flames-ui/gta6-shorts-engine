"""Automated video quality assurance and compliance validation."""
import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from .security import safe_subprocess_run

logger = logging.getLogger("gta6_shorts.validator")


class VideoValidationResult(BaseModel):
    is_valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0
    fps: float = 0.0
    has_audio: bool = False
    audio_codec: Optional[str] = None
    file_size_bytes: int = 0
    has_black_frames: bool = False


class VideoValidator:
    """Verifies rendered MP4 clips against technical platform requirements."""

    @staticmethod
    def validate_clip(
        file_path: str,
        min_duration: float = 5.0,
        max_duration: float = 65.0,
        target_aspect_ratio: float = 9.0 / 16.0,  # 0.5625
    ) -> VideoValidationResult:
        errors = []
        warnings = []

        if not os.path.exists(file_path):
            return VideoValidationResult(is_valid=False, errors=["File does not exist on disk."])

        file_size = os.path.getsize(file_path)
        if file_size < 50 * 1024:  # Under 50KB is likely corrupted or empty
            return VideoValidationResult(is_valid=False, errors=[f"File size too small ({file_size} bytes). Corrupted render."])

        # Probe streams via FFprobe
        probe_cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path,
        ]

        try:
            proc = safe_subprocess_run(probe_cmd, timeout=30)
            data = json.loads(proc.stdout)
        except Exception as exc:
            return VideoValidationResult(is_valid=False, errors=[f"FFprobe inspection failed: {exc}"])

        streams = data.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not v_stream:
            errors.append("No video stream found in rendered container.")
            return VideoValidationResult(is_valid=False, errors=errors)

        width = int(v_stream.get("width", 0))
        height = int(v_stream.get("height", 0))
        if width <= 0 or height <= 0:
            errors.append(f"Invalid dimensions: {width}x{height}")
            return VideoValidationResult(is_valid=False, errors=errors)

        aspect_ratio = width / height
        # Allow +/- 8% tolerance for vertical 9:16
        if abs(aspect_ratio - target_aspect_ratio) > 0.08:
            errors.append(f"Incorrect aspect ratio: {aspect_ratio:.3f} (expected ~{target_aspect_ratio:.3f} for 9:16 vertical shorts).")

        # Duration check
        fmt_dur = float(data.get("format", {}).get("duration", 0.0) or v_stream.get("duration", 0.0))
        if fmt_dur < min_duration:
            errors.append(f"Duration too short: {fmt_dur:.1f}s (minimum required is {min_duration}s).")
        elif fmt_dur > max_duration:
            errors.append(f"Duration exceeds platform limit: {fmt_dur:.1f}s (maximum is {max_duration}s).")

        # Frame rate
        fps = 30.0
        r_frame_rate = v_stream.get("r_frame_rate", "30/1")
        try:
            num, den = r_frame_rate.split("/")
            fps = float(num) / float(den)
            if fps < 23.0:
                warnings.append(f"Low frame rate detected: {fps:.1f} fps.")
        except Exception:
            pass

        # Audio check
        has_audio = a_stream is not None
        audio_codec = a_stream.get("codec_name") if a_stream else None
        if not has_audio:
            errors.append("Audio stream is missing. TikTok and Shorts require audible sound.")

        # Black frame detection (sample first 10 seconds)
        has_black_frames = False
        try:
            black_detect_cmd = [
                "ffmpeg",
                "-t", "10",
                "-i", file_path,
                "-vf", "blackdetect=d=2:pix_th=0.10",
                "-an",
                "-f", "null",
                "-",
            ]
            bd_res = safe_subprocess_run(black_detect_cmd, timeout=30, check=False)
            if "black_start" in bd_res.stderr:
                warnings.append("Potential black/blank frames detected during opening sequence.")
                has_black_frames = True
        except Exception:
            pass

        is_valid = len(errors) == 0
        return VideoValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            duration_seconds=fmt_dur,
            width=width,
            height=height,
            aspect_ratio=aspect_ratio,
            fps=fps,
            has_audio=has_audio,
            audio_codec=audio_codec,
            file_size_bytes=file_size,
            has_black_frames=has_black_frames,
        )


video_validator = VideoValidator()
