"""Regression tests for discovered-content metadata, authorized media, validation and moment analysis.

Real FFmpeg and FFprobe are used for media validation and detection. Network calls (YouTube Data API,
media hosts) are replaced with fakes, so the suite never contacts YouTube or a media host.
"""
import glob
import hashlib
import json
import logging
import os
import shutil
import subprocess

import pytest

from app.config import settings
from app.services.discovery import media_ingestion, media_pipeline, moment_detection, youtube_metadata
from app.services.discovery.base import RightsStatus
from app.services.discovery.media_ingestion import MediaIngestionError
from app.services.discovery.youtube_metadata import (
    YouTubeMetadataError,
    availability_for,
    fetch_video_metadata,
    parse_iso8601_duration,
    video_id_from_url,
)

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not installed")

PRODUCTION_SOURCE = {
    "id": "src_08a19e7ab7",
    "user_id": None,
    "name": "Shirrako",
    "source_type": "youtube",
    "is_active": True,
    "auto_clip_enabled": True,
    "category_id": "gta6",
    "channel_url": "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
    "feed_url": "",
    "status": "active",
}
PRODUCTION_ITEM = {
    "id": "yt_dQw4w9WgXcQ",
    "source_id": "src_08a19e7ab7",
    "user_id": None,
    "title": "GTA 6 discovered upload",
    "original_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "media_url": None,
    "creator_name": "Shirrako",
    "category_id": "gta6",
    "rights_status": "authorized_platforms",
    "duration_seconds": 0.0,
    "is_processed": False,
    "metadata_status": "pending",
    "media_status": "awaiting_authorized_media",
    "analysis_status": "not_started",
    "media_probe": {},
    "analysis_candidates": [],
}
AUTHORIZED_PERMISSION = {
    "rights_status": RightsStatus.AUTHORIZED_PLATFORMS,
    "allowed_platforms": ["tiktok", "youtube_shorts"],
    "expires_at": None,
    "revoked_at": None,
}


class FakeDB:
    def __init__(self, item=None, source=PRODUCTION_SOURCE):
        self.items = {item["id"]: dict(item)} if item else {}
        self.sources = {source["id"]: dict(source)} if source else {}
        self.writes = []

    def get_discovered_content(self, item_id):
        return dict(self.items[item_id]) if item_id in self.items else None

    def update_discovered_content(self, item_id, updates):
        self.writes.append(dict(updates))
        self.items[item_id].update(updates)
        return dict(self.items[item_id])

    def get_source(self, source_id):
        return dict(self.sources[source_id]) if source_id in self.sources else None

    def all_written_keys(self):
        return {key for write in self.writes for key in write}


class FakePermissions:
    def __init__(self, permission):
        self.permission = permission

    def get_permission(self, source_id):
        return dict(self.permission) if self.permission else None


class FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size=1024):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


def _fake_api_payload(**overrides):
    item = {
        "id": "dQw4w9WgXcQ",
        "snippet": {
            "title": "GTA 6 chase in Vice City",
            "channelTitle": "Shirrako",
            "channelId": "UCabcdefghijklmnopqrstuv",
            "publishedAt": "2026-11-20T10:00:00Z",
            "thumbnails": {"default": {"url": "https://i.ytimg.com/d.jpg"}, "high": {"url": "https://i.ytimg.com/h.jpg"}},
        },
        "contentDetails": {"duration": "PT1M5.5S", "regionRestriction": {"blocked": ["DE"]}},
        "status": {"privacyStatus": "public", "uploadStatus": "processed", "embeddable": True},
    }
    item.update(overrides)
    return {"items": [item]}


@pytest.fixture
def pipeline_db(monkeypatch):
    db = FakeDB(PRODUCTION_ITEM)
    monkeypatch.setattr(media_pipeline, "db", db)
    monkeypatch.setattr(media_pipeline, "rights_manager", FakePermissions(AUTHORIZED_PERMISSION))
    return db


