"""Core background video processing pipeline execution."""
import logging
import os
import shutil
import tempfile
import uuid
from typing import Any, Dict, List, Optional

from ..config import settings
from .social_meta import generate_clip_social_metadata
from .storage import storage_service
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.processor")


def process_video_job(job_id: str):
    """Main job entrypoint executed by background worker."""
    job = db.get_job(job_id)
    if not job:
        logger.error("Job %s not found in database", job_id)
        return

    logger.info("Starting processing for job %s: %s", job_id, job["url"])
    db.add_log(job_id, "started", f"Worker picked up job {job_id}")

    temp_dir = tempfile.mkdtemp(prefix=f"gta6_job_{job_id}_")

    try:
        # Step 1: Download / Acquire Source Video
        db.update_job_progress(job_id, stage="downloading", progress=10, status="processing")
        db.add_log(job_id, "downloading", f"Acquiring source video: {job['url']}")

        mode = job.get("mode", "local")
        num_clips = int(job.get("num_clips", 3))
        aspect_ratio = job.get("aspect_ratio", "9:16")
        gta_preset = bool(job.get("gta_preset", True))
        llm_provider = job.get("llm_provider", "gemini")

        source_video_path = None
        video_title = "Source Video"
        video_duration = 0.0

        if mode == "local":
            from shorts_generator.local.downloader import download_youtube_local, _extract_youtube_video_id
            # Download to temp directory
            source_video_path = download_youtube_local(job["url"], fmt=job.get("target_format", "720"))
            
            # Extract duration & title using ffprobe / yt-dlp metadata
            video_title = os.path.splitext(os.path.basename(source_video_path))[0]
            try:
                import subprocess, json
                probe_cmd = [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "json", source_video_path
                ]
                res = subprocess.run(probe_cmd, capture_output=True, text=True)
                dur_data = json.loads(res.stdout)
                video_duration = float(dur_data.get("format", {}).get("duration", 0.0))
            except Exception as pe:
                logger.warning("Could not probe video duration: %s", pe)

        else:
            # API / MuAPI mode
            from shorts_generator.downloader import download_youtube
            source_video_path = download_youtube(job["url"], fmt=job.get("target_format", "720"))

        db.update_job_progress(
            job_id,
            stage="transcribing",
            progress=30,
            video_title=video_title,
            video_duration=video_duration,
        )
        db.add_log(job_id, "transcribing", f"Video acquired. Duration: {video_duration:.1f}s. Beginning audio transcription.")

        # Step 2: Transcribe Video
        transcript = None
        if mode == "local":
            from shorts_generator.local.transcriber import transcribe_local
            transcript = transcribe_local(source_video_path, language=job.get("language"))
        else:
            from shorts_generator.transcriber import transcribe
            transcript = transcribe(source_video_path, language=job.get("language"))

        if not transcript or not transcript.get("segments"):
            raise RuntimeError("Transcription yielded no segments. Video might lack clear audio or speech.")

        db.update_job_progress(job_id, stage="highlight_detection", progress=50)
        db.add_log(job_id, "highlight_detection", f"Transcription complete ({len(transcript['segments'])} segments). Detecting viral highlights with {llm_provider}.")

        # Step 3: AI Highlight Detection
        from shorts_generator.highlights import get_highlights
        if mode == "local":
            from shorts_generator.local.llm import call_local_llm
            highlights_res = get_highlights(transcript, num_clips=num_clips, llm_fn=call_local_llm)
        else:
            from shorts_generator.highlights import call_muapi_llm
            highlights_res = get_highlights(transcript, num_clips=num_clips, llm_fn=call_muapi_llm)

        raw_highlights = highlights_res.get("highlights", [])
        if not raw_highlights:
            raise RuntimeError("AI Highlight detector found zero clips.")

        # Sort and take top num_clips
        top_highlights = sorted(raw_highlights, key=lambda h: int(h.get("score", 0)), reverse=True)[:num_clips]
        db.add_log(job_id, "highlight_detection", f"Selected top {len(top_highlights)} viral candidates.")

        # Step 4: Crop Clips to 9:16 vertical & upload
        db.update_job_progress(job_id, stage="cropping_clips", progress=70)
        db.add_log(job_id, "cropping_clips", f"Rendering vertical 9:16 shorts using FFmpeg.")

        rendered_clips = []
        for i, h in enumerate(top_highlights, 1):
            clip_id = f"clip_{uuid.uuid4().hex[:12]}"
            start = float(h["start_time"])
            end = float(h["end_time"])
            dur = end - start

            # Render clip
            out_clip_filename = f"{job_id}_{clip_id}.mp4"
            local_clip_path = os.path.join(temp_dir, out_clip_filename)

            if mode == "local":
                from shorts_generator.local.clipper import _cut_subclip, _reframe_subclip
                # 1. cut
                raw_cut = os.path.join(temp_dir, f"raw_{out_clip_filename}")
                _cut_subclip(source_video_path, start, end, raw_cut)
                # 2. reframe to 9:16
                try:
                    _reframe_subclip(raw_cut, local_clip_path, aspect_ratio=aspect_ratio)
                except Exception as e:
                    logger.warning("Smart reframing failed, falling back to center crop: %s", e)
                    # ffmpeg crop fallback
                    import subprocess
                    crop_cmd = [
                        "ffmpeg", "-y", "-i", raw_cut,
                        "-vf", "crop=ih*9/16:ih",
                        "-c:a", "copy", local_clip_path
                    ]
                    subprocess.run(crop_cmd, check=True)
            else:
                from shorts_generator.clipper import crop_clip
                # MuAPI returns remote URL
                remote_crop_url = crop_clip(source_video_path, start, end, aspect_ratio=aspect_ratio)
                # Download clip to local for storage upload
                import requests
                r = requests.get(remote_crop_url, timeout=120)
                with open(local_clip_path, "wb") as f:
                    f.write(r.content)

            # Step 5: Social Metadata Enhancement (Title, Hook, Hashtags, Description)
            social_data = generate_clip_social_metadata(
                clip_text=h.get("title", "") + " " + h.get("hook_sentence", ""),
                original_title=video_title,
                virality_reason=h.get("virality_reason"),
                gta_preset=gta_preset,
                llm_provider=llm_provider,
            )

            # Step 6: Upload to Storage (Supabase Storage or local)
            public_clip_url, storage_path = storage_service.upload_clip(local_clip_path, out_clip_filename)

            clip_record = {
                "id": clip_id,
                "job_id": job_id,
                "title": social_data.get("title") or h.get("title", f"Clip #{i}"),
                "description": social_data.get("description", ""),
                "hook": social_data.get("hook") or h.get("hook_sentence", ""),
                "score": int(h.get("score", 85)),
                "start_time": start,
                "end_time": end,
                "duration": round(dur, 2),
                "aspect_ratio": aspect_ratio,
                "clip_url": public_clip_url,
                "storage_path": storage_path,
                "captions": None,
                "hashtags": social_data.get("hashtags", []),
                "publishing_status": "draft",
            }
            db.create_clip(clip_record)
            rendered_clips.append(clip_record)
            db.add_log(job_id, "cropping_clips", f"Rendered and saved clip #{i}: '{clip_record['title']}'")

        # Step 7: Completed
        db.update_job_progress(job_id, stage="completed", progress=100, status="completed")
        db.add_log(job_id, "completed", f"Job successfully completed. {len(rendered_clips)} shorts generated.")

    except Exception as e:
        logger.exception("Error processing job %s: %s", job_id, e)
        db.update_job_progress(job_id, stage="failed", progress=0, status="failed", error=str(e))
        db.add_log(job_id, "failed", f"Processing failed: {str(e)}")

    finally:
        # Safe cleanup of all temporary video and audio files
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info("Cleaned temporary processing directory: %s", temp_dir)
            except Exception as cle:
                logger.warning("Could not clean temp directory %s: %s", temp_dir, cle)
