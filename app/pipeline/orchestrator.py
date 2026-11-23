"""The editing pipeline: one job in, five rendered and validated clips out (or a precise failure).

Stages, in order:
  validating -> [retrieving -> uploading_source] -> analyzing -> transcribing ->
  selecting_highlights -> extracting_clips -> rendering -> uploading_outputs ->
  validating_outputs -> completed

Rules enforced here:
  * A job becomes `completed` only after five clips are rendered, stored and validated.
  * Every status change is a compare-and-set transition with an event row.
  * Cancellation and retryable failures are honoured at stage boundaries and inside FFmpeg.
  * Temporary files live in a per-job directory that is always removed.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .. import states
from ..ai import analysis
from ..ai.provider import HighlightProvider
from ..captions import formats
from ..captions.timing import build_cues, get_style, words_in_window
from ..config import settings
from ..db import repo
from ..errors import Conflict, MediaValidationError, PipelineError, RetrievalError, StudioError
from ..media import probe as probe_mod
from ..media import render, youtube
from ..media.ffmpeg import CancelledError
from ..media.urls import YouTubeRef, parse_youtube_url
from ..storage import Storage, get_storage
from . import selection

logger = logging.getLogger("studio.pipeline")
CLIP_COUNT = 5
CANDIDATES_PER_WINDOW = 8
VERSION = "3.0.0"


class _Cancelled(Exception):
    """Internal signal: the owner asked to cancel this job."""


@dataclass
class Deps:
    storage: Storage
    transcriber: Callable[[str, Optional[str]], Dict]
    provider_factory: Callable[[], HighlightProvider]
    youtube_inspect: Callable[[YouTubeRef], Dict]
    youtube_download: Callable[..., str]
    sleep: Callable[[float], None] = time.sleep


def default_deps() -> Deps:
    from ..transcription import transcribe

    def provider() -> HighlightProvider:
        if settings.AI_PROVIDER.lower() == "gemini":
            if not settings.GEMINI_API_KEY:
                raise PipelineError("ai_not_configured", "Highlight analysis is not configured on this server.")
            from ..ai.gemini import GeminiProvider

            return GeminiProvider()
        raise PipelineError("ai_not_configured", "No highlight analysis provider is configured.")

    return Deps(
        storage=get_storage(),
        transcriber=transcribe,
        provider_factory=provider,
        youtube_inspect=youtube.inspect,
        youtube_download=youtube.download,
    )


def owner_segment(owner_id: str) -> str:
    return hashlib.sha256(owner_id.encode()).hexdigest()[:24]


def source_key(owner_id: str, source_id: str, extension: str) -> str:
    return f"sources/{owner_segment(owner_id)}/{source_id}/original{extension}"


def clip_key(job_id: str, clip_number: int, name: str) -> str:
    return f"jobs/{job_id}/clips/{clip_number}/{name}"


class _Heartbeat:
    def __init__(self, job_id: str, worker_id: str):
        self.job_id, self.worker_id = job_id, worker_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"hb-{job_id[:8]}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(settings.HEARTBEAT_INTERVAL_SECONDS):
            try:
                repo.set_job_stage(self.job_id, worker_id=self.worker_id)
                repo.heartbeat(self.worker_id, hostname=socket.gethostname(), pid=os.getpid(), status="busy",
                               current_job_id=self.job_id, version=VERSION)
            except Exception as exc:  # heartbeat failures must not kill the job
                logger.warning("heartbeat failed", extra={"job_id": self.job_id, "error_type": type(exc).__name__})


class _Run:
    def __init__(self, job_id: str, worker_id: str, deps: Deps):
        self.job_id = job_id
        self.worker_id = worker_id
        self.deps = deps
        self.temp = os.path.join(settings.TEMP_DIR, f"job-{job_id}")
        self.abort = threading.Event()

    def log(self, level: int, message: str, **fields) -> None:
        logger.log(level, message, extra={"job_id": self.job_id, "worker_id": self.worker_id, **fields})

    def event(self, name: str, message: str = "", stage: Optional[str] = None, **details) -> None:
        self.log(logging.INFO, message or name, event=name, stage=stage, **details)
        try:
            repo.add_event(self.job_id, name, message=message, stage=stage, details=details)
        except Exception as exc:
            logger.warning("could not record event", extra={"job_id": self.job_id, "event": name,
                                                             "error_type": type(exc).__name__})

    def cancel_requested(self) -> bool:
        return self.abort.is_set() or repo.is_cancel_requested(self.job_id)

    def checkpoint(self) -> None:
        if self.cancel_requested():
            raise _Cancelled()

    def advance(self, to: str, *, message: str, progress: Optional[int] = None) -> None:
        self.checkpoint()
        repo.transition_job(self.job_id, to, stage=to, progress=progress, message=message,
                            worker_id=self.worker_id)
        self.log(logging.INFO, message, event="stage_started", stage=to, progress=progress)

    def progress(self, value: int, stage: Optional[str] = None) -> None:
        repo.set_job_stage(self.job_id, stage=stage, progress=value, worker_id=self.worker_id)


def _mark_failed(run: _Run, code: str, message: str) -> str:
    current = repo.get_job(run.job_id)
    if current and current["status"] not in states.TERMINAL:
        try:
            repo.transition_job(run.job_id, states.FAILED, error_code=code, error_message=message,
                                message=message, worker_id=run.worker_id)
        except Conflict:
            # Another actor (for example a cancellation) changed the job first; keep its outcome.
            run.log(logging.WARNING, "failure not recorded; job changed concurrently", event="job_state_race")
            return states.FAILED
    run.log(logging.ERROR, "job failed", event="job_failed", error_code=code)
    return states.FAILED


def _fail_or_retry(run: _Run, code: str, message: str, retryable: bool) -> str:
    current = repo.get_job(run.job_id)
    attempts = int(current.get("attempt_count") or 0) if current else 0
    if retryable and current and current["status"] in states.ACTIVE \
            and attempts + 1 < settings.MAX_JOB_ATTEMPTS:
        delay = min(900, 30 * (2 ** attempts))
        repo.transition_job(run.job_id, states.QUEUED, message=f"Retrying after: {message}",
                            error_code=None, worker_id=run.worker_id,
                            details={"reason": code, "retry_in_seconds": delay})
        from ..queue import get_queue

        get_queue().enqueue(run.job_id, delay_seconds=delay)
        run.log(logging.WARNING, "job scheduled for retry", event="job_retry_scheduled",
                error_code=code, retry_in_seconds=delay, attempt=attempts + 1)
        return states.QUEUED
    return _mark_failed(run, code, message)


def _mark_cancelled(run: _Run) -> str:
    current = repo.get_job(run.job_id)
    if current and current["status"] not in states.TERMINAL:
        try:
            repo.transition_job(run.job_id, states.CANCELLED, message="Cancelled by the owner",
                                worker_id=run.worker_id)
        except Conflict:
            run.log(logging.WARNING, "cancellation raced another status change", event="job_state_race")
    run.log(logging.INFO, "job cancelled", event="job_cancelled")
    return states.CANCELLED


def run_job(job_id: str, worker_id: str = "worker", deps: Optional[Deps] = None) -> str:
    """Run one job to a terminal state (or back to queued for a retry). Returns the resulting status."""
    deps = deps or default_deps()
    job = repo.get_job(job_id)
    if job is None:
        logger.error("job not found", extra={"job_id": job_id, "event": "job_missing"})
        return "missing"
    if job["status"] in states.TERMINAL:
        return job["status"]

    run = _Run(job_id, worker_id, deps)
    heartbeat = _Heartbeat(job_id, worker_id)
    heartbeat.start()
    run.log(logging.INFO, "job started", event="job_started", status=job["status"])
    try:
        if job.get("cancel_requested"):
            raise _Cancelled()
        _pipeline(run, job)
        return states.COMPLETED
    except _Cancelled:
        return _mark_cancelled(run)
    except CancelledError:
        return _mark_cancelled(run)
    except PipelineError as exc:
        return _fail_or_retry(run, exc.code, exc.message, exc.retryable)
    except RetrievalError as exc:
        return _fail_or_retry(run, exc.code, exc.message, exc.retryable)
    except StudioError as exc:
        return _fail_or_retry(run, exc.code, exc.message, bool(exc.details.get("retryable")))
    except Exception as exc:  # never leak internals to the client; log for operators
        logger.exception("unexpected pipeline error", extra={"job_id": job_id, "event": "internal_error",
                                                             "error_type": type(exc).__name__})
        return _mark_failed(run, "internal_error",
                            "Processing stopped unexpectedly. Retry the job or contact support.")
    finally:
        heartbeat.stop()
        shutil.rmtree(run.temp, ignore_errors=True)


def _pipeline(run: _Run, job: Dict) -> None:
    deps = run.deps
    os.makedirs(run.temp, exist_ok=True)
    source = repo.get_source(job["source_id"])
    if source is None:
        raise PipelineError("source_missing", "The source for this job no longer exists.")

    run.advance(states.VALIDATING, message="Validating job and source", progress=3)
    if source["status"] == "failed":
        raise PipelineError("source_failed", source.get("failure_message") or "The source failed retrieval.")
    if source["source_type"] == "youtube" and source["status"] != "ready":
        _retrieve_youtube(run, source)
        source = repo.get_source(source["id"])
        run.advance(states.ANALYZING, message="Source stored privately; analysing", progress=20)
    else:
        run.advance(states.ANALYZING, message="Source ready; analysing", progress=20)

    # ---- analysing: local copy, integrity and measurements -----------------
    src_path = _local_source(run, source)
    info = probe_mod.probe(src_path)
    probe_mod.check_source_limits(info)
    probe_mod.verify_decodable(src_path)
    silence = probe_mod.silence_intervals(src_path, info.duration)
    run.event("source_validated", "Source decoded and measured", stage=states.ANALYZING,
              duration=info.duration, width=info.width, height=info.height, has_audio=info.has_audio)
    repo.record_usage(job["owner_id"], run.job_id, "source_seconds", info.duration)

    # ---- transcribing --------------------------------------------------------
    run.advance(states.TRANSCRIBING, message="Transcribing speech", progress=35)
    transcript = deps.transcriber(src_path, job.get("language"))
    repo.record_usage(job["owner_id"], run.job_id, "transcription_seconds", float(transcript["duration"]))
    run.event("transcribed", "Transcript ready", stage=states.TRANSCRIBING,
              segments=len(transcript["segments"]), language=transcript.get("language"))

    # ---- selecting highlights -----------------------------------------------
    run.advance(states.SELECTING, message="Analysing content and selecting moments", progress=50)
    outcome = analysis.analyze(
        provider=deps.provider_factory(),
        segments=transcript["segments"],
        duration=info.duration,
        title=source.get("title") or "",
        language=transcript.get("language"),
        min_seconds=int(job["min_clip_seconds"]),
        max_seconds=int(job["max_clip_seconds"]),
        wanted=CANDIDATES_PER_WINDOW,
        max_retries=settings.AI_MAX_RETRIES,
        sleep=deps.sleep,
        on_window=lambda n, total: run.progress(50 + int(8 * n / max(1, total)), states.SELECTING),
    )
    repo.record_usage(job["owner_id"], run.job_id, "ai_input_tokens", outcome.usage.input_tokens)
    repo.record_usage(job["owner_id"], run.job_id, "ai_output_tokens", outcome.usage.output_tokens)
    candidates = analysis.merge_overlapping(outcome.candidates)
    words = [w for seg in transcript["segments"] for w in seg.get("words", [])]
    decisions = selection.evaluate(
        candidates,
        segments=transcript["segments"],
        words=words,
        silence=silence,
        duration=info.duration,
        min_seconds=int(job["min_clip_seconds"]),
        max_seconds=int(job["max_clip_seconds"]),
        min_score=settings.MIN_CLIP_SCORE,
    )
    chosen, decisions = selection.choose(decisions, CLIP_COUNT)
    ranked = sorted(decisions, key=lambda d: (-d.candidate.engagement_score, -d.candidate.confidence, d.start))
    persistable = [d for d in ranked if d.end > d.start]
    highlight_ids = repo.replace_highlights(run.job_id, [
        {
            "candidate_rank": i,
            "start_seconds": d.start,
            "end_seconds": d.end,
            "duration_seconds": d.duration,
            "suggested_title": d.candidate.title,
            "description": d.candidate.description,
            "transcript": d.text[:4000],
            "selection_rationale": d.candidate.rationale,
            "engagement_score": d.candidate.engagement_score,
            "content_category": d.candidate.category,
            "confidence": round(d.candidate.confidence, 3),
            "selected": d.accepted and d in chosen,
            "rejection_reason": None if d in chosen else (d.reason or "not_selected"),
        }
        for i, d in enumerate(persistable, 1)
    ])
    run.event("highlights_selected", "Highlight selection complete", stage=states.SELECTING,
              proposed=len(outcome.candidates), accepted=len(chosen), rejected=len(decisions) - len(chosen),
              rejected_windows=len(outcome.rejected))
    selection.ensure_five(chosen, decisions, CLIP_COUNT)

    # ---- extracting clips and captions -------------------------------------
    run.advance(states.EXTRACTING, message="Preparing five clips and captions", progress=58)
    style = get_style(job["caption_preset"])
    plans = []
    id_by_rank = {i: highlight_ids[i - 1] for i in range(1, len(persistable) + 1)}
    for number, dec in enumerate(chosen, 1):
        rank = persistable.index(dec) + 1
        cue_words, exact = words_in_window(transcript["segments"], dec.start, dec.end)
        cues = build_cues(cue_words, style, dec.duration)
        files = {
            "srt": formats.to_srt(cues),
            "vtt": formats.to_vtt(cues),
            "ass": formats.to_ass(cues, style, settings.OUTPUT_WIDTH, settings.OUTPUT_HEIGHT),
        }
        sync = formats.validate_sync(cues, dec.duration, cue_words, files)
        if not sync["ok"]:
            raise PipelineError("caption_sync_failed",
                                f"Captions for clip {number} failed synchronisation checks: {sync['issues'][0]}")
        clip = repo.create_clip(
            job_id=run.job_id,
            owner_id=job["owner_id"],
            highlight_id=id_by_rank.get(rank),
            clip_number=number,
            title=dec.candidate.title[:120],
            description=dec.candidate.description or None,
            start_seconds=dec.start,
            end_seconds=dec.end,
            duration_seconds=dec.duration,
            render_status="pending",
            validation_status="pending",
        )
        clip_dir = os.path.join(run.temp, f"clip-{number}")
        os.makedirs(clip_dir, exist_ok=True)
        for fmt, text in files.items():
            with open(os.path.join(clip_dir, f"captions.{fmt}"), "w", encoding="utf-8") as fh:
                fh.write(text)
        plans.append({"number": number, "clip": clip, "dec": dec, "dir": clip_dir, "cues": len(cues),
                      "sync": sync, "files": files, "exact_word_timing": exact})

    # ---- rendering -----------------------------------------------------------
    run.advance(states.RENDERING, message="Rendering five vertical clips", progress=65)
    rendered: Dict[int, Dict] = {}
    done = {"n": 0}
    done_lock = threading.Lock()

    def render_one(plan: Dict) -> None:
        dec, clip_dir = plan["dec"], plan["dir"]
        repo.update_clip(plan["clip"]["id"], render_status="rendering")
        out_path = os.path.join(clip_dir, "clip.mp4")
        ass_path = os.path.join(clip_dir, "captions.ass")
        started = time.monotonic()
        geom = render.render_clip(
            source=src_path, start=dec.start, end=dec.end, out_path=out_path, src=info, ass_path=ass_path,
            cancel_check=run.cancel_requested,
        )
        thumb_path = os.path.join(clip_dir, "thumb.jpg")
        render.thumbnail(out_path, thumb_path, at_seconds=dec.duration / 2)
        repo.update_clip(plan["clip"]["id"], render_status="rendered", width=geom.out_w, height=geom.out_h,
                         video_codec="h264", audio_codec="aac" if info.has_audio else None)
        rendered[plan["number"]] = {"mp4": out_path, "thumb": thumb_path, "geom": geom,
                                    "render_seconds": time.monotonic() - started}
        with done_lock:
            done["n"] += 1
            run.progress(65 + int(26 * done["n"] / CLIP_COUNT), states.RENDERING)

    with ThreadPoolExecutor(max_workers=settings.MAX_PARALLEL_RENDERS, thread_name_prefix="render") as pool:
        futures = [pool.submit(render_one, p) for p in plans]
        try:
            for fut in as_completed(futures):
                fut.result()
        except BaseException:
            run.abort.set()
            raise
    render_seconds = sum(r["render_seconds"] for r in rendered.values())
    repo.record_usage(job["owner_id"], run.job_id, "render_seconds", render_seconds)
    run.event("clips_rendered", "Five clips rendered", stage=states.RENDERING, render_seconds=round(render_seconds, 2))

    # ---- uploading outputs ---------------------------------------------------
    run.advance(states.UPLOADING_OUTPUTS, message="Storing clips, thumbnails and captions", progress=92)
    storage = run.deps.storage
    for plan in plans:
        n = plan["number"]
        out = rendered[n]
        mp4_key = clip_key(run.job_id, n, "clip.mp4")
        thumb_key = clip_key(run.job_id, n, "thumb.jpg")
        size = storage.put_file(mp4_key, out["mp4"], "video/mp4")
        storage.put_file(thumb_key, out["thumb"], "image/jpeg")
        for fmt in ("srt", "vtt", "ass"):
            storage.put_file(clip_key(run.job_id, n, f"captions.{fmt}"), os.path.join(plan["dir"], f"captions.{fmt}"),
                             "text/plain; charset=utf-8")
        caption_key = clip_key(run.job_id, n, "captions.ass")
        repo.update_clip(plan["clip"]["id"], storage_key=mp4_key, thumbnail_key=thumb_key, caption_key=caption_key,
                         file_size_bytes=size)
        for fmt in ("srt", "vtt", "ass"):
            repo.save_caption(
                plan["clip"]["id"], fmt,
                storage_key=clip_key(run.job_id, n, f"captions.{fmt}"),
                cue_count=plan["cues"],
                transcript_text=plan["dec"].text[:4000],
                language=transcript.get("language"),
                word_timestamps=plan["exact_word_timing"],
                sync_validation=plan["sync"],
            )
        repo.record_usage(job["owner_id"], run.job_id, "storage_bytes", size)

    # ---- validating outputs -------------------------------------------------
    run.advance(states.VALIDATING_OUTPUTS, message="Validating the five rendered files", progress=96)
    for plan in plans:
        n = plan["number"]
        out = rendered[n]
        geom = out["geom"]
        try:
            details = render.validate_output(
                out["mp4"], expect_w=geom.out_w, expect_h=geom.out_h,
                expect_duration=plan["dec"].duration, expect_audio=info.has_audio,
            )
        except MediaValidationError as exc:
            repo.update_clip(plan["clip"]["id"], validation_status="failed",
                             validation_details={"error": exc.code})
            raise PipelineError("output_validation_failed",
                                f"Clip {n} did not pass output validation: {exc.message}") from exc
        details["sha256"] = _sha256(out["mp4"])
        details["caption_sync"] = plan["sync"]
        repo.update_clip(plan["clip"]["id"], validation_status="passed", validation_details=details)

    final_clips = repo.list_clips(run.job_id)
    if len(final_clips) != CLIP_COUNT or any(c["validation_status"] != "passed" for c in final_clips):
        raise PipelineError("output_incomplete", "Not all five clips passed validation.")
    run.advance(states.COMPLETED, message="Five clips are ready", progress=100)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_source(run: _Run, source: Dict) -> str:
    storage = run.deps.storage
    if storage.name == "local":
        return storage.local_path(source["file_key"])
    ext = os.path.splitext(source["file_key"])[1] or ".mp4"
    dest = os.path.join(run.temp, f"source{ext}")
    storage.download_to(source["file_key"], dest)
    return dest


def _retrieve_youtube(run: _Run, source: Dict) -> None:
    deps = run.deps
    run.advance(states.RETRIEVING, message="Retrieving video metadata and media", progress=8)
    repo.update_source(source["id"], status="retrieving")
    ref = parse_youtube_url(source["original_url"])
    meta = deps.youtube_inspect(ref)
    repo.update_source(source["id"], title=meta.get("title"), channel_title=meta.get("channel_title"),
                       duration_seconds=round(meta["duration"], 3))

    def on_progress(frac: float) -> None:
        run.progress(8 + int(6 * frac), states.RETRIEVING)

    path = deps.youtube_download(ref, run.temp, progress=on_progress, cancel_check=run.cancel_requested)
    run.advance(states.UPLOADING_SOURCE, message="Storing source privately", progress=15)
    info = probe_mod.probe(path)
    probe_mod.check_source_limits(info)
    probe_mod.verify_decodable(path)
    ext = os.path.splitext(path)[1].lower() or ".mp4"
    digest = _sha256(path)
    duplicate = repo.find_live_upload_by_sha(source["owner_id"], digest)
    if duplicate and str(duplicate["id"]) != str(source["id"]):
        raise PipelineError("duplicate_source", "This video is already in your library as another source.")
    key = source_key(source["owner_id"], str(source["id"]), ext)
    size = deps.storage.put_file(key, path, "video/mp4")
    repo.update_source(
        source["id"], status="ready", file_key=key, file_size_bytes=size, sha256=digest,
        container_format=info.format_name, video_codec=info.video_codec, audio_codec=info.audio_codec,
        width=info.width, height=info.height, has_audio=info.has_audio, duration_seconds=info.duration,
    )
    repo.record_usage(source["owner_id"], run.job_id, "storage_bytes", size)
    run.event("source_retrieved", "Source retrieved and stored", stage=states.RETRIEVING,
              duration=info.duration, size_bytes=size)


def new_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def run_job_task(job_id: str) -> None:
    """RQ entry point. A Redis lock prevents two workers from running the same job at once."""
    from ..queue import get_queue

    queue = get_queue()
    token = queue.acquire_lock(job_id)
    if token is None:
        logger.info("job already running elsewhere; skipping duplicate execution",
                    extra={"job_id": job_id, "event": "duplicate_skipped"})
        return
    try:
        run_job(job_id, worker_id=os.environ.get("WORKER_ID") or new_worker_id())
    finally:
        queue.release_lock(job_id, token)