@pytest.fixture
def no_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("network must not be used in this test")
    monkeypatch.setattr(media_ingestion.requests, "get", boom)
    monkeypatch.setattr(youtube_metadata.requests, "get", boom)


@pytest.fixture
def public_host(monkeypatch):
    """Treat test hostnames as public. Real SSRF checks are covered by the IP-literal tests."""
    monkeypatch.setattr(media_ingestion, "validate_safe_url", lambda url, require_https=True, **kw: url)


def make_fixture_video(path, seconds=4, width=320, height=240, audio=True):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={width}x{height}:rate=30"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=800:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return str(path)


# ---------------------------------------------------------------- YouTube metadata

@pytest.mark.parametrize("value,seconds", [
    ("PT1M5.5S", 65.5),
    ("PT1H2M3S", 3723.0),
    ("P1DT0S", 86400.0),
    ("PT0S", 0.0),
    ("PT45S", 45.0),
])
def test_parse_iso8601_duration_values(value, seconds):
    assert parse_iso8601_duration(value) == pytest.approx(seconds)


@pytest.mark.parametrize("value", ["", "soon", "P", "PT", "1M5S"])
def test_parse_iso8601_duration_rejects_unreadable_values(value):
    with pytest.raises(ValueError):
        parse_iso8601_duration(value)


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", None),
    ("https://example.com/watch?v=dQw4w9WgXcQ", None),
    (None, None),
])
def test_video_id_from_url(url, expected):
    assert video_id_from_url(url) == expected


def test_metadata_requires_api_key_and_never_calls_network(monkeypatch, no_network):
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "")
    with pytest.raises(YouTubeMetadataError) as exc:
        fetch_video_metadata(["dQw4w9WgXcQ"])
    assert exc.value.code == "youtube_api_key_missing"


def test_metadata_parses_official_api_fields(monkeypatch):
    seen = {}

    class ApiResponse:
        status_code = 200

        def json(self):
            return _fake_api_payload()

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(params or {})
        return ApiResponse()

    monkeypatch.setattr(youtube_metadata.requests, "get", fake_get)
    found = fetch_video_metadata(["dQw4w9WgXcQ"], api_key="test-key")
    meta = found["dQw4w9WgXcQ"]
    assert seen["id"] == "dQw4w9WgXcQ" and seen["key"] == "test-key"
    assert meta["duration_seconds"] == pytest.approx(65.5)
    assert meta["title"] == "GTA 6 chase in Vice City"
    assert meta["channel_title"] == "Shirrako"
    assert meta["thumbnail_url"] == "https://i.ytimg.com/h.jpg"
    assert meta["region_blocked_count"] == 1
    assert meta["availability"] == "available"


def test_unreadable_duration_is_none_not_zero(monkeypatch):
    class ApiResponse:
        status_code = 200

        def json(self):
            return _fake_api_payload(contentDetails={"duration": "P"})

    monkeypatch.setattr(youtube_metadata.requests, "get", lambda *a, **k: ApiResponse())
    assert fetch_video_metadata(["dQw4w9WgXcQ"], api_key="k")["dQw4w9WgXcQ"]["duration_seconds"] is None


def test_private_and_unprocessed_videos_are_not_available():
    assert availability_for({"privacy_status": "private", "upload_status": "processed"}) == "private"
    assert availability_for({"privacy_status": "public", "upload_status": "rejected"}) == "unavailable"
    assert availability_for({"privacy_status": "unlisted", "upload_status": "processed"}) == "available"


# ---------------------------------------------------------------- metadata stage

def test_production_source_without_api_key_records_failure_and_no_duration(monkeypatch, pipeline_db, no_network, caplog):
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "")
    caplog.set_level(logging.INFO, logger="gta6_shorts.discovery.pipeline")
    result = media_pipeline.resolve_metadata("yt_dQw4w9WgXcQ")
    assert result["metadata_status"] == "failed"
    assert result["metadata_error"] == "youtube_api_key_missing"
    assert "duration_seconds" not in pipeline_db.all_written_keys()
    assert "is_processed" not in pipeline_db.all_written_keys()
    assert any('"stage": "metadata"' in r.getMessage() and '"outcome": "failed"' in r.getMessage() for r in caplog.records)


