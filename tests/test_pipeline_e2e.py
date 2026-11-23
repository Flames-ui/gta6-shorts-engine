"""End-to-end: real FFmpeg, real PostgreSQL, real local storage. Transcription and the model's
judgement are supplied as deterministic fixtures so the media pipeline is what is being tested."""
import os

import pytest
from fastapi.testclient import TestClient

from app import states
from app.ai.fake import ScriptedProvider
from app.ai.provider import ProviderError
from app.captions.formats import parse_srt
from app.db import repo
from app.main import app
from app.media import probe as probe_mod
from app.pipeline.orchestrator import Deps, run_job
from app.storage import get_storage

from .conftest import make_token, requires_db, requires_ffmpeg
from .helpers import speech_timeline

pytestmark = [requires_db, requires_ffmpeg]
ALICE = {"Authorization": f"Bearer {make_token('user-alice')}"}
MOMENT_STARTS = [6.0, 36.0, 66.0, 96.0, 126.0]


def _highlight(m, score=88, **over):
    base = {"start_seconds": m["start"], "end_seconds": m["end"], "title": f"Insight {int(m['start'])}",
            "description": "A self-contained explanation.", "rationale": "Complete thought with a clear payoff.",
            "engagement_score": score, "confidence": 0.92, "category": "education", "standalone": True}
    base.update(over)
    return base


def _deps(timeline, highlights, *, provider=None):
    provider = provider or (lambda prompt: {"highlights": highlights})
    return Deps(
        storage=get_storage(),
        transcriber=lambda path, lang: timeline,
        provider_factory=lambda: ScriptedProvider(provider),
        youtube_inspect=lambda ref: {},
        youtube_download=lambda *a, **k: "",
        sleep=lambda s: None,
    )


@pytest.fixture
def job_for_pipeline(clean_db, fake_queue, storage, pipeline_video):
    client = TestClient(app)
    with open(pipeline_video, "rb") as fh:
        src = client.post("/api/v1/sources/upload", headers=ALICE,
                          files={"file": ("pipeline.mp4", fh, "video/mp4")}).json()
    job = client.post("/api/v1/jobs", headers=ALICE, json={"source_id": src["id"],
                                                           "caption_preset": "bold_creator"}).json()
    return job["id"]


def test_five_distinct_vertical_clips_are_produced_and_served(job_for_pipeline, pipeline_video):
    timeline = speech_timeline(MOMENT_STARTS)
    moments = timeline["moments"]
    highlights = [_highlight(m, score=s) for m, s in zip(moments, [91, 88, 86, 84, 80])]
    highlights.append(_highlight({"start": 40.0, "end": 49.0}))           # too short: must be rejected
    highlights.append(_highlight(moments[0], score=60))                   # below quality bar

    status = run_job(job_for_pipeline, worker_id="test-worker", deps=_deps(timeline, highlights))
    job = repo.get_job(job_for_pipeline)
    assert status == states.COMPLETED, job.get("error_message")
    assert job["status"] == states.COMPLETED and job["progress"] == 100

    clips = repo.list_clips(job_for_pipeline)
    assert [c["clip_number"] for c in clips] == [1, 2, 3, 4, 5]
    assert all(c["validation_status"] == "passed" for c in clips)
    assert len({c["start_seconds"] for c in clips}) == 5          # five distinct moments, not five copies

    for clip in clips:
        path = get_storage().local_path(clip["storage_key"])
        info = probe_mod.probe(path)
        assert (info.width, info.height) == (1080, 1920)
        assert info.video_codec == "h264" and info.audio_codec == "aac"
        assert info.duration == pytest.approx(float(clip["duration_seconds"]), abs=0.4)
        assert float(clip["duration_seconds"]) >= 15

    srt = get_storage().local_path(f"jobs/{job_for_pipeline}/clips/1/captions.srt")
    cues = parse_srt(open(srt, encoding="utf-8").read())
    limit = float(clips[0]["duration_seconds"]) + 0.01
    assert cues and all(0 <= start < end <= limit for start, end, _text in cues)

    highlights_saved = repo.list_highlights(job_for_pipeline)
    selected = [h for h in highlights_saved if h["selected"]]
    rejected = [h for h in highlights_saved if not h["selected"]]
    assert len(selected) == 5
    # The score-60 duplicate overlaps a stronger candidate and is merged away before selection; the
    # too-short candidate is refused during validation. Neither reaches the stored highlights.
    assert rejected == []
    assert len(highlights_saved) == 5

    # The API serves the same job, its clips and their downloads.
    client = TestClient(app)
    detail = client.get(f"/api/v1/jobs/{job_for_pipeline}", headers=ALICE).json()
    assert detail["status"] == "completed" and len(detail["clips"]) == 5
    link = client.get(f"/api/v1/clips/{clips[0]['id']}/download-url", headers=ALICE).json()
    body = client.get(link["url"]).content
    assert len(body) > 10_000
    thumb = get_storage().local_path(f"jobs/{job_for_pipeline}/clips/1/thumb.jpg")
    assert os.path.getsize(thumb) > 500


