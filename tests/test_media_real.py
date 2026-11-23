"""Real FFmpeg/FFprobe behaviour on generated media (no mocks)."""
import os

import pytest

from app.errors import MediaValidationError, PipelineError
from app.media import probe as probe_mod
from app.media import render
from app.media.ffmpeg import CancelledError, run
from app.media.render import crop_geometry

from .conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def test_probe_reports_real_dimensions_duration_and_audio(video_clip):
    info = probe_mod.probe(video_clip)
    assert (info.width, info.height) == (1280, 720)
    assert info.duration == pytest.approx(12.0, abs=0.2)
    assert info.has_audio is True
    assert info.video_codec == "h264"


def test_probe_detects_missing_audio(silent_video):
    info = probe_mod.probe(silent_video)
    assert info.has_audio is False


def test_non_media_files_are_rejected(tmp_path):
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"this is definitely not a video file" * 50)
    with pytest.raises(MediaValidationError) as exc:
        probe_mod.probe(str(p))
    assert exc.value.code in {"unreadable_media", "not_media"}


def test_audio_only_files_are_rejected_as_sources(tmp_path):
    import subprocess

    p = tmp_path / "audio.m4a"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=3", "-c:a", "aac", str(p)],
                   check=True)
    with pytest.raises(MediaValidationError) as exc:
        probe_mod.probe(str(p))
    assert exc.value.code == "no_video_stream"


def test_source_limits_enforced(video_clip):
    info = probe_mod.probe(video_clip)
    probe_mod.check_source_limits(info, max_seconds=600, min_seconds=1)
    with pytest.raises(MediaValidationError) as too_long:
        probe_mod.check_source_limits(info, max_seconds=5, min_seconds=1)
    assert too_long.value.code == "duration_exceeds_limit"
    with pytest.raises(MediaValidationError) as too_short:
        probe_mod.check_source_limits(info, max_seconds=600, min_seconds=60)
    assert too_short.value.code == "duration_below_minimum"


def test_full_decode_passes_for_valid_media(video_clip):
    probe_mod.verify_decodable(video_clip)