def test_resolved_metadata_sets_duration_and_keeps_item_unprocessed(monkeypatch, pipeline_db):
    class ApiResponse:
        status_code = 200

        def json(self):
            return _fake_api_payload()

    monkeypatch.setattr(youtube_metadata.requests, "get", lambda *a, **k: ApiResponse())
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    result = media_pipeline.resolve_metadata("yt_dQw4w9WgXcQ")
    stored = pipeline_db.items["yt_dQw4w9WgXcQ"]
    assert result["metadata_status"] == "resolved"
    assert stored["duration_seconds"] == pytest.approx(65.5)
    assert stored["creator_name"] == "Shirrako"
    assert stored["availability"] == "available"
    assert stored["is_processed"] is False


def test_video_missing_from_api_fails_closed(monkeypatch, pipeline_db):
    class EmptyResponse:
        status_code = 200

        def json(self):
            return {"items": []}

    monkeypatch.setattr(youtube_metadata.requests, "get", lambda *a, **k: EmptyResponse())
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    assert media_pipeline.resolve_metadata("yt_dQw4w9WgXcQ")["metadata_error"] == "video_not_found"


def test_private_video_is_recorded_as_failed(monkeypatch, pipeline_db):
    class ApiResponse:
        status_code = 200

        def json(self):
            return _fake_api_payload(status={"privacyStatus": "private", "uploadStatus": "processed"})

    monkeypatch.setattr(youtube_metadata.requests, "get", lambda *a, **k: ApiResponse())
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    result = media_pipeline.resolve_metadata("yt_dQw4w9WgXcQ")
    assert result["metadata_error"] == "video_not_available:private"


# ---------------------------------------------------------------- media access policy

@pytest.mark.parametrize("url", [
    "http://media.rights-holder.example/clip.mp4",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://rr3---sn-abc.googlevideo.com/videoplayback?id=1",
    "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
])
def test_non_authorized_media_urls_are_refused(url):
    with pytest.raises(MediaIngestionError) as exc:
        media_ingestion.check_authorized_media_url(url)
    assert exc.value.code in ("media_url_rejected", "platform_media_not_authorized")


@pytest.mark.parametrize("url", ["https://127.0.0.1/clip.mp4", "https://169.254.169.254/latest/meta-data"])
def test_private_and_metadata_addresses_are_refused(url):
    with pytest.raises(MediaIngestionError) as exc:
        media_ingestion.check_authorized_media_url(url)
    assert exc.value.code == "media_url_rejected"


def test_authorized_rights_holder_url_is_accepted(public_host):
    assert media_ingestion.check_authorized_media_url("https://media.rights-holder.example/clip.mp4") == \
        "https://media.rights-holder.example/clip.mp4"


# ---------------------------------------------------------------- download and validation

@needs_ffmpeg
def test_authorized_download_is_hashed_and_validated(tmp_path, monkeypatch, public_host):
    video = make_fixture_video(tmp_path / "source.mp4")
    body = open(video, "rb").read()
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(
        200, {"Content-Type": "video/mp4", "Content-Length": str(len(body))}, body))
    dest = tmp_path / "media"
    result = media_ingestion.ingest_authorized_media("https://media.rights-holder.example/clip.mp4", "yt_test", str(dest))
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["bytes"] == len(body)
    assert result["probe"]["width"] == 320 and result["probe"]["height"] == 240
    assert result["probe"]["has_audio"] is True
    assert result["probe"]["duration_seconds"] == pytest.approx(4.0, abs=0.2)
    assert os.path.isfile(result["path"])


