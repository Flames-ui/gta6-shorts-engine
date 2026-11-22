"""Core background video processing pipeline with multi-category AI, FFmpeg rendering, and validation."""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from typing import Any, Dict, List, Optional

from ..config import settings
from .ai_intelligence import ai_intelligence_service
from .categories import category_service
from .security import sanitize_filename, validate_safe_url
from .social_meta import generate_clip_social_metadata
from .storage import storage_service
from .supabase_client import db
from .video_renderer import video_renderer
from .video_validator import video_validator

logger = logging.getLogger("gta6_shorts.processor")


def process_video_job(job_id: str):
    """Main job entrypoint executed by background worker process."""
    job = db.get_job(job_id)
    if not job:
        logger.error("Job %s not found in database", job_id)
        return

    logger.info("Starting processing for job %s: %s (Category: %s)", job_id, job["url"], job.get("category_id"))
    db.add_log(job_id, "started", f"Worker picked up job {job_id}")

    temp_dir = tempfile.mkdtemp(prefix=f"shorts_job_{job_id}_")

    try:
        category_id = job.get("category_id", "gaming")
        user_id = job.get("user_id")
        num_clips = int(job.get("num_clips", 3))
        aspect_ratio = job.get("aspect_ratio", "9:16")
        llm_provider = job.get("llm_provider", "gemini")
        mode = job.get("mode", "local")

        # Step 0: Campaign Eligibility Gate Check
        campaign_id = job.get("campaign_id")
        source_id = job.get("source_id")
        target_platform = job.get("target_platform", "tiktok")

        campaign = None
        source = None
        if campaign_id:
            from .campaign_eligibility import campaign_eligibility_service
            campaign = db.get_campaign(campaign_id)
            if source_id:
                source = db.get_campaign_source(source_id)

            gate_ok, gate_reason, gate_details = campaign_eligibility_service.validate_clipping_gate(
                campaign=campaign,
                source=source,
                target_platform=target_platform,
            )
            if not gate_ok:
                db.update_job_progress(job_id, stage="campaign_gate", status="failed", error=gate_reason)
                db.add_log(job_id, "campaign_gate", f"Blocked by campaign eligibility gate: {gate_reason}")
                return

        # Step 1: Video Acquisition (Safe Ingestion)
        db.update_job_progress(job_id, stage="downloading", progress=10, status="processing")
        db.add_log(job_id, "downloading", f"Acquiring video source for category '{category_id}': {job['url']}")

        # Check source rights (fail-closed in production)
        source_id = job.get("source_id")
        if not source_id and settings.ENVIRONMENT.lower() == "production" and settings.FAIL_CLOSED_IN_PRODUCTION:
            db.update_job_progress(job_id, stage="rights_check", status="failed", error="Rights check failed: No authorized source ID associated with job in production.")
            db.add_log(job_id, "rights_check", "Blocked by content rights check: Missing authorized source ID.")
            return

        if source_id:
            from .discovery.rights_manager import rights_manager
            allowed, msg = rights_manager.check_rights_for_production(source_id)
            if not allowed:
                db.update_job_progress(job_id, stage="rights_check", status="failed", error=f"Rights check failed: {msg}")
                db.add_log(job_id, "rights_check", f"Blocked by content rights check: {msg}")
                return


        raw_url = job["url"].strip()
        source_video_path = None
        video_title = "Source Video"
        video_duration = 0.0

        if os.path.isfile(raw_url):
            # Authorized direct local media upload
            source_video_path = raw_url
            video_title = os.path.splitext(os.path.basename(raw_url))[0]
        else:
            # Validate remote URL before retrieval
            validate_safe_url(raw_url)
            if mode == "local":
                from shorts_generator.local.downloader import download_youtube_local
                source_video_path = download_youtube_local(raw_url, fmt=job.get("target_format", "720"))
                video_title = os.path.splitext(os.path.basename(source_video_path))[0]
            else:
                from shorts_generator.downloader import download_youtube
                source_video_path = download_youtube(raw_url, fmt=job.get("target_format", "720"))
                video_title = os.path.splitext(os.path.basename(source_video_path))[0]

        # Probe duration and metadata
        try:
            probe_cmd = [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "json", source_video_path,
            ]
            res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
            dur_data = json.loads(res.stdout)
            video_duration = float(dur_data.get("format", {}).get("duration", 0.0))
        except Exception as pe:
            logger.warning("Could not probe video duration: %s", pe)

        db.update_job_progress(
            job_id,
            stage="transcribing",
            progress=30,
            video_title=video_title,
            video_duration=video_duration,
        )
        db.add_log(job_id, "transcribing", f"Source acquired. Duration: {video_duration:.1f}s. Transcribing audio.")

        # Step 2: Audio Transcription
        transcript = None
        if mode == "local":
            from shorts_generator.local.transcriber import transcribe_local
            transcript = transcribe_local(source_video_path, language=job.get("language"))
        else:
            from shorts_generator.transcriber import transcribe
            transcript = transcribe(source_video_path, language=job.get("language"))

        if not transcript or not transcript.get("segments"):
            raise RuntimeError("Transcription produced zero segments. Video audio may be silent or unintelligible.")

        db.update_job_progress(job_id, stage="highlight_detection", progress=50)
        db.add_log(job_id, "highlight_detection", f"Transcribed {len(transcript['segments'])} segments. Scoring highlights with {llm_provider}.")

        # Step 3: AI Intelligence & Highlight Identification
        full_transcript_text = " ".join(s.get("text", "") for s in transcript.get("segments", []))
        ai_analysis = ai_intelligence_service.analyze_content(
            transcript_text=full_transcript_text,
            category_id=category_id,
            video_title=video_title,
            provider=llm_provider,
        )

        candidates = ai_analysis.highlights
        if not candidates:
            # Fallback to existing highlight detector if custom AI returned none
            from shorts_generator.highlights import get_highlights
            if mode == "local":
                from shorts_generator.local.llm import call_local_llm
                hl_res = get_highlights(transcript, num_clips=num_clips, llm_fn=call_local_llm)
            else:
                from shorts_generator.highlights import call_muapi_llm
                hl_res = get_highlights(transcript, num_clips=num_clips, llm_fn=call_muapi_llm)
            raw_hl = hl_res.get("highlights", [])
            candidates = sorted(raw_hl, key=lambda h: int(h.get("score", 0)), reverse=True)[:num_clips]

        top_candidates = candidates[:num_clips]
        db.add_log(job_id, "highlight_detection", f"Selected {len(top_candidates)} highlights for vertical rendering.")

        # Step 4: Video Rendering & Reframing to 9:16
        db.update_job_progress(job_id, stage="cropping_clips", progress=70)
        db.add_log(job_id, "cropping_clips", "Rendering vertical 9:16 Shorts with audio normalization and subtitle styling.")

        rendered_clips = []
        for i, c in enumerate(top_candidates, 1):
            clip_id = f"clip_{uuid.uuid4().hex[:12]}"
            # Support both dict and Pydantic highlight structures
            start = float(c.start_time if hasattr(c, "start_time") else c["start_time"])
            end = float(c.end_time if hasattr(c, "end_time") else c["end_time"])
            score_val = float(c.virality_score if hasattr(c, "virality_score") else c.get("score", 85))
            dur = max(end - start, 1.0)

            out_clip_filename = f"{job_id}_{clip_id}.mp4"
            local_clip_path = os.path.join(temp_dir, out_clip_filename)

            # Use video_renderer for standardized vertical 9:16 generation
            success, render_msg = video_renderer.render_vertical_clip(
                source_video_path=source_video_path,
                start_time=start,
                end_time=end,
                output_path=local_clip_path,
                aspect_ratio=aspect_ratio,
                normalize_audio=True,
            )

            if not success or not os.path.exists(local_clip_path):
                logger.warning("Standard renderer failed (%s); falling back to direct subclip cut", render_msg)
                from shorts_generator.local.clipper import _cut_subclip
                _cut_subclip(source_video_path, start, end, local_clip_path)

            # Step 5: Final Video Validation & Quality Inspection
            val_result = video_validator.validate_clip(local_clip_path)
            # Step 5b: Campaign Compliance Validation
            compliance_status = "compliant"
            submission_ready = val_result.is_valid
            failure_reasons = []
            if campaign:
                from .compliance_validator import compliance_validator
                req_tags = campaign.get("required_hashtags") or []
                req_mentions = campaign.get("required_mentions") or []
                combined_caption = f"{social_data.get('title') or video_title} {' '.join(req_mentions)} {' '.join(req_tags)}"

                comp_result = compliance_validator.validate_clip_compliance(
                    clip_id=clip_id,
                    video_path=local_clip_path,
                    campaign=campaign,
                    source=source,
                    target_platform=target_platform,
                    clip_metadata={
                        "duration": dur,
                        "caption": combined_caption,
                        "hashtags": ' '.join(req_tags),
                        "description": social_data.get('description'),
                        "start_time": start,
                        "end_time": end,
                    },
                    existing_clips=rendered_clips,
                )
                compliance_status = "compliant" if comp_result.is_compliant else "non_compliant"
                submission_ready = comp_result.submission_ready
                failure_reasons = comp_result.failure_reasons

                db.save_campaign_clip_compliance({
                    "clip_id": clip_id,
                    "campaign_id": campaign["id"],
                    "source_id": source_id,
                    "job_id": job_id,
                    "target_platform": target_platform,
                    "compliance_status": compliance_status,
                    "compliance_results": comp_result.details,
                    "submission_ready": submission_ready,
                    "failure_reasons": failure_reasons,
                })

            if not val_result.is_valid:
                logger.warning("Clip %s failed validation: %s", clip_id, val_result.issues)

            # Step 6: Social Metadata & Category-Aware Copy
            category_def = category_service.get_category(category_id)
            c_title = (c.hook if hasattr(c, "hook") else c.get("title")) or f"{category_def.name} Highlight #{i}"
            c_hook = (c.hook if hasattr(c, "hook") else c.get("hook_sentence")) or c_title
            c_reason = (c.reason if hasattr(c, "reason") else c.get("virality_reason")) or "High-interest moment"

            social_data = generate_clip_social_metadata(
                clip_text=f"{c_title} {c_hook}",
                original_title=video_title,
                virality_reason=c_reason,
                gta_preset=(category_id == "gaming"),
                llm_provider=llm_provider,
            )

            # Step 7: Storage Upload (Hierarchical tenant isolation)
            public_clip_url, storage_path = storage_service.upload_clip(
                local_file_path=local_clip_path,
                destination_name=out_clip_filename,
                job_id=job_id,
                user_id=user_id,
            )

            # Generate thumbnail JPEG
            thumb_filename = f"{job_id}_{clip_id}_thumb.jpg"
            local_thumb_path = os.path.join(temp_dir, thumb_filename)
            thumbnail_url = None
            try:
                video_renderer.generate_thumbnail(local_clip_path, local_thumb_path, capture_timestamp=min(1.5, dur / 2.0))
                if os.path.exists(local_thumb_path) and os.path.getsize(local_thumb_path) > 0:
                    thumbnail_url = storage_service.upload_thumbnail(local_thumb_path, thumb_filename, job_id, user_id)
            except Exception as te:
                logger.warning("Could not generate thumbnail for clip %s: %s", clip_id, te)

            clip_record = {
                "id": clip_id,
                "job_id": job_id,
                "user_id": user_id,
                "category_id": category_id,
                "source_id": source_id,
                "title": social_data.get("title") or c_title,
                "hook_sentence": social_data.get("hook") or c_hook,
                "virality_reason": c_reason,
                "score": score_val,
                "start_time": start,
                "end_time": end,
                "duration": round(dur, 2),
                "aspect_ratio": aspect_ratio,
                "storage_path": storage_path,
                "storage_url": public_clip_url,
                "thumbnail_url": thumbnail_url,
                "publishing_status": "draft",
                "suggested_tags": social_data.get("hashtags") or category_def.default_hashtags,
                "is_quality_approved": val_result.is_valid,
            }
            db.create_clip(clip_record)
            rendered_clips.append(clip_record)
            db.add_log(job_id, "cropping_clips", f"Rendered and verified clip #{i}: '{clip_record['title']}' (Score: {score_val:.0f})")

        # Step 8: Complete Job
        db.update_job_progress(job_id, stage="completed", progress=100, status="completed")
        db.add_log(job_id, "completed", f"Job finished successfully. Generated {len(rendered_clips)} verified vertical shorts.")
        db.create_audit_log(
            action="job_completed",
            resource_type="job",
            user_id=user_id,
            resource_id=job_id,
            details={"clips_generated": len(rendered_clips), "category_id": category_id, "url": job.get("url")},
        )

    except Exception as e:
        logger.exception("Error processing job %s: %s", job_id, e)
        db.update_job_progress(job_id, stage="failed", progress=0, status="failed", error=str(e))
        db.add_log(job_id, "failed", f"Processing aborted: {str(e)}")
        db.create_audit_log(
            action="job_failed",
            resource_type="job",
            user_id=user_id,
            resource_id=job_id,
            details={"error": str(e), "category_id": category_id, "url": job.get("url")},
        )

    finally:
        # Safe cleanup of all temporary working files
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info("Cleaned temporary processing directory: %s", temp_dir)
            except Exception as cle:
                logger.warning("Could not clean temp directory %s: %s", temp_dir, cle)
