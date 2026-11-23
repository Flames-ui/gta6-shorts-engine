"""Highlight selection rules: quality, boundaries, duplicates and the five-clip guarantee."""
import pytest

from app.ai.analysis import Candidate
from app.errors import PipelineError
from app.pipeline import selection

from .helpers import speech_timeline

MOMENTS = [6.0, 36.0, 66.0, 96.0, 126.0]


def _cand(start, end, score=85, conf=0.9, standalone=True, title=None, text_hint="", cat="education"):
    return Candidate(start=start, end=end, title=title or f"Moment {start}", description="d", rationale="r",
                     engagement_score=score, confidence=conf, category=cat, standalone=standalone,
                     source_window=(0.0, 150.0))


def _timeline():
    return speech_timeline(MOMENTS)


def _evaluate(candidates, timeline=None, silence=None):
    t = timeline or _timeline()
    words = [w for s in t["segments"] for w in s["words"]]
    return selection.evaluate(candidates, segments=t["segments"], words=words, silence=silence or [],
                              duration=t["duration"], min_seconds=15, max_seconds=90, min_score=75)


def test_good_moments_are_accepted_and_snapped_to_sentence_boundaries():
    m = speech_timeline(MOMENTS)["moments"]
    decisions = _evaluate([_cand(m[0]["start"] + 0.2, m[0]["end"] - 0.1)])
    assert decisions[0].accepted, decisions[0].reason
    assert decisions[0].start == pytest.approx(m[0]["start"], abs=0.01)


@pytest.mark.parametrize("kwargs,reason", [
    ({"score": 74}, "below_quality_threshold"),
    ({"conf": 0.5}, "low_confidence"),
    ({"standalone": False}, "not_standalone"),
])
def test_quality_gates_reject_weak_candidates(kwargs, reason):
    m = speech_timeline(MOMENTS)["moments"]
    d = _evaluate([_cand(m[1]["start"], m[1]["end"], **kwargs)])[0]
    assert not d.accepted
    assert d.reason == reason


def test_too_short_and_too_long_are_rejected():
    short = _evaluate([_cand(6.0, 9.0)])[0]
    assert not short.accepted and short.reason in {"too_short", "too_few_words"}
    long = _evaluate([_cand(6.0, 200.0)])[0]
    assert not long.accepted


def test_introductions_are_rejected():
    t = speech_timeline(MOMENTS)
    intro = "Hey everyone, welcome back to the channel."
    first = t["segments"][0]
    intro_words = [{"word": w, "start": first["start"] + i * 0.28, "end": first["start"] + (i + 1) * 0.28}
                   for i, w in enumerate(intro.split())]
    first["words"] = intro_words + first["words"]
    first["text"] = intro + " " + first["text"]
    t["moments"][0]["start"] = first["start"]
    m = t["moments"][0]
    d = _evaluate([_cand(m["start"], m["end"])], timeline=t)[0]
    assert not d.accepted
    assert d.reason == "introductory_filler"


def test_mostly_silent_windows_are_rejected():
    m = speech_timeline(MOMENTS)["moments"][0]
    silence = [(m["start"], m["end"])]
    d = _evaluate([_cand(m["start"], m["end"])], silence=silence)[0]
    assert not d.accepted
    assert d.reason == "mostly_silent"


def test_overlapping_and_duplicate_moments_are_not_both_chosen():
    m = speech_timeline(MOMENTS)["moments"]
    decisions = _evaluate([_cand(m[0]["start"], m[0]["end"], score=90),
                           _cand(m[0]["start"] + 2, m[0]["end"] + 1, score=80),
                           _cand(m[1]["start"], m[1]["end"], score=85)])
    chosen, rest = selection.choose(decisions, 5)
    starts = [d.start for d in chosen]
    assert len(starts) == len(set(starts))
    assert len(chosen) == 2
    assert any(d.reason in {"overlaps_selected_clip", "repeats_selected_moment"} for d in rest)


def test_choose_prefers_higher_engagement():
    m = speech_timeline(MOMENTS)["moments"]
    decisions = _evaluate([_cand(x["start"], x["end"], score=s) for x, s in zip(m, [78, 95, 81, 90, 76])])
    chosen, _ = selection.choose(decisions, 5)
    assert [round(d.candidate.engagement_score) for d in chosen] == [95, 90, 81, 78, 76]


def test_ensure_five_fails_with_clear_reason_when_not_enough_moments():
    m = speech_timeline(MOMENTS)["moments"]
    decisions = _evaluate([_cand(x["start"], x["end"]) for x in m[:3]])
    chosen, _ = selection.choose(decisions, 5)
    with pytest.raises(PipelineError) as exc:
        selection.ensure_five(chosen, decisions, 5)
    assert exc.value.code == "insufficient_highlights"
    assert "3 of 5" in exc.value.message


def test_ensure_five_passes_with_five_distinct_moments():
    m = speech_timeline(MOMENTS)["moments"]
    decisions = _evaluate([_cand(x["start"], x["end"]) for x in m])
    chosen, _ = selection.choose(decisions, 5)
    selection.ensure_five(chosen, decisions, 5)
    assert len(chosen) == 5
