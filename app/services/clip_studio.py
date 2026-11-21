"""Interactive Multitrack Clip Studio Engine & Timeline Synthesizer."""
import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from pydantic import BaseModel, Field

from ..config import settings
from .outro_service import outro_service
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.studio")


class TimelineKeyframe(BaseModel):
    time: float = Field(ge=0.0)
    zoom: float = Field(default=1.0, ge=1.0, le=2.0)
    crop_x_percent: float = Field(default=50.0, ge=0.0, le=100.0)
    crop_y_percent: float = Field(default=50.0, ge=0.0, le=100.0)


class CaptionItem(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str
    highlight: bool = False


class MultitrackTimeline(BaseModel):
    clip_id: str
    duration: float = Field(default=30.0, ge=1.0, le=60.0)
    source_url: Optional[str] = None
    title: str = "Untitled clip"

    # 6 Specialized Studio Tracks
    video_track: Dict[str, Any] = Field(default_factory=lambda: {
        "start_time": 0.0,
        "end_time": 26.0,
        "speed": 1.0,
        "volume": 1.0,
    })
    audio_track: Dict[str, Any] = Field(default_factory=lambda: {"ducking": False, "bg_music": None, "bg_volume": 0.0})
    captions_track: List[CaptionItem] = Field(default_factory=list)
    hooks_track: Dict[str, Any] = Field(default_factory=lambda: {
        "text": "", "start": 0.0, "end": 0.0, "preset": "none", "color": "#FFFFFF",
    })
    reframing_track: List[TimelineKeyframe] = Field(default_factory=list)
    outro_track: Dict[str, Any] = Field(default_factory=lambda: {
        "enabled": False, "duration": 4.0, "promo_text": "", "destination_url": "",
    })


class StudioEventHub:
    """Manages real-time Server-Sent Events (SSE) for the interactive Clip Studio console."""

    def __init__(self):
        self._queues: Dict[str, List[asyncio.Queue]] = {}

    def subscribe(self, job_or_clip_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        if job_or_clip_id not in self._queues:
            self._queues[job_or_clip_id] = []
        self._queues[job_or_clip_id].append(q)
        return q

    def unsubscribe(self, job_or_clip_id: str, q: asyncio.Queue):
        if job_or_clip_id in self._queues:
            self._queues[job_or_clip_id] = [item for item in self._queues[job_or_clip_id] if item is not q]
            if not self._queues[job_or_clip_id]:
                del self._queues[job_or_clip_id]

    async def broadcast(self, job_or_clip_id: str, event_type: str, data: Dict[str, Any]):
        msg = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        for q in self._queues.get(job_or_clip_id, []):
            await q.put(msg)
        # Also broadcast to global studio stream
        for q in self._queues.get("global", []):
            await q.put(msg)


studio_hub = StudioEventHub()


class ClipStudioService:
    """Core multitrack timeline orchestration and dynamic re-rendering engine."""

    def get_or_create_timeline(self, clip_id: str) -> MultitrackTimeline:
        """Fetch persisted timeline or synthesize an intelligent 6-track default."""
        saved = db.get_clip_timeline(clip_id)
        if saved:
            return MultitrackTimeline(**saved)

        # Fallback to clip data from database
        clip = db.get_clip(clip_id) or {}
        duration = float(clip.get("duration", 30.0))
        title = clip.get("title", "GTA 6 Viral Moment")
        outro_cfg = outro_service.get_config()

        timeline = MultitrackTimeline(
            clip_id=clip_id,
            duration=duration,
            source_url=clip.get("storage_url") or clip.get("video_url"),
            title=title,
            video_track={
                "start_time": 0.0,
                "end_time": max(1.0, duration - (outro_cfg.duration_seconds if outro_cfg.enabled else 0.0)),
                "speed": 1.0,
                "volume": 1.0,
            },
            audio_track={"ducking": False, "bg_music": None, "bg_volume": 0.0},
            captions_track=[],
            hooks_track={"text": "", "start": 0.0, "end": 0.0, "preset": "none", "color": "#FFFFFF"},
            reframing_track=[],
            outro_track={
                "enabled": outro_cfg.enabled,
                "duration": outro_cfg.duration_seconds,
                "promo_text": outro_cfg.promo_text,
                "destination_url": outro_cfg.destination_url,
            },
        )

        db.save_clip_timeline(clip_id, timeline.model_dump())
        return timeline

    def save_timeline(self, clip_id: str, timeline_data: Dict[str, Any]) -> MultitrackTimeline:
        """Persist modified multitrack timeline data."""
        timeline = MultitrackTimeline(**timeline_data)
        timeline.clip_id = clip_id
        db.save_clip_timeline(clip_id, timeline.model_dump())
        return timeline

    def render_timeline(self, clip_id: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Render only a real, locally stored source file; never substitute synthetic footage."""
        timeline = self.get_or_create_timeline(clip_id)
        clip = db.get_clip(clip_id)
        if not clip:
            raise ValueError("Clip record not found")
        source_path = clip.get("file_path")
        if not source_path or not os.path.isfile(source_path):
            raise ValueError("A real local source video is required; no source file is available for this clip")

        target_dir = os.path.join(settings.OUTPUT_DIR, "studio_renders")
        os.makedirs(target_dir, exist_ok=True)
        safe_clip_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in clip_id)[:100]
        final_output = output_path or os.path.join(target_dir, f"{safe_clip_id}_studio_rendered.mp4")
        temp_main = f"{final_output}.main.mp4"
        start_time = max(0.0, float(timeline.video_track.get("start_time", 0.0)))
        end_time = float(timeline.video_track.get("end_time", timeline.duration))
        segment_duration = end_time - start_time
        if segment_duration <= 0:
            raise ValueError("Timeline end must be after its start")

        filters = ["scale=1080:1920:force_original_aspect_ratio=increase", "crop=1080:1920"]
        hook_text = str(timeline.hooks_track.get("text") or "").strip()
        hook_text_file = None
        if hook_text:
            hook_text_file = f"{temp_main}.hook.txt"
            with open(hook_text_file, "w", encoding="utf-8") as text_file:
                text_file.write(hook_text)
            escaped_text_file = hook_text_file.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            hook_end = max(0.0, float(timeline.hooks_track.get("end", 0.0)))
            filters.append(
                f"drawtext=textfile='{escaped_text_file}':expansion=none:fontcolor=white:fontsize=48:box=1:boxcolor=black@0.6:"
                f"boxborderw=10:x=(w-text_w)/2:y=240:enable='between(t,0,{hook_end})'"
            )
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start_time:.3f}", "-i", source_path,
            "-t", f"{segment_duration:.3f}", "-vf", ",".join(filters),
            "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", temp_main,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        finally:
            if hook_text_file and os.path.isfile(hook_text_file):
                os.remove(hook_text_file)

        outro_enabled = bool(timeline.outro_track.get("enabled", False))
        if outro_enabled:
            outro_path = outro_service.get_config().outro_path
            if not outro_path or not os.path.isfile(outro_path):
                raise ValueError("Outro is enabled but no configured outro video exists")
            outro_res = outro_service.append_outro(temp_main, final_output)
            final_duration = outro_res["duration"]
        else:
            shutil.copy2(temp_main, final_output)
            final_duration = outro_service.probe_video_duration(final_output)
        try:
            os.remove(temp_main)
        except OSError:
            pass
        if not os.path.isfile(final_output) or os.path.getsize(final_output) == 0 or final_duration <= 0:
            raise RuntimeError("FFmpeg did not produce a valid playable output")

        db.update_clip(clip_id, {
            "storage_url": final_output,
            "duration": final_duration,
            "status": "completed",
        })
        return {
            "status": "rendered",
            "clip_id": clip_id,
            "output_path": final_output,
            "duration": final_duration,
            "rendered_at": datetime.now(timezone.utc).isoformat(),
        }



clip_studio = ClipStudioService()
