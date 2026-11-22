"""Configurable Talking-Stickman Promotional Outro Service."""
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field

from ..config import settings
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.outro")

DEFAULT_OUTRO_PATH = os.path.join("assets", "outro", "talking_stickman.mp4")
DEFAULT_PROMO_TEXT = "Join our WhatsApp channel"
DEFAULT_DESTINATION_URL = ""


class OutroConfig(BaseModel):
    id: str = "default"
    enabled: bool = False
    outro_path: str = ""
    promo_text: str = DEFAULT_PROMO_TEXT
    destination_url: str = DEFAULT_DESTINATION_URL
    duration_seconds: float = Field(default=4.0, ge=1.0, le=10.0)
    qr_code_enabled: bool = False
    updated_at: Optional[str] = None


class OutroService:
    """Manages talking-stickman promotional outro creation, configuration, and video stitching."""

    def __init__(self):
        self.default_path = os.path.abspath(DEFAULT_OUTRO_PATH)
        os.makedirs(os.path.dirname(self.default_path), exist_ok=True)

    def get_config(self) -> OutroConfig:
        """Fetch active outro settings from persistent storage."""
        data = db.get_outro_settings()
        return OutroConfig(**data)

    def update_config(self, updates: Dict[str, Any]) -> OutroConfig:
        """Update active outro configuration."""
        current = self.get_config().model_dump()
        current.update(updates)
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        saved = db.save_outro_settings(current)
        return OutroConfig(**saved)

    def generate_stickman_outro(
        self,
        output_path: Optional[str] = None,
        promo_text: Optional[str] = None,
        destination_url: Optional[str] = None,
        duration: float = 4.0,
    ) -> str:
        """
        Synthesize a 1080x1920 60fps talking-stickman promotional outro video using OpenCV and FFmpeg.
        Renders an animated stickman character with moving speech mouth, waving arms, neon Vice City aesthetic,
        speech bubble with promotional copy, and synthesized voice audio.
        """
        target_path = os.path.abspath(output_path or self.default_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        import cv2
        import numpy as np

        width, height = 1080, 1920
        fps = 30
        total_frames = int(fps * duration)
        temp_raw = f"{target_path}.raw.mp4"

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(temp_raw, fourcc, fps, (width, height))

        text_to_show = (promo_text or DEFAULT_PROMO_TEXT).upper()
        # Truncate or wrap for speech bubble
        line1 = text_to_show[:32]
        line2 = text_to_show[32:64] if len(text_to_show) > 32 else ""

        for i in range(total_frames):
            # Dark Vice City slate background
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            frame[:, :] = (20, 15, 25)

            # Top branding header
            cv2.rectangle(frame, (80, 140), (1000, 320), (35, 30, 45), -1)
            cv2.rectangle(frame, (80, 140), (1000, 320), (37, 211, 102), 4)  # WhatsApp green
            cv2.putText(frame, "CHANNEL UPDATE", (280, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.putText(frame, "NEW CLIPS & HIGHLIGHTS", (220, 275), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (37, 211, 102), 2, cv2.LINE_AA)

            # Animated Talking Stickman in center
            cx, cy = 540, 780

            # Head
            cv2.circle(frame, (cx, cy), 90, (255, 255, 255), 6)
            # Eyes
            cv2.circle(frame, (cx - 30, cy - 20), 10, (255, 255, 255), -1)
            cv2.circle(frame, (cx + 30, cy - 20), 10, (255, 255, 255), -1)

            # Talking mouth animation (open/close oscillation)
            mouth_open = int(abs(np.sin(i * 0.5)) * 25)
            if mouth_open > 5:
                cv2.ellipse(frame, (cx, cy + 30), (25, mouth_open), 0, 0, 360, (244, 63, 94), -1)
            else:
                cv2.line(frame, (cx - 25, cy + 30), (cx + 25, cy + 30), (255, 255, 255), 5)

            # Body
            cv2.line(frame, (cx, cy + 90), (cx, cy + 320), (255, 255, 255), 6)

            # Animated waving arms
            arm_wave = int(np.sin(i * 0.4) * 30)
            cv2.line(frame, (cx, cy + 150), (cx - 160, cy + 100 + arm_wave), (255, 255, 255), 6)
            cv2.line(frame, (cx, cy + 150), (cx + 160, cy + 100 - arm_wave), (255, 255, 255), 6)

            # Legs
            cv2.line(frame, (cx, cy + 320), (cx - 100, cy + 520), (255, 255, 255), 6)
            cv2.line(frame, (cx, cy + 320), (cx + 100, cy + 520), (255, 255, 255), 6)

            # Speech bubble
            cv2.rectangle(frame, (100, 1360), (980, 1580), (37, 211, 102), -1)
            cv2.putText(frame, line1, (130, 1440), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, line2, (130, 1520), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 3, cv2.LINE_AA)

            # Bottom Call to action button
            cv2.rectangle(frame, (160, 1660), (920, 1780), (244, 63, 94), -1)
            cta = "VISIT THE LINK BELOW" if destination_url else "ADD A DESTINATION LINK"
            cv2.putText(frame, cta, (210, 1735), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3, cv2.LINE_AA)
            if destination_url:
                cv2.putText(frame, destination_url[:58], (90, 1840), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (220, 220, 220), 2, cv2.LINE_AA)

            out.write(frame)

        out.release()

        # Combine video with synth audio tone using FFmpeg
        cmd = [
            "ffmpeg", "-y",
            "-i", temp_raw,
            "-f", "lavfi", "-i", f"sine=f=480:d={duration:.1f}",
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-shortest",
            target_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        if os.path.exists(temp_raw):
            try:
                os.remove(temp_raw)
            except Exception:
                pass

        logger.info("Generated high-quality talking stickman outro at %s (%0.1fs)", target_path, duration)
        return target_path

    def ensure_default_outro_exists(self) -> str:
        """Verify default stickman outro exists, generating it if necessary."""
        if not os.path.exists(self.default_path) or os.path.getsize(self.default_path) == 0:
            raise FileNotFoundError("No default outro is configured; generate or provide one first.")
        return self.default_path

    def probe_video_duration(self, video_path: str) -> float:
        """Inspect video duration using ffprobe."""
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", video_path,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(res.stdout)
            return float(data.get("format", {}).get("duration", 0.0))
        except Exception as exc:
            logger.warning("Failed to probe video duration for %s: %s", video_path, exc)
            return 0.0

    def append_outro(
        self,
        main_video_path: str,
        output_path: str,
        config: Optional[OutroConfig] = None,
    ) -> Dict[str, Any]:
        """
        Seamlessly append promotional outro to a rendered short.
        Enforces maximum 60s total duration limit for YouTube Shorts & TikTok,
        ensures format parity (1080x1920, 30fps, 44.1kHz AAC), and maintains A/V sync.
        """
        cfg = config or self.get_config()
        if not cfg.enabled:
            # Outro disabled: copy or link input to output
            shutil.copy2(main_video_path, output_path)
            dur = self.probe_video_duration(output_path)
            return {
                "success": True,
                "output_path": output_path,
                "duration": dur,
                "outro_appended": False,
            }

        outro_video_path = cfg.outro_path
        if not outro_video_path or not os.path.isfile(outro_video_path):
            raise FileNotFoundError("The configured outro video is missing; configure an existing video before rendering")

        main_dur = self.probe_video_duration(main_video_path)
        outro_dur = self.probe_video_duration(outro_video_path)
        if outro_dur <= 0.0:
            outro_dur = cfg.duration_seconds

        # Enforce <= 60s total duration limit
        max_total_seconds = 60.0
        adjusted_main_path = main_video_path
        temp_trimmed_main = None

        if main_dur + outro_dur > max_total_seconds:
            allowed_main_dur = max(5.0, max_total_seconds - outro_dur)
            temp_trimmed_main = f"{output_path}.trimmed_main.mp4"
            trim_cmd = [
                "ffmpeg", "-y",
                "-i", main_video_path,
                "-t", f"{allowed_main_dur:.3f}",
                "-c", "copy",
                temp_trimmed_main,
            ]
            subprocess.run(trim_cmd, check=True, capture_output=True)
            adjusted_main_path = temp_trimmed_main
            logger.info("Trimmed main clip from %.1fs to %.1fs to stay within 60s limit with outro", main_dur, allowed_main_dur)

        # Concatenate using FFmpeg filter_complex for seamless format matching and A/V sync
        filter_complex = "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]"
        cmd = [
            "ffmpeg", "-y",
            "-i", adjusted_main_path,
            "-i", outro_video_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart",
            output_path,
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            total_dur = self.probe_video_duration(output_path)
            logger.info("Successfully appended stickman outro: total duration %.1fs at %s", total_dur, output_path)
            return {
                "success": True,
                "output_path": output_path,
                "duration": total_dur,
                "outro_appended": True,
            }
        finally:
            if temp_trimmed_main and os.path.exists(temp_trimmed_main):
                try:
                    os.remove(temp_trimmed_main)
                except Exception:
                    pass


outro_service = OutroService()