@needs_ffmpeg
def test_corrupt_bytes_fail_ffprobe_and_leave_no_file(tmp_path, monkeypatch, public_host):
    body = os.urandom(200_000)
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(
        200, {"Content-Type": "video/mp4", "Content-Length": str(len(body))}, body))
    dest = tmp_path / "media"
    with pytest.raises(MediaIngestionError) as exc:
        media_ingestion.ingest_authorized_media("https://media.rights-holder.example/clip.mp4", "yt_bad", str(dest))
    assert exc.value.code in ("ffprobe_failed", "no_video_stream", "duration_unknown", "invalid_dimensions")
    assert glob.glob(str(dest / "*.video")) == []
    assert glob.glob(str(dest / "*.partial")) == []


def test_non_video_content_type_is_rejected(tmp_path, monkeypatch, public_host):
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(
        200, {"Content-Type": "text/html", "Content-Length": "5000"}, b"<html>" + b"x" * 4993))
    with pytest.raises(MediaIngestionError) as exc:
        media_ingestion.download_authorized_media("https://media.rights-holder.example/page", str(tmp_path), "yt_html")
    assert exc.value.code == "content_type_rejected"


def test_truncated_download_is_rejected(tmp_path, monkeypatch, public_host):
    body = b"\x00" * 100_000
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(
        200, {"Content-Type": "video/mp4", "Content-Length": str(len(body) + 5000)}, body))
    with pytest.raises(MediaIngestionError) as exc:
        media_ingestion.download_authorized_media("https://media.rights-holder.example/clip.mp4", str(tmp_path), "yt_cut")
    assert exc.value.code == "incomplete_download"
    assert glob.glob(str(tmp_path / "*.partial")) == []


def test_declared_size_over_limit_is_refused_before_download(tmp_path, monkeypatch, public_host):
    monkeypatch.setattr(media_ingestion, "MAX_MEDIA_BYTES", 1000)
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(
        200, {"Content-Type": "video/mp4", "Content-Length": "5000"}, b""))
    with pytest.raises(MediaIngestionError) as exc:
        media_ingestion.download_authorized_media("https://media.rights-holder.example/clip.mp4", str(tmp_path), "yt_big")
    assert exc.value.code == "media_too_large"


def test_redirect_to_platform_video_is_refused(tmp_path, monkeypatch, public_host):
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(
        302, {"Location": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}))
    with pytest.raises(MediaIngestionError) as exc:
        media_ingestion.download_authorized_media("https://media.rights-holder.example/clip.mp4", str(tmp_path), "yt_r")
    assert exc.value.code == "platform_media_not_authorized"


def test_http_error_is_reported(tmp_path, monkeypatch, public_host):
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(403, {}, b""))
    with pytest.raises(MediaIngestionError) as exc:
        media_ingestion.download_authorized_media("https://media.rights-holder.example/clip.mp4", str(tmp_path), "yt_403")
    assert exc.value.code == "download_http_error"


@needs_ffmpeg
def test_changed_file_fails_recorded_sha(tmp_path, monkeypatch, public_host):
    video = make_fixture_video(tmp_path / "source.mp4")
    body = open(video, "rb").read()
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(
        200, {"Content-Type": "video/mp4", "Content-Length": str(len(body))}, body))
    with pytest.raises(MediaIngestionError) as exc:
        media_ingestion.ingest_authorized_media("https://media.rights-holder.example/clip.mp4", "yt_sha",
                                                str(tmp_path / "m"), expected_sha256="0" * 64)
    assert exc.value.code == "sha256_mismatch"


# ---------------------------------------------------------------- ingest stage gates

def test_pending_rights_block_download_entirely(monkeypatch, no_network):
    db = FakeDB(dict(PRODUCTION_ITEM, media_url="https://media.rights-holder.example/clip.mp4"))
    monkeypatch.setattr(media_pipeline, "db", db)
    pending = dict(AUTHORIZED_PERMISSION, rights_status=RightsStatus.PENDING_VERIFICATION)
    monkeypatch.setattr(media_pipeline, "rights_manager", FakePermissions(pending))
    result = media_pipeline.ingest_media("yt_dQw4w9WgXcQ")
    assert result["media_status"] == "blocked"
    assert result["media_error"].startswith("rights_gate:rights_not_authorized")


