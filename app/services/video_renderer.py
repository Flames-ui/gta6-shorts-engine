"""Video rendering pipeline for smart 9:16 cropping, captions, hook overlays, and audio normalization."""
import json
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from .security import safe_subprocess_run, sanitize_filename
from .video_validator import video_validator

logger = logging.getLogger("gta6_shorts.renderer")


class VideoRenderProfile:
    TIKTOK = "tiktok"
    YOUTUBE_SHORTS = "youtube_shorts"


class VideoRenderer:
    """Renders viral 9:16 vertical shorts from source footage using FFmpeg."""

    @staticmethod
    def get_video_dimensions(video_path: str) -> Tuple[int, int, float]:
        """Inspect video width, height, and duration via FFprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            video_path,
        ]
        res = safe_subprocess_run(cmd, timeout=30)
        data = json.loads(res.stdout)
        streams = data.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        w = int(v_stream.get("width", 1920))
        h = int(v_stream.get("height", 1080))
        d = float(data.get("format", {}).get("duration", 0.0) or v_stream.get("duration", 0.0))
        return w, h, d

    @staticmethod
    def render_clip(
        source_video_path: str,
        output_path: str,
        start_time: float,
        end_time: float,
        hook_overlay_text: Optional[str] = None,
        subtitle_srt_path: Optional[str] = None,
        enable_audio_normalization: bool = True,
        target_profile: str = VideoRenderProfile.TIKTOK,
    ) -> Dict[str, Any]:
        """
        Execute FFmpeg to cut, vertically crop (9:16 at 1080x1920), apply hook overlay,
        normalize audio, and write final compliant MP4.
        """
        w, h, total_d = VideoRenderer.get_video_dimensions(source_video_path)
        duration = max(1.0, end_time - start_time)

        # 9:16 aspect ratio: for height h, crop width is h * 9 / 16
        crop_w = int(h * 9.0 / 16.0)
        # Ensure even numbers
        crop_w = crop_w - (crop_w % 2)
        # Center horizontally by default
        crop_x = max(0, int((w - crop_w) / 2))
        crop_x = crop_x - (crop_x % 2)

        # Assemble video filter chain
        vf_filters = [f"crop={crop_w}:{h}:{crop_x}:0", "scale=1080:1920:flags=lanczos"]

        # Hook overlay text in first 3 seconds (clean yellow bold text with semi-transparent background box)
        if hook_overlay_text:
            safe_hook = hook_overlay_text.replace("'", "\\'").replace(":", "\\:").replace('"', '')[:60]
            drawtext_filter = (
                f"drawtext=text='{safe_hook}':enable='between(t,0,3)':fontcolor=yellow:"
                f"fontsize=46:box=1:boxcolor=black@0.65:boxborderw=10:x=(w-text_w)/2:y=h*0.22"
            )
            vf_filters.append(drawtext_filter)

        # Subtitles if available
        if subtitle_srt_path and os.path.exists(subtitle_srt_path):
            safe_srt = subtitle_srt_path.replace("\\", "/").replace(":", "\\:")
            sub_filter = f"subtitles='{safe_srt}':force_style='FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,MarginV=120'"
            vf_filters.append(sub_filter)

        filter_complex = ",".join(vf_filters)

        # Build FFmpeg command
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", f"{start_time:.3f}",
            "-t", f"{duration:.3f}",
            "-i", source_video_path,
            "-vf", filter_complex,
        ]

        if enable_audio_normalization:
            # EBU R128 loudness normalization for mobile video
            cmd.extend(["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"])

        cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "21",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-movflags", "+faststart",
            output_path,
        ])

        logger.info("Rendering 9:16 vertical short: %s -> %s (%.1fs - %.1fs)", source_video_path, output_path, start_time, end_time)
        safe_subprocess_run(cmd, timeout=300)

        # Validate rendered clip
        validation = video_validator.validate_clip(output_path)
        logger.info("Validation for %s: valid=%s, warnings=%s", output_path, validation.is_valid, validation.warnings)

        return {
            "output_path": output_path,
            "duration": validation.duration_seconds,
            "is_valid": validation.is_valid,
            "validation": validation.model_dump(),
        }

    @staticmethod
    def generate_thumbnail(video_path: str, output_thumb_path: str, capture_timestamp: float = 1.5) -> str:
        """Capture a 1080x1920 representative JPEG thumbnail from the rendered video."""
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", f"{capture_timestamp:.2f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            output_thumb_path,
        ]
        safe_subprocess_run(cmd, timeout=30)
        return output_thumb_path


video_renderer = VideoRenderer()
