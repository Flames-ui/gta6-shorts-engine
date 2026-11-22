"""Tests for intelligent gameplay analysis, interesting-moment detection, and candidate ranking."""
import os
import subprocess
import pytest

from app.services.gameplay_analyzer import (
    gameplay_analyzer,
    GameplayCandidate,
    GameplayKeyframe,
)


@pytest.fixture(scope="module")
def sample_gameplay_video(tmp_path_factory):
    """Generate a real 10-second test video with audio tone for gameplay analysis tests."""
    target_dir = tmp_path_factory.mktemp("gameplay_data")
    vpath = os.path.join(str(target_dir), "sample_gta6_gameplay.mp4")

    # Generate 1920x1080 30fps test video with audio
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=10",
        "-f", "lavfi", "-i", "sine=f=440:d=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        vpath,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return vpath


def test_probe_video_metadata(sample_gameplay_video):
    meta = gameplay_analyzer.probe_video_metadata(sample_gameplay_video)
    assert meta["duration"] >= 9.9
    assert meta["width"] == 1920
    assert meta["height"] == 1080
    assert meta["fps"] == 30.0
    assert meta["has_audio"] is True


def test_visual_motion_peaks(sample_gameplay_video):
    peaks = gameplay_analyzer.detect_visual_motion_peaks(sample_gameplay_video, sample_interval=0.5)
    assert isinstance(peaks, list)
    assert len(peaks) > 0
    assert "time" in peaks[0]
    assert "motion" in peaks[0]


def test_audio_energy_peaks(sample_gameplay_video):
    peaks = gameplay_analyzer.detect_audio_energy_peaks(sample_gameplay_video)
    assert isinstance(peaks, list)


def test_analyze_gameplay_footage(sample_gameplay_video):
    transcripts = [
        {"start": 1.0, "end": 4.0, "text": "Watch this police chase in Vice City!"},
        {"start": 5.0, "end": 8.0, "text": "Lucia broke the GTA 6 physics engine with this stunt jump!"},
    ]
    candidates = gameplay_analyzer.analyze_gameplay_footage(
        video_path=sample_gameplay_video,
        source_id="src_test_gta6",
        video_title="GTA 6 Lucia Police Chase",
        transcript_segments=transcripts,
        min_duration=3.0,
        max_duration=6.0,
        max_candidates=3,
    )

    assert len(candidates) > 0
    c = candidates[0]
    assert isinstance(c, GameplayCandidate)
    assert c.duration >= 2.9
    assert c.composite_score >= 5.0
    assert len(c.suggested_hook) > 0
    assert len(c.keyframes) >= 2
    assert isinstance(c.keyframes[0], GameplayKeyframe)
    # Check ranking order (descending score)
    scores = [x.composite_score for x in candidates]
    assert scores == sorted(scores, reverse=True)