def test_missing_media_url_is_awaiting_not_failed(pipeline_db, no_network):
    result = media_pipeline.ingest_media("yt_dQw4w9WgXcQ")
    assert result["media_status"] == "awaiting_authorized_media"
    assert result["media_error"] == "no_authorized_media_url"


def test_youtube_url_offered_as_media_is_refused_without_download(pipeline_db, no_network):
    result = media_pipeline.ingest_media("yt_dQw4w9WgXcQ", media_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert result["media_status"] == "failed"
    assert result["media_error"].startswith("platform_media_not_authorized")
    assert "media_sha256" not in pipeline_db.all_written_keys()


@needs_ffmpeg
def test_successful_ingest_records_integrity_facts(tmp_path, monkeypatch, pipeline_db, public_host):
    video = make_fixture_video(tmp_path / "source.mp4")
    body = open(video, "rb").read()
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(
        200, {"Content-Type": "video/mp4", "Content-Length": str(len(body))}, body))
    result = media_pipeline.ingest_media("yt_dQw4w9WgXcQ", media_url="https://media.rights-holder.example/clip.mp4",
                                         dest_dir=str(tmp_path / "media"))
    stored = pipeline_db.items["yt_dQw4w9WgXcQ"]
    assert result["media_status"] == "validated"
    assert stored["media_sha256"] == hashlib.sha256(body).hexdigest()
    assert stored["media_access_method"] == "authorized_url"
    assert stored["media_probe"]["width"] == 320
    assert stored["is_processed"] is False


# ---------------------------------------------------------------- moment detection

def test_signal_timeline_combines_motion_and_audio_with_raw_values():
    motion = [{"time": 0.0, "motion": 2.0}, {"time": 0.5, "motion": 8.0}, {"time": 1.0, "motion": 4.0}]
    audio = [{"time": 0.4, "energy": 5.0}, {"time": 1.0, "energy": 10.0}]
    timeline = moment_detection.build_signal_timeline(motion, audio)
    by_time = {s["time"]: s for s in timeline}
    assert by_time[0.5]["motion_raw"] == 8.0 and by_time[0.5]["audio_raw"] == 5.0
    assert by_time[1.0]["combined"] == pytest.approx(0.5 * (4 / 8) + 0.5 * 1.0, abs=1e-3)


def test_signal_timeline_uses_motion_alone_without_audio_and_drops_nan():
    motion = [{"time": 0.0, "motion": 1.0}, {"time": 0.5, "motion": float("nan")}, {"time": 1.0, "motion": 4.0}]
    timeline = moment_detection.build_signal_timeline(motion, [])
    assert [s["time"] for s in timeline] == [0.0, 1.0]
    assert all(s["audio_raw"] is None for s in timeline)


def test_candidate_peaks_respect_separation_and_minimum_signal():
    timeline = [{"time": t, "motion_raw": 1.0, "audio_raw": None, "combined": c}
                for t, c in [(10, 0.95), (15, 0.9), (40, 0.8), (70, 0.5), (90, 0.7)]]
    peaks = moment_detection.find_candidate_peaks(timeline)
    assert [p["time"] for p in peaks] == [10, 40, 90]


def test_default_scorer_rejects_measured_signals_without_semantic_evidence(tmp_path):
    motion = [{"time": t * 0.5, "motion": 1.0 if t != 40 else 9.0} for t in range(0, 120)]
    audio = [{"time": float(t), "energy": 1.0 if t != 20 else 9.5} for t in range(0, 60)]
    result = moment_detection.analyze_footage(
        "unused.video", item_id="yt_x", source_id="src_x", duration=60.0,
        motion_fn=lambda *a, **k: motion, audio_fn=lambda *a, **k: audio,
    )
    assert result["candidates"], "a strong measured peak must produce a candidate window"
    assert result["qualifying"] == []
    top = result["candidates"][0]
    assert top["total_score"] < moment_detection.MIN_PASSING_SCORE
    assert top["rejection_reasons"], "every rejection carries the scorer's reasons"
    assert top["start_time"] <= 20.0 <= top["end_time"]