def test_truncated_media_is_detected(video_clip, tmp_path):
    data = open(video_clip, "rb").read()
    bad = tmp_path / "truncated.mp4"
    bad.write_bytes(data[: len(data) // 3])
    # Either the container is rejected at probe time, or decoding fails. Both must be refused.
    with pytest.raises((MediaValidationError, PipelineError)):
        probe_mod.probe(str(bad))
        probe_mod.verify_decodable(str(bad))


def test_silence_detection_finds_the_silent_lead_in(media_dir):
    from .helpers import make_video

    path = make_video(os.path.join(media_dir, "gated.mp4"), duration=8, audio_gate=True)
    spans = probe_mod.silence_intervals(path, 8.0)
    assert spans and spans[0][0] < 0.5 and 2.5 <= spans[0][1] <= 3.5
    assert probe_mod.silent_fraction(spans, 0.0, 2.0) > 0.9
    assert probe_mod.silent_fraction(spans, 4.0, 8.0) == pytest.approx(0.0, abs=0.05)


# ---------------------------------------------------------------- geometry

@pytest.mark.parametrize("w,h,expect", [
    (1920, 1080, (1080, 1920)),   # 16:9 1080p: centred 9:16 crop, scaled to target
    (1280, 720, (1080, 1920)),
    (1080, 1920, (1080, 1920)),   # already vertical
    (1000, 1000, (1080, 1920)),   # square
])
def test_geometry_targets_1080x1920_for_hd_sources(w, h, expect):
    g = crop_geometry(w, h, 1080, 1920)
    assert (g.out_w, g.out_h) == expect
    assert g.crop_w % 2 == 0 and g.crop_h % 2 == 0
    assert g.crop_w <= w and g.crop_h <= h
    assert abs((g.crop_w / g.crop_h) - 9 / 16) < 0.01  # no distorted aspect ratio


def test_geometry_never_upscales_very_small_sources():
    g = crop_geometry(320, 240, 1080, 1920)
    assert (g.out_w, g.out_h) == (g.crop_w, g.crop_h)
    assert g.out_h <= 240


# ---------------------------------------------------------------- rendering

def test_render_produces_playable_vertical_clip_with_audio(video_clip, tmp_path):
    info = probe_mod.probe(video_clip)
    out = str(tmp_path / "clip.mp4")
    geom = render.render_clip(source=video_clip, start=2.0, end=9.0, out_path=out, src=info, ass_path=None)
    check = render.validate_output(out, expect_w=geom.out_w, expect_h=geom.out_h, expect_duration=7.0,
                                   expect_audio=True)
    assert check["width"] == 1080 and check["height"] == 1920
    assert check["video_codec"] == "h264" and check["audio_codec"] == "aac"


def test_render_burns_in_ass_captions(video_clip, tmp_path):
    from app.captions.formats import to_ass
    from app.captions.timing import Word, build_cues, get_style

    style = get_style("bold_creator")
    cues = build_cues([Word(text="Captions", start=0.2, end=0.6), Word(text="render", start=0.7, end=1.1), Word(text="here.", start=1.2, end=1.6)],
                      style, clip_duration=3.0)
    ass = tmp_path / "captions.ass"
    ass.write_text(to_ass(cues, style, 1080, 1920), encoding="utf-8")
    info = probe_mod.probe(video_clip)
    out = str(tmp_path / "captioned.mp4")
    geom = render.render_clip(source=video_clip, start=0.0, end=3.0, out_path=out, src=info, ass_path=str(ass))
    render.validate_output(out, expect_w=geom.out_w, expect_h=geom.out_h, expect_duration=3.0, expect_audio=True)


def test_special_characters_in_paths_are_escaped(video_clip, tmp_path):
    info = probe_mod.probe(video_clip)
    odd_dir = tmp_path / "odd, dir: 'quote' [1]"
    odd_dir.mkdir()
    out = str(odd_dir / "clip.mp4")
    ass = odd_dir / "captions.ass"
    from app.captions.formats import to_ass
    from app.captions.timing import Word, build_cues, get_style

    style = get_style("clean_minimal")
    ass.write_text(to_ass(build_cues([Word(text="Hi", start=0.1, end=0.5)], style, 2.0), style, 1080, 1920), encoding="utf-8")
    geom = render.render_clip(source=video_clip, start=0.0, end=2.0, out_path=out, src=info, ass_path=str(ass))
    render.validate_output(out, expect_w=geom.out_w, expect_h=geom.out_h, expect_duration=2.0, expect_audio=True)


def test_thumbnail_is_written(video_clip, tmp_path):
    info = probe_mod.probe(video_clip)
    out = str(tmp_path / "c.mp4")
    render.render_clip(source=video_clip, start=0.0, end=4.0, out_path=out, src=info, ass_path=None)
    thumb = str(tmp_path / "t.jpg")
    render.thumbnail(out, thumb, at_seconds=2.0)
    assert os.path.getsize(thumb) > 500


def test_render_can_be_cancelled_mid_encode(video_clip, tmp_path):
    info = probe_mod.probe(video_clip)
    with pytest.raises(CancelledError):
        render.render_clip(source=video_clip, start=0.0, end=10.0, out_path=str(tmp_path / "x.mp4"), src=info,
                           ass_path=None, cancel_check=lambda: True)


def test_ffmpeg_timeout_is_enforced():
    with pytest.raises(PipelineError) as exc:
        run("ffmpeg", ["-hide_banner", "-f", "lavfi", "-i", "testsrc2=duration=600", "-f", "null", "-"],
            timeout=1, label="slow")
    assert exc.value.code == "media_timeout"


def test_ffmpeg_argument_allowlist_blocks_shell_and_unsafe_options():
    with pytest.raises(Exception):
        run("ffmpeg", ["-i", "in.mp4", "-filter_complex", "movie=/etc/passwd"], timeout=5)
