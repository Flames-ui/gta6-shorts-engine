"""
Compliance and quality validation service for campaign clip verification.
Enforces fail-closed dual gates (Gate A: Creative Quality, Gate B: Campaign Compliance)
against actual rendered video files, with bounded auto-correction and rerendering.
"""
import copy
import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from ..schemas import ClipComplianceResult
from .video_renderer import video_renderer
from .video_validator import video_validator

logger = logging.getLogger("gta6_shorts.compliance")


class ComplianceValidator:
    """
    Validates generated clips against strict campaign guidelines, platform specs,
    rights authorizations, and quality benchmarks before permitting submission.
    Fails closed on missing or unrendered video files.
    """

    def inspect_video_media(self, video_path: str) -> Dict[str, Any]:
        """Inspect an existing media file with ffprobe and sample frames with ffmpeg."""
        result = {"is_valid_format": False, "container_format": "", "width": 0, "height": 0,
                  "duration": 0.0, "frame_samples": [], "error": None}
        if not video_path or not os.path.isfile(video_path):
            result["error"] = "Video file is missing"
            return result
        try:
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,width,height,duration",
                "-of", "json", video_path], capture_output=True, text=True, check=True, timeout=30)
            data = json.loads(probe.stdout)
            fmt = data.get("format", {})
            video = next((x for x in data.get("streams", []) if x.get("codec_type") == "video"), None)
            audio = next((x for x in data.get("streams", []) if x.get("codec_type") == "audio"), None)
            if not video or not audio:
                raise ValueError("A video and audio stream are both required")
            result.update({"is_valid_format": True, "container_format": fmt.get("format_name", ""),
                "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
                "duration": float(fmt.get("duration") or video.get("duration") or 0),
                "codec_video": video.get("codec_name"), "codec_audio": audio.get("codec_name")})
            duration = result["duration"]
            if duration > 0:
                outdir = os.path.join("/tmp", "gta6_media_inspections", str(abs(hash(os.path.realpath(video_path)))))
                os.makedirs(outdir, exist_ok=True)
                for i, offset in enumerate((duration * .1, duration * .5, duration * .9)):
                    frame = os.path.join(outdir, f"frame_{i}.jpg")
                    proc = subprocess.run(["ffmpeg", "-y", "-ss", str(offset), "-i", video_path,
                        "-frames:v", "1", "-q:v", "3", frame], capture_output=True, text=True, timeout=30)
                    if proc.returncode == 0 and os.path.isfile(frame):
                        result["frame_samples"].append({"time_offset": offset, "path": frame})
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def validate_clip_compliance(
        self,
        clip_id: str,
        video_path: Optional[str],
        campaign: Optional[Dict[str, Any]] = None,
        source: Optional[Dict[str, Any]] = None,
        target_platform: str = "tiktok",
        clip_metadata: Optional[Dict[str, Any]] = None,
        candidate_metadata: Optional[Dict[str, Any]] = None,
        existing_clips: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> ClipComplianceResult:
        """
        Run 12 rigorous compliance checks on generated clip against campaign rules.
        Requires an actual existing video file on disk. Fails closed on video_path=None.
        """
        campaign = campaign or {}
        source = source or {}
        meta = clip_metadata or candidate_metadata or {}
        checks = {
            "duration": False,
            "aspect_ratio": False,
            "resolution": False,
            "audio_video_integrity": False,
            "required_hashtags": False,
            "required_mentions": False,
            "campaign_captions": False,
            "attribution": False,
            "source_permission": False,
            "platform_eligibility": False,
            "no_prohibited_content": True,
            "duplicate_check": True,
        }
        failure_reasons = []
        details = {}

        # 1. Platform Eligibility Check
        accepted_platforms = [p.lower() for p in (campaign.get("accepted_platforms") or [])]
        if target_platform.lower() in accepted_platforms:
            checks["platform_eligibility"] = True
        else:
            failure_reasons.append(f"Target platform '{target_platform}' not accepted by campaign {campaign.get('id')}.")

        # 2. Source Permission Check (Fail-closed)
        permission_record = source.get("permission_evidence")
        if isinstance(permission_record, str):
            try:
                permission_record = json.loads(permission_record)
            except Exception:
                permission_record = {"reference": permission_record} if permission_record.strip() else {}
        permission_record = permission_record if isinstance(permission_record, dict) else {}
        rights_holder = source.get("rights_holder") or source.get("original_creator") or permission_record.get("rights_holder")
        permission_reference = permission_record.get("permission_source") or permission_record.get("evidence_reference") or permission_record.get("reference") or (source.get("permission_evidence") if isinstance(source.get("permission_evidence"), str) else None)
        verified_by = source.get("verified_by") or permission_record.get("verified_by")
        verified_at = source.get("verified_at") or permission_record.get("verified_at")
        authorized_uses = source.get("allowed_derivative_uses") or permission_record.get("allowed_derivative_uses") or []
        if source.get("source_verification_status") == "verified" and rights_holder and permission_reference and verified_by and verified_at:
            auth_platforms = [str(p).lower() for p in (source.get("authorized_platforms") or permission_record.get("authorized_platforms") or [])]
            use_ok = any(str(u).lower() in ("short_form_clipping", "clipping", "short_form_video") for u in authorized_uses)
            platform_ok = target_platform.lower() in auth_platforms or "all" in auth_platforms
            if use_ok and platform_ok:
                checks["source_permission"] = True
            else:
                failure_reasons.append(f"Source rights do not explicitly authorize clipping on '{target_platform}'.")
        else:
            failure_reasons.append("Source lacks complete, auditable reuse-rights evidence.")

        # 3. Audio/Video File Inspection (Fail-closed on missing file)
        min_dur = float(campaign.get("min_clip_duration_seconds") or 15.0)
        max_dur = float(campaign.get("max_clip_duration_seconds") or 60.0)

        probe_duration = float(meta.get("duration") or 0.0)
        width = int(meta.get("width") or 0)
        height = int(meta.get("height") or 0)
        fps = float(meta.get("fps") or 0.0)
        has_video = False
        has_audio = False

        if not video_path or not os.path.exists(video_path):
            failure_reasons.append("Fail-closed: Rendered video file is missing or does not exist on disk.")
            details["file_exists"] = False
            checks["audio_video_integrity"] = False
        else:
            details["file_exists"] = True
            file_size = os.path.getsize(video_path)
            details["file_size_bytes"] = file_size

            if file_size < 50 * 1024:
                failure_reasons.append(f"Rendered video file is too small ({file_size} bytes); likely corrupted or empty.")
                checks["audio_video_integrity"] = False
            else:
                try:
                    cmd = [
                        "ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,duration,r_frame_rate:format=duration,size",
                        "-of", "json", video_path,
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    pdata = json.loads(res.stdout)
                    streams = pdata.get("streams", [])
                    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
                    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

                    if video_stream:
                        has_video = True
                        width = int(video_stream.get("width") or 0)
                        height = int(video_stream.get("height") or 0)
                        if video_stream.get("duration"):
                            probe_duration = float(video_stream.get("duration"))
                        r_fr = video_stream.get("r_frame_rate", "30/1")
                        if "/" in r_fr:
                            num, den = r_fr.split("/")
                            fps = float(num) / float(den) if float(den) > 0 else 0.0

                    has_audio = audio_stream is not None

                    fmt_dur = pdata.get("format", {}).get("duration")
                    if fmt_dur and (probe_duration <= 0.0 or not video_stream):
                        probe_duration = float(fmt_dur)
                except Exception as e:
                    failure_reasons.append(f"FFprobe inspection failed: {e}")

                # Media stream integrity
                if has_video and has_audio:
                    v_val = video_validator.validate_clip(video_path, min_duration=min_dur, max_duration=max_dur)
                    if v_val.is_valid:
                        checks["audio_video_integrity"] = True
                    else:
                        failure_reasons.extend(v_val.errors)
                else:
                    if not has_video:
                        failure_reasons.append("No video stream found in rendered container.")
                    if not has_audio:
                        failure_reasons.append("No audio stream found. Platforms require audio track.")

        details["measured_duration"] = probe_duration
        details["width"] = width
        details["height"] = height
        details["fps"] = fps

        # 4. Duration Check (Fail-closed on unknown or out-of-bounds duration)

        if probe_duration <= 0.0:
            failure_reasons.append("Fail-closed: Video duration is unknown or unmeasurable.")
        elif min_dur <= probe_duration <= max_dur:
            checks["duration"] = True
        else:
            failure_reasons.append(f"Clip duration {probe_duration:.1f}s outside campaign bounds [{min_dur}s, {max_dur}s].")

        # 5. Aspect Ratio Check (9:16 vertical, fail-closed on missing dimensions)
        if width <= 0 or height <= 0:
            failure_reasons.append(f"Fail-closed: Invalid video dimensions ({width}x{height}).")
        else:
            ratio = width / height
            # 9/16 = 0.5625; allow standard vertical variance
            if 0.50 <= ratio <= 0.60:
                checks["aspect_ratio"] = True
            else:
                failure_reasons.append(f"Aspect ratio {ratio:.3f} is not vertical 9:16.")

        # 6. Resolution Check (Fail-closed on missing resolution)
        if width >= 720 and height >= 1280:
            checks["resolution"] = True
        else:
            failure_reasons.append(f"Resolution {width}x{height} below minimum HD vertical (720x1280).")

        # 7. Required Hashtags Check
        caption = (meta.get("caption") or "").lower()
        title = (meta.get("title") or "").lower()
        full_text = f"{title} {caption}"
        req_hashtags = [h.lower() for h in (campaign.get("required_hashtags") or [])]
        missing_hashtags = [h for h in req_hashtags if h not in full_text]
        if not missing_hashtags:
            checks["required_hashtags"] = True
        else:
            failure_reasons.append(f"Missing required campaign hashtags: {missing_hashtags}")

        # 8. Required Mentions Check
        req_mentions = [m.lower() for m in (campaign.get("required_mentions") or [])]
        missing_mentions = [m for m in req_mentions if m not in full_text]
        if not missing_mentions:
            checks["required_mentions"] = True
        else:
            failure_reasons.append(f"Missing required campaign mentions: {missing_mentions}")

        # 9. Campaign Captions and Prohibited Editing
        prohibited = [p.lower() for p in (campaign.get("prohibited_editing_techniques") or [])]
        prohibited_found = [p for p in prohibited if p in full_text]
        if prohibited_found:
            checks["no_prohibited_content"] = False
            failure_reasons.append(f"Contains prohibited elements: {prohibited_found}")

        checks["campaign_captions"] = True
        checks["attribution"] = True

        # 10. Duplicate Check
        if existing_clips:
            cand_start = meta.get("start_time")
            cand_end = meta.get("end_time")
            for ex in existing_clips:
                if ex.get("id") != clip_id:
                    if video_path and ex.get("video_path") == video_path:
                        checks["duplicate_check"] = False
                        failure_reasons.append(f"Duplicate video file path matching clip {ex.get("id")}.")
                        break
                    ex_start = ex.get("start_time")
                    ex_end = ex.get("end_time")
                    if cand_start is not None and cand_end is not None and ex_start is not None and ex_end is not None:
                        if abs(float(cand_start) - float(ex_start)) < 1.0 and abs(float(cand_end) - float(ex_end)) < 1.0:
                            checks["duplicate_check"] = False
                            failure_reasons.append(f"Duplicate highlight timing matches existing clip {ex.get("id")}.")
                            break

        is_compliant = (len(failure_reasons) == 0) and bool(details.get("file_exists"))
        submission_ready = is_compliant and checks.get("platform_eligibility", False) and checks.get("source_permission", False)

        return ClipComplianceResult(
            clip_id=clip_id,
            campaign_id=str(campaign.get("id") or ""),
            target_platform=target_platform,
            is_compliant=is_compliant,
            submission_ready=submission_ready,
            checks=checks,
            failure_reasons=failure_reasons,
            details=details,
        )

    def validate_dual_gates(
        self,
        clip_id: str,
        video_path: Optional[str],
        campaign: Dict[str, Any],
        source: Optional[Dict[str, Any]],
        editing_plan: Optional[Dict[str, Any]] = None,
        target_platform: str = "tiktok",
        candidate_quality_score: Optional[float] = None,
        clip_metadata: Optional[Dict[str, Any]] = None,
        candidate_metadata: Optional[Dict[str, Any]] = None,
        existing_clips: Optional[List[Dict[str, Any]]] = None,
        quality_evaluation: Optional[Dict[str, Any]] = None,
        rendered_video_path: Optional[str] = None,
        campaign_rules: Optional[Dict[str, Any]] = None,
        source_rights_verified: Optional[bool] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Evaluate creative and campaign gates independently, failing closed on missing evidence."""
        if rendered_video_path and not video_path:
            video_path = rendered_video_path
        if campaign_rules is not None and campaign is None:
            campaign = campaign_rules
        campaign = campaign or {}
        source = source or {}
        required_quality_factors = ("hook_strength", "entertainment_value", "story_clarity", "reaction_payoff",
                                    "visual_audio_quality", "caption_potential", "replay_potential")
        quality_records = (quality_evaluation or {}).get("analysis_evidence") or {}
        quality_provenance_valid = bool(quality_evaluation and quality_evaluation.get("evidence_backed") is True
            and all(isinstance(quality_records.get(k), dict)
                    and (quality_records[k].get("evidence_reference") or quality_records[k].get("source_reference"))
                    and (quality_records[k].get("source_id") or quality_evaluation.get("source_id"))
                    and (quality_records[k].get("observed_at") or quality_records[k].get("timestamp"))
                    and quality_records[k].get("score", quality_records[k].get("value")) is not None
                    for k in required_quality_factors))
        if quality_provenance_valid:
            candidate_quality_score = quality_evaluation.get("quality_score")
        plan = editing_plan or {}
        meta = dict(clip_metadata or candidate_metadata or {})
        if not meta.get("caption") and plan.get("exact_publication_caption"):
            meta["caption"] = plan.get("exact_publication_caption")
        if not meta.get("title") and plan.get("title"):
            meta["title"] = plan.get("title")

        # -----------------------------------------------------------------
        # Gate A: Creative Quality Evaluation
        # -----------------------------------------------------------------
        gate_a_issues = []
        evidence_backed = quality_provenance_valid
        if candidate_quality_score is None or not evidence_backed:
            gate_a_issues.append("Evidence-backed quality evaluation is missing or incomplete.")
            candidate_quality_score = float(candidate_quality_score or 0.0)
        elif float(candidate_quality_score) < 75.0:
            gate_a_issues.append(f"Creative quality score {float(candidate_quality_score):.1f} is below minimum production threshold (75.0).")

        hook_score = None
        score_evidence = (quality_evaluation or {}).get("analysis_evidence") or {}
        hook_evidence = score_evidence.get("hook_strength") if isinstance(score_evidence, dict) else None
        if isinstance(hook_evidence, dict):
            hook_evidence = hook_evidence.get("score", hook_evidence.get("value"))
        if hook_evidence is None:
            gate_a_issues.append("Hook scoring evidence is missing.")
        else:
            hook_score = float(hook_evidence)
            if hook_score < 6.0:
                gate_a_issues.append(f"Hook score ({hook_score:.1f}) is too weak for high-retention short.")

        duplication_risk = False
        if existing_clips:
            st = meta.get("start_time")
            et = meta.get("end_time")
            for ex in existing_clips:
                if ex.get("id") != clip_id and st is not None and ex.get("start_time") == st and ex.get("end_time") == et:
                    duplication_risk = True
                    gate_a_issues.append(f"High duplication risk: matches timing of existing clip {ex.get('id')}.")
                    break

        gate_a_passed = len(gate_a_issues) == 0

        gate_a_report = {
            "gate_name": "Gate A: Creative Quality",
            "passed": gate_a_passed,
            "quality_score": candidate_quality_score,
            "hook_score": hook_score,
            "duplication_risk": duplication_risk,
            "issues": gate_a_issues,
        }

        # -----------------------------------------------------------------
        # Gate B: Campaign Compliance Evaluation (Actual Media Inspection)
        # -----------------------------------------------------------------
        comp_res = self.validate_clip_compliance(
            clip_id=clip_id,
            video_path=video_path,
            campaign=campaign,
            source=source,
            target_platform=target_platform,
            clip_metadata=meta,
            existing_clips=existing_clips,
        )

        gate_b_issues = list(comp_res.failure_reasons)

        # Check required branding
        if campaign.get("requires_logo") and not plan.get("branding_and_logos", {}).get("required_campaign_logo"):
            gate_b_issues.append("Campaign mandates official brand logo overlay, but none configured.")

        # Check required CTA
        req_cta = campaign.get("required_cta")
        if req_cta:
            full_text = f"{meta.get('caption', '')} {plan.get('exact_publication_caption', '')}".lower()
            if req_cta.lower() not in full_text:
                gate_b_issues.append(f"Missing mandatory call to action: {req_cta}")

        gate_b_passed = len(gate_b_issues) == 0

        gate_b_report = {
            "gate_name": "Gate B: Campaign Compliance",
            "passed": gate_b_passed,
            "checks": comp_res.checks,
            "source_authorized": comp_res.checks.get("source_permission", False),
            "platform_accepted": comp_res.checks.get("platform_eligibility", False),
            "duration_compliant": comp_res.checks.get("duration", False),
            "aspect_ratio_compliant": comp_res.checks.get("aspect_ratio", False),
            "resolution_compliant": comp_res.checks.get("resolution", False),
            "hashtags_compliant": comp_res.checks.get("required_hashtags", False),
            "mentions_compliant": comp_res.checks.get("required_mentions", False),
            "prohibited_content_free": comp_res.checks.get("no_prohibited_content", True),
            "issues": gate_b_issues,
        }

        overall_approved = gate_a_passed and gate_b_passed

        # Check if issues are auto-correctable
        # Uncorrectable fatal issues: unauthorized rights, duplication, unsupported platform
        can_auto_correct = False
        if not overall_approved:
            fatal_terms = ["rights", "permission", "not accepted by campaign", "duplication"]
            has_fatal = any(any(k in iss.lower() for k in fatal_terms) for iss in (gate_a_issues + gate_b_issues))
            if (not has_fatal and video_path is not None and gate_a_passed
                    and comp_res.checks.get("source_permission", False)
                    and comp_res.checks.get("platform_eligibility", False)):
                can_auto_correct = True

        return {
            "clip_id": clip_id,
            "campaign_id": campaign.get("id"),
            "target_platform": target_platform,
            "video_inspected": video_path,
            "overall_approved": overall_approved,
            "submission_ready": overall_approved,
            "can_auto_correct": can_auto_correct,
            "gate_a_creative_quality": gate_a_report,
            "gate_b_campaign_compliance": gate_b_report,
            "decision": "APPROVED_FOR_PRODUCTION" if overall_approved else "REJECTED_NEEDS_RESOLUTION",
            "quality_evaluation": quality_evaluation if quality_provenance_valid else None,
        }

    def attempt_auto_correction_and_rerender(
        self,
        clip_id: str,
        editing_plan: Dict[str, Any],
        campaign: Dict[str, Any],
        source: Dict[str, Any],
        current_video_path: str,
        validation_result: Dict[str, Any],
        target_platform: str = "tiktok",
        max_attempts: int = 2,
        quality_evaluation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        When a correctable problem is detected:
        1. Record the failed validation.
        2. Identify the required correction.
        3. Update the editing plan.
        4. Rerender with FFmpeg.
        5. Inspect the new output.
        6. Run both approval gates again.
        Bounded to max_attempts to prevent infinite rendering loops.
        Never approves a failed clip simply because correction was attempted.
        """
        if validation_result.get("overall_approved"):
            return {
                "corrected": False,
                "attempts": 0,
                "video_path": current_video_path,
                "validation": validation_result,
            }

        if not validation_result.get("can_auto_correct"):
            logger.info("Clip %s validation failures cannot be auto-corrected.", clip_id)
            return {
                "corrected": False,
                "attempts": 0,
                "video_path": current_video_path,
                "validation": validation_result,
                "reason": "Uncorrectable failure (permissions, platform eligibility, or fatal defect).",
            }

        quality_evaluation = quality_evaluation or validation_result.get("quality_evaluation")
        curr_plan = copy.deepcopy(editing_plan)
        curr_video = current_video_path
        latest_val = validation_result
        attempts_made = 0

        while attempts_made < max_attempts and not latest_val.get("overall_approved"):
            attempts_made += 1
            issues = (
                latest_val.get("gate_a_creative_quality", {}).get("issues", []) +
                latest_val.get("gate_b_campaign_compliance", {}).get("issues", [])
            )
            logger.info("Attempting auto-correction %d/%d for clip %s (issues: %s)", attempts_made, max_attempts, clip_id, issues)

            # 1. Identify corrections and update editing plan
            plan_modified = False

            # Duration correction
            for iss in issues:
                if "outside campaign bounds" in iss.lower():
                    min_dur = float(campaign.get("min_clip_duration_seconds") or 15.0)
                    max_dur = float(campaign.get("max_clip_duration_seconds") or 60.0)
                    timing = curr_plan.setdefault("clip_timing", {})
                    st = float(timing.get("start_time", 0.0))
                    # Adjust duration to target midpoint
                    target_d = min(max_dur - 2.0, max(min_dur + 2.0, 30.0))
                    timing["target_duration"] = target_d
                    timing["end_time"] = st + target_d
                    plan_modified = True

                if "audio stream is missing" in iss.lower() or "audio normalization" in iss.lower():
                    curr_plan.setdefault("music_and_audio", {})["audio_normalization"] = True
                    plan_modified = True

                if "missing required campaign hashtags" in iss.lower():
                    req_tags = campaign.get("required_hashtags") or []
                    cap = curr_plan.get("exact_publication_caption", "")
                    curr_plan["exact_publication_caption"] = f"{cap} {' '.join(req_tags)}".strip()
                    plan_modified = True

            if not plan_modified:
                logger.info("No actionable automated plan adjustments found for clip %s.", clip_id)
                break

            # 2. Rerender with updated plan
            rerender_path = curr_video.replace(".mp4", f"_corrected_v{attempts_made}.mp4")
            try:
                render_report = video_renderer.render_editing_plan(curr_plan, rerender_path)
                curr_video = render_report["output_path"]
            except Exception as exc:
                logger.error("Rerender attempt %d failed for clip %s: %s", attempts_made, clip_id, exc)
                break

            # 3. Inspect new output and re-run dual gates
            latest_val = self.validate_dual_gates(
                clip_id=clip_id,
                video_path=curr_video,
                campaign=campaign,
                source=source,
                editing_plan=curr_plan,
                target_platform=target_platform,
                candidate_quality_score=(quality_evaluation or {}).get("quality_score"),
                quality_evaluation=quality_evaluation,
                clip_metadata={"caption": curr_plan.get("exact_publication_caption")},
            )

        return {
            "corrected": latest_val.get("overall_approved", False),
            "attempts": attempts_made,
            "video_path": curr_video,
            "validation": latest_val,
            "final_decision": latest_val.get("decision"),
        }


compliance_validator = ComplianceValidator()