@pytest.mark.parametrize("score,qualifies", [(75.0, True), (74.99, False), (92.0, True)])
def test_minimum_score_of_75_is_the_only_qualifying_rule(score, qualifies):
    motion = [{"time": t * 0.5, "motion": 1.0 if t != 30 else 9.0} for t in range(0, 120)]
    result = moment_detection.analyze_footage(
        "unused.video", item_id="yt_y", source_id="src_y", duration=60.0,
        score_fn=lambda c: {"total_score": score, "rejection_reasons": []},
        motion_fn=lambda *a, **k: motion, audio_fn=lambda *a, **k: [],
    )
    assert bool(result["qualifying"]) is qualifies


def test_analysis_refuses_footage_longer_than_synchronous_limit():
    with pytest.raises(moment_detection.MomentAnalysisError) as exc:
        moment_detection.analyze_footage("x", item_id="i", source_id="s",
                                         duration=moment_detection.MAX_ANALYSIS_SECONDS + 1)
    assert exc.value.code == "duration_exceeds_synchronous_limit"


@needs_ffmpeg
def test_real_footage_burst_is_detected_at_its_timestamp(tmp_path):
    """Static frames for 20s, then motion and a loud tone at 20s. The detected window must cover 20s."""
    path = str(tmp_path / "burst.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=20",
        "-f", "lavfi", "-i", "testsrc=s=320x240:r=30:d=10",
        "-f", "lavfi", "-i", "aevalsrc='if(between(t,20,22),sin(2*PI*800*t),0)':d=30",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", path,
    ], check=True, capture_output=True)
    result = moment_detection.analyze_footage(path, item_id="yt_burst", source_id="src_burst", duration=30.0)
    assert result["signals"]["motion_samples"] > 0
    assert result["signals"]["audio_available"] is True
    assert result["candidates"], "measured burst must produce a candidate window"
    assert any(c["start_time"] <= 22.0 and c["end_time"] >= 20.0 for c in result["candidates"])
    assert result["qualifying"] == []


# ---------------------------------------------------------------- analysis and render gate stages

def test_render_gate_never_allows_render_without_campaign(pipeline_db):
    item = dict(PRODUCTION_ITEM, media_status="validated", media_sha256="a" * 64)
    gate = media_pipeline.render_gate(item, dict(PRODUCTION_SOURCE))
    assert gate["allowed"] is False
    assert any("campaign" in reason.lower() for reason in gate["reasons"])
    assert gate["rights_status"] == "authorized_platforms"


def test_analysis_blocked_until_media_is_validated(pipeline_db, no_network):
    result = media_pipeline.analyze_item("yt_dQw4w9WgXcQ")
    assert result["analysis_status"] == "blocked"
    assert result["analysis_reason"] == "media_not_validated"


def test_analysis_blocked_when_rights_are_pending(monkeypatch, no_network):
    item = dict(PRODUCTION_ITEM, media_status="validated", media_sha256="b" * 64, media_probe={"duration_seconds": 30})
    monkeypatch.setattr(media_pipeline, "db", FakeDB(item))
    monkeypatch.setattr(media_pipeline, "rights_manager", FakePermissions(
        dict(AUTHORIZED_PERMISSION, rights_status=RightsStatus.EXPIRED)))
    result = media_pipeline.analyze_item("yt_dQw4w9WgXcQ")
    assert result["analysis_status"] == "blocked"
    assert result["analysis_reason"].startswith("rights_gate:")


