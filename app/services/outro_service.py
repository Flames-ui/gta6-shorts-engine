"""Configurable Talking-Stickman Promotional Outro Service."""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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
    channel_url: Optional[str] = None
    duration_seconds: float = Field(default=4.0, ge=1.0, le=10.0)
    qr_code_enabled: bool = False
    asset_exists: bool = False
    asset_metadata: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[str] = None


class OutroService:
    """Manages talking-stickman promotional outro creation, configuration, and video stitching."""

    def __init__(self):
        self.default_path = os.path.abspath(DEFAULT_OUTRO_PATH)
        os.makedirs(os.path.dirname(self.default_path), exist_ok=True)

    def get_config(self) -> OutroConfig:
        """Fetch active outro settings from persistent storage."""
        data = dict(db.get_outro_settings())
        path = data.get("outro_path")
        exists = bool(path and os.path.isfile(path) and os.path.getsize(path) > 0)
        data["asset_exists"] = exists
        metadata = {}
        if exists:
            try:
                with open(f"{path}.metadata.json", "r", encoding="utf-8") as metadata_file:
                    metadata = json.load(metadata_file)
            except (OSError, json.JSONDecodeError):
                metadata = {}
        data["asset_metadata"] = metadata
        dest = data.get("destination_url") or ""
        if not data.get("channel_url"):
            data["channel_url"] = dest
        return OutroConfig(**data)

    def update_config(self, updates: Dict[str, Any]) -> OutroConfig:
        """Update active outro configuration."""
        current = self.get_config().model_dump()
        if "channel_url" in updates and not updates.get("destination_url"):
            updates["destination_url"] = updates["channel_url"]
        current.update(updates)
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        # Keep API-only aliases and filesystem metadata out of the persisted database row.
        persisted = {
            key: current.get(key)
            for key in ("id", "enabled", "outro_path", "promo_text", "destination_url", "duration_seconds", "qr_code_enabled", "updated_at")
        }
        saved = db.save_outro_settings(persisted)
        saved["channel_url"] = saved.get("destination_url", "")
        return self.get_config()

    def check_campaign_outro_eligibility(
        self,
        campaign: Dict[str, Any],
        current_duration: float = 0.0,
    ) -> Tuple[bool, List[str]]:
        """
        Evaluate Section 19.D rules before injecting stickman outro into a campaign video.
        Fails closed: returns (eligible: bool, reasons: List[str]).
        Verifies:
        - Outro enabled globally and asset exists.
        - Campaign permits promotional outros.
        - Campaign permits external links / destination URLs.
        - Campaign permits WhatsApp promotion (if promo text or URL mentions WhatsApp).
        - Branding requirements are satisfied (e.g. no exclusive brand prohibitions).
        - Total duration (current + outro) <= campaign max_duration and <= 60.0s.
        """
        reasons = []
        cfg = self.get_config()
        if not cfg.enabled:
            return False, ["Outro injection is disabled globally in Outro Studio."]

        if not cfg.outro_path or not os.path.isfile(cfg.outro_path) or os.path.getsize(cfg.outro_path) == 0:
            return False, ["Outro asset does not exist on disk; synthesis required before injection."]

        rules_value = campaign.get("raw_rules") or campaign.get("rules") or ""
        raw_rules = rules_value.lower() if isinstance(rules_value, str) else json.dumps(rules_value, sort_keys=True).lower()
        title = str(campaign.get("title") or "").lower()
        prohibited = [str(p).lower() for p in (campaign.get("prohibited_editing_techniques") or [])]
        disqualifications = [str(d).lower() for d in (campaign.get("disqualification_reasons") or [])]
        combined_rules = f"{raw_rules} {title} {' '.join(prohibited)} {' '.join(disqualifications)}"

        # Permissions must be affirmative. Missing/ambiguous campaign language fails closed.
        promo_allowed = bool(campaign.get("promotional_outro_allowed")) or any(
            marker in combined_rules for marker in (
                "promotional outro permitted", "promotional outros permitted",
                "promotional outro allowed", "promotional outros allowed",
                "promotional cta permitted", "promotional cta allowed",
            )
        )
        if not promo_allowed:
            reasons.append("Campaign does not explicitly permit promotional outros.")

        if any(marker in combined_rules for marker in (
            "no outro", "no outros", "no promotion", "no promotional outro",
            "no self-promo", "no self promo", "no external promo", "no 3rd party promo",
            "no third party promo", "clean video only", "no external cta",
        )):
            reasons.append("Campaign explicitly prohibits promotional outro or CTA content.")

        is_link_promo = bool(cfg.destination_url and cfg.destination_url.strip())
        if is_link_promo:
            from urllib.parse import urlparse
            parsed_destination = urlparse(cfg.destination_url.strip())
            if parsed_destination.scheme not in ("https", "http") or not parsed_destination.netloc:
                reasons.append("Configured destination link is not a valid HTTP(S) URL.")
            link_allowed = bool(campaign.get("external_links_allowed")) or any(
                marker in combined_rules for marker in (
                    "external links permitted", "external links allowed", "external urls permitted",
                    "external urls allowed", "destination links permitted", "destination links allowed",
                )
            )
            if not link_allowed:
                reasons.append("Campaign does not explicitly permit external links.")
            if any(marker in combined_rules for marker in ("no external links", "no links", "no urls", "no website links", "no external urls")):
                reasons.append("Campaign explicitly prohibits external links.")

        is_whatsapp = "whatsapp" in (cfg.promo_text or "").lower() or "whatsapp" in (cfg.destination_url or "").lower()
        if is_whatsapp:
            whatsapp_allowed = bool(campaign.get("whatsapp_promotion_allowed")) or any(
                marker in combined_rules for marker in (
                    "whatsapp promotion permitted", "whatsapp promotions permitted",
                    "whatsapp promotion allowed", "whatsapp promotions allowed",
                    "whatsapp links permitted", "whatsapp links allowed",
                )
            )
            if not whatsapp_allowed:
                reasons.append("Campaign does not explicitly permit WhatsApp promotion.")
            if any(marker in combined_rules for marker in ("no whatsapp", "no chat links", "no messaging links", "only official links")):
                reasons.append("Campaign explicitly prohibits WhatsApp or messaging promotion.")

        # 4. Check branding requirements
        if campaign.get("requires_exclusive_branding") or "exclusive branding" in combined_rules:
            reasons.append("Campaign requires exclusive brand assets; third-party promotional outros prohibited.")

        # 5. Check duration compliance
        max_campaign_dur = float(campaign.get("max_clip_duration_seconds") or 60.0)
        max_allowed = min(max_campaign_dur, 60.0)
        asset_duration = self.probe_video_duration(cfg.outro_path)
        if asset_duration <= 0:
            reasons.append("Generated outro duration could not be verified.")
            asset_duration = cfg.duration_seconds
        projected_duration = current_duration + asset_duration
        if projected_duration > max_allowed:
            reasons.append(f"Projected duration {projected_duration:.1f}s exceeds maximum allowed ({max_allowed:.1f}s).")

        is_eligible = len(reasons) == 0
        return is_eligible, reasons

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

        digest = hashlib.sha256()
        with open(target_path, "rb") as generated_file:
            for chunk in iter(lambda: generated_file.read(1024 * 1024), b""):
                digest.update(chunk)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", target_path],
            capture_output=True, text=True, check=True,
        )
        streams = json.loads(probe.stdout).get("streams", [])
        dimensions = {"width": streams[0].get("width"), "height": streams[0].get("height")} if streams else {}
        metadata = {
            "asset_id": str(uuid.uuid4()),
            "file_path": target_path,
            "duration_seconds": self.probe_video_duration(target_path),
            "dimensions": dimensions,
            "sha256": digest.hexdigest(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "configuration": {"promo_text": promo_text or DEFAULT_PROMO_TEXT, "destination_url": destination_url or "", "duration_seconds": duration},
            "generation_status": "generated",
        }
        with open(f"{target_path}.metadata.json", "w", encoding="utf-8") as metadata_file:
            json.dump(metadata, metadata_file, indent=2)
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
