"""Tests for GTA 6 autonomous registry, outro, and Clip Studio foundations."""
import pytest

from app.services.discovery.gta_registry import gta_registry
from app.services.discovery.rights_manager import rights_manager
from app.services.outro_service import OutroConfig, outro_service
from app.services.clip_studio import MultitrackTimeline, clip_studio


def test_gta6_registry_rejects_non_gta_content():
    relevant, reason = gta_registry.is_gta6_relevant("A random racing game clip", "No GTA references")
    assert relevant is False
    assert "only ingests GTA 6" in reason


def test_gta6_registry_accepts_gta6_content():
    relevant, reason = gta_registry.is_gta6_relevant("GTA 6 Lucia reveals Vice City", "Grand Theft Auto VI")
    assert relevant is True
    assert "GTA 6 keyword" in reason


def test_gta6_registry_requires_verified_rights():
    with pytest.raises(PermissionError):
        gta_registry.ingest_moment(
            source_id="src_unverified_test",
            video_url="https://example.com/video.mp4",
            title="GTA 6 unverified gameplay",
            start_time=0.0,
            end_time=5.0,
            score=0.0,
        )


def test_gta6_registry_ingests_eligible_moment():
    rights_manager.set_permission(
        source_id="src_test_creator",
        status=__import__("app.services.discovery.base", fromlist=["RightsStatus"]).RightsStatus.AUTHORIZED_EDITING,
        allowed_platforms=["youtube_shorts"],
        verified_by="test-admin",
    )
    moment = gta_registry.ingest_moment(
        source_id="src_test_creator",
        video_url="https://www.youtube.com/watch?v=QdBZY2fkU-0",
        title="GTA 6 Official Trailer 1 - Vice City",
        start_time=2.0,
        end_time=8.0,
        score=9.3,
        hook_text="GTA 6 VICE CITY REVEAL",
        event_type="reveal",
    )
    assert moment["source_id"] == "src_test_creator"
    assert moment["duration"] == 6.0
    assert moment["score"] == 9.3


def test_outro_config_validation():
    config = OutroConfig(enabled=True, duration_seconds=4.0, promo_text="JOIN GTA 6 COMMUNITY")
    assert config.enabled is True
    assert config.duration_seconds == 4.0
    with pytest.raises(Exception):
        OutroConfig(duration_seconds=20.0)


def test_studio_timeline_has_six_tracks():
    timeline = MultitrackTimeline(clip_id="test_clip")
    assert timeline.video_track is not None
    assert timeline.audio_track is not None
    assert timeline.captions_track is not None
    assert timeline.hooks_track is not None
    assert timeline.reframing_track is not None
    assert timeline.outro_track is not None