@needs_ffmpeg
def test_end_to_end_analysis_completes_without_processing_or_rendering(tmp_path, monkeypatch, public_host, caplog):
    video = make_fixture_video(tmp_path / "source.mp4", seconds=6)
    body = open(video, "rb").read()
    sha = hashlib.sha256(body).hexdigest()
    dest = tmp_path / "media"
    os.makedirs(dest)
    local = dest / f"yt_dQw4w9WgXcQ-{sha[:16]}.video"
    shutil.copy(video, local)
    item = dict(PRODUCTION_ITEM, media_status="validated", media_sha256=sha,
                media_probe={"duration_seconds": 6.0, "width": 320, "height": 240},
                media_url="https://media.rights-holder.example/clip.mp4")
    db = FakeDB(item)
    monkeypatch.setattr(media_pipeline, "db", db)
    monkeypatch.setattr(media_pipeline, "rights_manager", FakePermissions(AUTHORIZED_PERMISSION))
    caplog.set_level(logging.INFO, logger="gta6_shorts.discovery.pipeline")

    result = media_pipeline.analyze_item("yt_dQw4w9WgXcQ", dest_dir=str(dest))

    assert result["analysis_status"] == "completed"
    assert result["qualifying_moment_count"] == 0
    assert result["render_gate"]["allowed"] is False
    stored = db.items["yt_dQw4w9WgXcQ"]
    assert stored["analysis_status"] == "completed"
    assert stored["is_processed"] is False
    assert "is_processed" not in db.all_written_keys()
    assert any('"stage": "analysis"' in r.getMessage() for r in caplog.records)


def test_analysis_restores_missing_local_copy_only_with_matching_hash(tmp_path, monkeypatch, public_host):
    item = dict(PRODUCTION_ITEM, media_status="validated", media_sha256="c" * 64,
                media_probe={"duration_seconds": 30}, media_url="https://media.rights-holder.example/clip.mp4")
    db = FakeDB(item)
    monkeypatch.setattr(media_pipeline, "db", db)
    monkeypatch.setattr(media_pipeline, "rights_manager", FakePermissions(AUTHORIZED_PERMISSION))
    other = b"\x01" * 100_000
    monkeypatch.setattr(media_ingestion.requests, "get", lambda *a, **k: FakeResponse(
        200, {"Content-Type": "video/mp4", "Content-Length": str(len(other))}, other))
    result = media_pipeline.analyze_item("yt_dQw4w9WgXcQ", dest_dir=str(tmp_path))
    assert result["analysis_status"] == "failed"
    assert result["analysis_reason"].startswith("media_unavailable:")
    assert db.items["yt_dQw4w9WgXcQ"]["analysis_status"] == "failed"


# ---------------------------------------------------------------- API surface

def test_pipeline_endpoints_require_authentication(client):
    assert client.post("/api/v1/discovery/items/yt_x/resolve-metadata").status_code in (401, 403)
    assert client.post("/api/v1/discovery/items/yt_x/media", json={"media_url": "https://a.example/x.mp4"}).status_code in (401, 403)
    assert client.post("/api/v1/discovery/items/yt_x/analyze").status_code in (401, 403)


def test_unknown_item_returns_404(client, auth_headers):
    response = client.get("/api/v1/discovery/items/yt_does_not_exist", headers=auth_headers)
    assert response.status_code == 404


def test_pipeline_state_round_trips_through_the_database_layer():
    """Real storage layer (SQLite in tests): pipeline columns persist; identity and rights fields are protected."""
    import uuid
    from app.services.supabase_client import db

    item_id = f"yt_round_{uuid.uuid4().hex[:8]}"
    db.save_discovered_content({
        "id": item_id, "source_id": "src_round", "title": "Round trip", "original_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "category_id": "gta6", "rights_status": "authorized_platforms", "duration_seconds": 0.0, "is_processed": False,
    })
    db.update_discovered_content(item_id, {
        "metadata_status": "failed", "metadata_error": "youtube_api_key_missing",
        "media_probe": {"width": 320}, "analysis_candidates": [{"start_time": 1.0}], "qualifying_moment_count": 0,
    })
    stored = db.get_discovered_content(item_id)
    assert stored["metadata_status"] == "failed"
    assert stored["media_probe"] == {"width": 320}
    assert stored["analysis_candidates"] == [{"start_time": 1.0}]
    assert stored["is_processed"] is False
    with pytest.raises(ValueError):
        db.update_discovered_content(item_id, {"is_processed": True})
    with pytest.raises(ValueError):
        db.update_discovered_content(item_id, {"rights_status": "authorized_editing"})

