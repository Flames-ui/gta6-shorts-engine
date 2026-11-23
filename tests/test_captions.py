import pytest

from app.captions import formats
from app.captions.timing import Word, build_cues, get_style, words_in_window
from app.captions.timing import PRESETS

from .helpers import speech_timeline


def _words(n=24, start=10.0, step=0.3):
    return [Word(text=f"w{i}.", start=round(start + i * step, 3), end=round(start + i * step + 0.25, 3))
            for i in range(n)]


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_produces_valid_srt_vtt_and_ass(preset):
    style = get_style(preset)
    cues = build_cues(_words(), style, clip_duration=30.0)
    assert cues
    assert formats.validate_sync(cues, 30.0, _words(), {
        "srt": formats.to_srt(cues), "vtt": formats.to_vtt(cues),
        "ass": formats.to_ass(cues, style, 1080, 1920)})["ok"]


def test_cues_are_inside_the_clip_and_never_negative():
    cues = build_cues(_words(40, start=0.1), get_style("bold_creator"), clip_duration=20.0)
    for c in cues:
        assert c.start >= 0
        assert c.end <= 20.0 + 1e-6
        assert c.end > c.start


def test_cues_are_ordered_and_do_not_overlap():
    cues = build_cues(_words(60), get_style("clean_minimal"), clip_duration=40.0)
    for a, b in zip(cues, cues[1:]):
        assert a.end <= b.start + 1e-6


def test_long_pauses_split_cues():
    words = _words(6) + [Word(text="late.", start=20.0, end=20.25)]
    cues = build_cues(words, get_style("clean_minimal"), clip_duration=25.0)
    assert len(cues) >= 2


def test_srt_round_trip_preserves_timing_and_text():
    cues = build_cues(_words(12), get_style("clean_minimal"), clip_duration=10.0)
    text = formats.to_srt(cues)
    parsed = formats.parse_srt(text)
    assert len(parsed) == len(cues)
    assert all(abs(p.start - c.start) < 0.002 and abs(p.end - c.end) < 0.002 for p, c in zip(parsed, cues))


def test_vtt_header_and_ass_escaping():
    style = get_style("bold_creator")
    cues = build_cues([Word(text="{brace}", start=0.0, end=0.3), Word(text="line\\two", start=0.3, end=0.6)], style, clip_duration=2.0)
    assert formats.to_vtt(cues).startswith("WEBVTT")
    ass = formats.to_ass(cues, style, 1080, 1920)
    assert "{brace}" not in ass.split("[Events]")[1].replace("{\\", "")  # braces escaped in dialogue text
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


def test_validate_sync_flags_negative_and_out_of_clip_timestamps():
    from app.captions.timing import Cue

    bad = [Cue(start=-0.5, end=1.0, lines=[[Word(text="x", start=-0.5, end=1.0)]])]
    report = formats.validate_sync(bad, 5.0, [], {"srt": "", "vtt": "WEBVTT", "ass": ""})
    assert report["ok"] is False
    assert any("negative" in i or "start" in i for i in report["issues"])


def test_validate_sync_flags_malformed_files():
    cues = build_cues(_words(12), get_style("clean_minimal"), clip_duration=10.0)
    report = formats.validate_sync(cues, 10.0, _words(12), {"srt": "not an srt", "vtt": "nope",
                                                            "ass": "[Events]\nbroken"})
    assert report["ok"] is False


def test_words_in_window_uses_exact_word_timing_when_present():
    timeline = speech_timeline([5.0])
    words, exact = words_in_window(timeline["segments"], 5.0, 25.0)
    assert exact is True
    assert words and words[0].start == 0.0          # clip-relative time
    assert all(0.0 <= w.start < w.end <= 20.0 + 1e-6 for w in words)


def test_words_in_window_falls_back_to_estimates_without_word_timing():
    segs = [{"start": 0.0, "end": 4.0, "text": "one two three four"}]
    words, exact = words_in_window(segs, 0.0, 4.0)
    assert exact is False
    assert len(words) == 4


def test_unknown_preset_is_rejected():
    with pytest.raises(Exception):
        get_style("comic_sans_party")
