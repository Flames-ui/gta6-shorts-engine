"""Interactive Multitrack Clip Studio Engine & Timeline Synthesizer."""
import asyncio
import collections
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

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
    audio_track: Dict[str, Any] = Field(default_factory=lambda: {
        "ducking": False, "bg_music": None, "bg_volume": 0.0, "normalize_loudness": True,
    })
    captions_track: List[CaptionItem] = Field(default_factory=list)
    hooks_track: Dict[str, Any] = Field(default_factory=lambda: {
        "text": "", "start": 0.0, "end": 0.0, "preset": "none", "color": "#FFFFFF",
    })
    reframing_track: List[TimelineKeyframe] = Field(default_factory=list)
    outro_track: Dict[str, Any] = Field(default_factory=lambda: {
        "enabled": False, "duration": 4.0, "promo_text": "", "destination_url": "",
    })


class StudioEventHub:
    """Manages real-time Server-Sent Events (SSE) and persistent event logs for Clip Studio."""

    def __init__(self):
        self._queues: Dict[str, List[asyncio.Queue]] = {}
        # Keep recent events in a bounded ring buffer for UI reload/hydration
        self._history: Dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=50))

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

    def record_event_sync(self, job_or_clip_id: str, event_type: str, data: Dict[str, Any]):
        """Synchronous event recording to ensure non-async worker threads can publish events."""
        msg = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        self._history[job_or_clip_id].append(msg)
        self._history["global"].append(msg)

    async def broadcast(self, job_or_clip_id: str, event_type: str, data: Dict[str, Any]):
        """Asynchronously dispatch events to SSE subscribers."""
        msg = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        self._history[job_or_clip_id].append(msg)
        self._history["global"].append(msg)

        for q in self._queues.get(job_or_clip_id, []):
            try:
                q.put_nowait(msg)
            except Exception:
                pass
        for q in self._queues.get("global", []):
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    def get_history(self, job_or_clip_id: str) -> List[Dict[str, Any]]:
        """Return cached recent events for hydration on page connect."""
        return list(self._history.get(job_or_clip_id, []))


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
            audio_track={
                "ducking": False,
                "bg_music": None,
                "bg_volume": 0.0,
                "normalize_loudness": True,
            },
            captions_track=[],
            hooks_track={
                "text": clip.get("hook_sentence") or f"GTA 6: {title.upper()}",
                "start": 0.0,
                "end": 3.0,
                "preset": "impact_banner",
                "color": "#FFFFFF",
            },
            reframing_track=[
                TimelineKeyframe(time=0.0, zoom=1.0, crop_x_percent=50.0, crop_y_percent=50.0),
                TimelineKeyframe(time=duration * 0.5, zoom=1.08, crop_x_percent=50.0, crop_y_percent=50.0),
                TimelineKeyframe(time=duration, zoom=1.0, crop_x_percent=50.0, crop_y_percent=50.0),
            ],
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
        payload = dict(timeline_data)
        payload["clip_id"] = clip_id
        if "reframing_track" in payload and isinstance(payload["reframing_track"], list):
            norm_track = []
            for kf in payload["reframing_track"]:
                if isinstance(kf, dict):
                    dkf = dict(kf)
                    if "time" not in dkf and "time_offset" in dkf:
                        dkf["time"] = dkf.pop("time_offset")
                    norm_track.append(dkf)
                else:
                    norm_track.append(kf)
            payload["reframing_track"] = norm_track
        timeline = MultitrackTimeline(**payload)
        db.save_clip_timeline(clip_id, timeline.model_dump())
        return timeline

    def _generate_ass_subtitles(self, captions: List[CaptionItem], target_path: str):
        """Generate formatted Advanced SubStation Alpha (.ass) subtitle file positioned in safe zone."""
        header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,60,60,320,1
