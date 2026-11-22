import uuid
"""Tests for 6-track Clip Studio timeline rendering, ASS subtitles, loudnorm, and stickman outro integration."""
import os
import subprocess
import pytest

from app.services.clip_studio import clip_studio, CaptionItem, TimelineKeyframe
from app.services.supabase_client import db


@pytest.fixture(scope="module")
def sample_video_with_audio(tmp_path_factory):
    """Generate a real 5-second 1080p MP4 file with audio tone for rendering tests."""
    target_dir = tmp_path_factory.mktemp("render_fixtures")
    vpath = os.path.join(str(target_dir), "source_footage.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=5",
        "-f", "lavfi", "-i", "sine=f=440:d=5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        vpath,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return vpath


def test_timeline_structure_and_six_tracks():
    timeline = clip_studio.get_or_create_timeline("clip_test_tracks")
    assert timeline.clip_id == "clip_test_tracks"
    # Ensure all 6 distinct tracks exist
    assert "start_time" in timeline.video_track
    assert "normalize_loudness" in timeline.audio_track
    assert isinstance(timeline.captions_track, list)
    assert "text" in timeline.hooks_track
    assert isinstance(timeline.reframing_track, list)
    assert "enabled" in timeline.outro_track
    assert timeline.duration <= 60.0


def test_ass_captions_generation(tmp_path):
    captions = [
        CaptionItem(start=0.5, end=2.0, text="GTA 6 Vice City graphics are unbelievable"),
        CaptionItem(start=2.1, end=4.0, text="Next-gen lighting on water surface"),
    ]
    ass_path = os.path.join(str(tmp_path), "captions.ass")
    clip_studio._generate_ass_subtitles(captions, ass_path)

    assert os.path.exists(ass_path)
    with open(ass_path) as f:
        content = f.read()

    assert "[Script Info]" in content
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "GTA 6 Vice City" in content


def test_full_timeline_render_with_ffmpeg(sample_video_with_audio, tmp_path):
    clip_id = f"clip_studio_e2e_test_{uuid.uuid4().hex[:8]}"
    db.create_clip({
        "id": clip_id,
        "title": "GTA 6 Stunt Chase",
        "file_path": sample_video_with_audio,
        "user_id": "test_render_user",
        "category": "gaming",
        "duration": 5.0,
    })

    # Configure timeline with hooks, captions, reframing, and outro
    timeline_data = {
        "duration": 5.0,
        "title": "GTA 6 Stunt Chase",
        "video_track": {
            "start_time": 0.0,
            "end_time": 3.0,
            "speed": 1.0,
            "volume": 1.0,
        },
        "audio_track": {
            "ducking": False,
            "normalize_loudness": True,
        },
        "hooks_track": {
            "text": "LUCIA BREAKS VICE CITY",
            "start": 0.0,
            "end": 2.0,
            "preset": "top_banner",
            "color": "#F43F5E",
        },
        "captions_track": [
            {"start": 0.2, "end": 2.5, "text": "Unreal police chase physics in GTA 6"},
        ],
        "reframing_track": [
            {"time_offset": 0.0, "crop_x_percent": 50.0, "crop_y_percent": 50.0, "zoom": 1.0},
            {"time_offset": 2.5, "crop_x_percent": 52.0, "crop_y_percent": 50.0, "zoom": 1.05},
        ],
        "outro_track": {
            "enabled": True,
            "duration": 2.0,
            "promo_text": "Follow for daily GTA 6 leaks",
            "destination_url": "https://gta6.example.com",
        },
    }
    clip_studio.save_timeline(clip_id, timeline_data)

    target_out = os.path.join(str(tmp_path), "final_rendered_short.mp4")
    result = clip_studio.render_timeline(clip_id, output_path=target_out)

    assert result["success"] is True
    assert os.path.exists(target_out)
    assert os.path.getsize(target_out) > 5000

    # Verify rendered video properties with ffprobe
    import json
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name",
        "-of", "json",
        target_out,
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    vinfo = json.loads(res.stdout)["streams"][0]
    assert vinfo["width"] == 1080
    assert vinfo["height"] == 1920
    assert vinfo["codec_name"] == "h264"