def test_insufficient_moments_fail_honestly_with_no_clips(job_for_pipeline):
    timeline = speech_timeline(MOMENT_STARTS)
    highlights = [_highlight(m) for m in timeline["moments"][:3]]
    status = run_job(job_for_pipeline, worker_id="test-worker", deps=_deps(timeline, highlights))
    job = repo.get_job(job_for_pipeline)
    assert status == states.FAILED
    assert job["error_code"] == "insufficient_highlights"
    assert "3" in job["error_message"] or "of 5" in job["error_message"]
    assert repo.list_clips(job_for_pipeline) == []
    with pytest.raises(Exception):  # nothing was rendered, so there is no clip file to find
        get_storage().local_path(f"jobs/{job_for_pipeline}/clips/1/clip.mp4")


def test_model_outage_requeues_then_fails_after_the_retry_limit(job_for_pipeline, fake_queue):
    timeline = speech_timeline(MOMENT_STARTS)

    def outage(prompt):
        return ProviderError("timeout", "The model timed out.", retryable=True)

    deps = _deps(timeline, [], provider=outage)
    results = [run_job(job_for_pipeline, worker_id="w", deps=deps) for _ in range(3)]
    assert results == [states.QUEUED, states.QUEUED, states.FAILED]
    job = repo.get_job(job_for_pipeline)
    assert job["status"] == states.FAILED and job["error_code"] == "ai_timeout"
    # The first entry is the enqueue at submission; then two retries with exponential backoff.
    assert [delay for _, delay in fake_queue.enqueued] == [0, 30, 60]


def test_cancelled_job_stops_before_rendering(job_for_pipeline):
    repo.request_cancel(job_for_pipeline, "user-alice")
    timeline = speech_timeline(MOMENT_STARTS)
    status = run_job(job_for_pipeline, worker_id="w", deps=_deps(timeline, [_highlight(m) for m in timeline["moments"]]))
    assert status == states.CANCELLED
    assert repo.list_clips(job_for_pipeline) == []


def test_corrupt_source_is_failed_with_a_clear_code(clean_db, fake_queue, storage):
    src = repo.create_source("user-alice", source_type="upload", status="ready",
                             file_key="sources/x/corrupt/original.mp4", sha256="d" * 64, title="Corrupt",
                             duration_seconds=60.0)
    key = "sources/x/corrupt/original.mp4"
    path = _write_garbage()
    get_storage().put_file(key, path, "video/mp4")
    job = repo.create_job("user-alice", str(src["id"]), clip_count=5, aspect_ratio="9:16",
                          caption_preset="bold_creator", min_clip_seconds=15, max_clip_seconds=90)
    timeline = speech_timeline(MOMENT_STARTS)
    status = run_job(str(job["id"]), worker_id="w", deps=_deps(timeline, []))
    assert status == states.FAILED
    assert repo.get_job(str(job["id"]))["error_code"] in {"unreadable_media", "no_video_stream"}
    assert repo.list_clips(str(job["id"])) == []


def _write_garbage():
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"\x00\x00\x00\x18ftypisom" + os.urandom(20000))
    return path