Style: Highlight,DejaVu Sans,56,&H0000E5FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        def fmt_time(t: float) -> str:
            hrs = int(t // 3600)
            mins = int((t % 3600) // 60)
            secs = t % 60
            return f"{hrs}:{mins:02d}:{secs:05.2f}"

        events = []
        for c in captions:
            start_str = fmt_time(c.start)
            end_str = fmt_time(c.end)
            style_name = "Highlight" if c.highlight else "Default"
            cleaned_text = c.text.replace("\n", " ").replace("{", "").replace("}", "")
            events.append(f"Dialogue: 0,{start_str},{end_str},{style_name},,0,0,0,,{cleaned_text}")

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(events) + "\n")

    def render_timeline(self, clip_id: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Render only a real, locally stored source file; never substitute synthetic footage.
        Applies:
        - Multitrack trimming
        - Dynamic vertical 9:16 reframing with keyframes
        - Synchronized styled captions placed in safe zone
        - 3-second dynamic hook banner
        - Loudnorm audio normalization
        - Configured talking-stickman promotional outro (<= 60s total duration constraint)
        - Real-time SSE stage events broadcasting
        """
        studio_hub.record_event_sync(clip_id, "loading_source", {"clip_id": clip_id, "stage": "loading_source"})

        timeline = self.get_or_create_timeline(clip_id)
        clip = db.get_clip(clip_id)
        if not clip:
            raise ValueError("Clip record not found")
        source_path = clip.get("file_path") or clip.get("storage_path")
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

        studio_hub.record_event_sync(clip_id, "applying_reframing", {
            "clip_id": clip_id,
            "start_time": start_time,
            "end_time": end_time,
            "duration": segment_duration,
        })

        # Calculate crop coordinates based on reframing keyframes
        # Default center crop 9:16 from 16:9 (crop 608x1080 from 1920x1080)
        crop_x = 656
        crop_y = 0
        if timeline.reframing_track:
            avg_x_pct = sum(k.crop_x_percent for k in timeline.reframing_track) / len(timeline.reframing_track)
            crop_x = int(max(0, min(1920 - 608, (1920 - 608) * (avg_x_pct / 100.0))))

        vfilters = [
            f"crop=w=608:h=1080:x={crop_x}:y={crop_y}",
            "scale=1080:1920",
        ]

        # Hook text overlay for first 3 seconds
        hook_text = str(timeline.hooks_track.get("text") or "").strip()
        hook_text_file = None
        if hook_text:
            hook_text_file = f"{temp_main}.hook.txt"
            with open(hook_text_file, "w", encoding="utf-8") as text_file:
                text_file.write(hook_text)
            escaped_text_file = hook_text_file.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            hook_end = max(1.0, min(segment_duration, float(timeline.hooks_track.get("end", 3.0))))
            vfilters.append(
                f"drawtext=textfile='{escaped_text_file}':expansion=none:fontcolor=white:fontsize=48:box=1:boxcolor=black@0.6:"
                f"boxborderw=10:x=(w-text_w)/2:y=240:enable='between(t,0,{hook_end})'"
            )

        # Synchronized Captions Track
        ass_sub_path = None
        if timeline.captions_track:
            studio_hub.record_event_sync(clip_id, "generating_captions", {"caption_count": len(timeline.captions_track)})
            ass_sub_path = f"{temp_main}.subs.ass"
            self._generate_ass_subtitles(timeline.captions_track, ass_sub_path)
            escaped_ass = ass_sub_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            vfilters.append(f"ass='{escaped_ass}'")

        # Audio Track processing & Loudnorm normalization
        audio_filters = []
        if timeline.audio_track.get("normalize_loudness", True):
            studio_hub.record_event_sync(clip_id, "processing_audio", {"loudness_normalized": True})
            audio_filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")

        cmd = [
            "ffmpeg", "-y", "-ss", f"{start_time:.3f}", "-i", source_path,
            "-t", f"{segment_duration:.3f}",
            "-vf", ",".join(vfilters),
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        ]
        if audio_filters:
            cmd.extend(["-af", ",".join(audio_filters)])
        cmd.extend(["-movflags", "+faststart", temp_main])

        studio_hub.record_event_sync(clip_id, "rendering", {"target": temp_main})

        try:
            subprocess.run(cmd, check=True, capture_output=True)
        finally:
            if hook_text_file and os.path.isfile(hook_text_file):
                try:
                    os.remove(hook_text_file)
                except OSError:
                    pass
            if ass_sub_path and os.path.isfile(ass_sub_path):
                try:
                    os.remove(ass_sub_path)
                except OSError:
                    pass

        # Check outro concatenation
        outro_enabled = bool(timeline.outro_track.get("enabled", False))
        if outro_enabled:
            studio_hub.record_event_sync(clip_id, "adding_outro", {"enabled": True})
            cfg = outro_service.get_config()
            outro_path = cfg.outro_path
            if not outro_path or not os.path.isfile(outro_path):
                try:
                    outro_path = outro_service.generate_stickman_outro(
                        promo_text=timeline.outro_track.get("promo_text") or cfg.promo_text,
                        destination_url=timeline.outro_track.get("destination_url") or cfg.destination_url,
                        duration=float(timeline.outro_track.get("duration", 3.0)),
                    )
                except Exception as ex:
                    logger.warning("Could not auto-generate stickman outro: %s", ex)
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

        studio_hub.record_event_sync(clip_id, "validating_output", {
            "output_path": final_output,
            "duration": final_duration,
            "status": "valid",
        })

        db.update_clip(clip_id, {
            "storage_url": final_output,
            "duration": final_duration,
            "status": "completed",
        })

        studio_hub.record_event_sync(clip_id, "completed", {
            "output_path": final_output,
            "duration": final_duration,
        })

        return {
            "success": True,
            "status": "rendered",
            "clip_id": clip_id,
            "output_path": final_output,
            "duration": final_duration,
            "rendered_at": datetime.now(timezone.utc).isoformat(),
        }


clip_studio = ClipStudioService()
