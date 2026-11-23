"""AI provider contract: validated responses, retries, invalid output and provider failures."""
import json

import pytest

from app.ai import analysis
from app.ai.fake import ScriptedProvider
from app.ai.provider import ProviderError
from app.errors import PipelineError

from .helpers import speech_timeline

MOMENTS = [6.0, 36.0, 66.0, 96.0, 126.0]


def _item(start, end, **over):
    base = {"start_seconds": start, "end_seconds": end, "title": "A clear insight", "description": "d",
            "rationale": "self-contained explanation", "engagement_score": 84, "confidence": 0.9,
            "category": "education", "standalone": True}
    base.update(over)
    return base


def test_valid_response_is_parsed():
    text = json.dumps({"highlights": [_item(6, 26)]})
    cands, rejected = analysis.parse_candidates(text, (0, 150), 15, 90)
    assert len(cands) == 1 and not rejected
    assert cands[0].engagement_score == 84


def test_fenced_json_is_accepted():
    text = "```json\n" + json.dumps({"highlights": [_item(6, 26)]}) + "\n```"
    cands, _ = analysis.parse_candidates(text, (0, 150), 15, 90)
    assert len(cands) == 1


@pytest.mark.parametrize("item,reason", [
    (_item(6, 26, engagement_score=150), "out of range"),
    (_item(6, 26, confidence=1.5), "out of range"),
    (_item(6, 26, standalone="yes"), "standalone"),
    (_item(6, 26, title="   "), "empty title"),
    (_item(6, 9), "duration"),
    (_item(6, 500), "duration"),
    (_item(-30, 0), "outside analysed window"),
    ({"title": "no timing"}, "missing"),
    ("not an object", "not an object"),
    (_item("abc", 20), "non-numeric"),
])
def test_invalid_items_are_rejected_without_crashing(item, reason):
    text = json.dumps({"highlights": [item, _item(40, 60)]})
    cands, rejected = analysis.parse_candidates(text, (0, 150), 15, 90)
    assert len(cands) == 1  # the valid sibling survives
    assert any(reason in r for r in rejected), rejected


def test_non_json_raises_value_error():
    with pytest.raises(ValueError):
        analysis.parse_candidates("I think the best part is at the start", (0, 150), 15, 90)


def test_missing_highlights_list_raises():
    with pytest.raises(ValueError):
        analysis.parse_candidates(json.dumps({"answer": 1}), (0, 150), 15, 90)


def test_chunk_windows_overlap_for_long_sources():
    assert analysis.chunk_windows(600) == [(0.0, 600.0)]
    windows = analysis.chunk_windows(2000, chunk=900, overlap=60)
    assert windows[0] == (0.0, 900.0)
    assert windows[1][0] < windows[0][1]  # overlap
    assert windows[-1][1] == 2000.0


def _segments():
    return speech_timeline(MOMENTS)["segments"]


def test_transient_rate_limit_is_retried_then_succeeds():
    sleeps = []
    provider = ScriptedProvider([
        ProviderError("rate_limited", "slow down", retryable=True),
        {"highlights": [_item(6, 26)]},
    ])
    out = analysis.analyze(provider=provider, segments=_segments(), duration=150, title="t", language="en",
                           min_seconds=15, max_seconds=90, wanted=8, max_retries=3, sleep=sleeps.append)
    assert len(out.candidates) == 1
    assert sleeps and sleeps[0] > 0
    assert len(provider.calls) == 2


def test_persistent_provider_failure_fails_the_job_cleanly():
    provider = ScriptedProvider([ProviderError("timeout", "timed out", retryable=True)] * 5)
    with pytest.raises(PipelineError) as exc:
        analysis.analyze(provider=provider, segments=_segments(), duration=150, title="t", language="en",
                         min_seconds=15, max_seconds=90, wanted=8, max_retries=2, sleep=lambda s: None)
    assert exc.value.code == "ai_timeout"


def test_non_retryable_provider_error_is_not_retried():
    provider = ScriptedProvider([ProviderError("auth", "bad key", retryable=False)])
    with pytest.raises(PipelineError):
        analysis.analyze(provider=provider, segments=_segments(), duration=150, title="t", language="en",
                         min_seconds=15, max_seconds=90, wanted=8, max_retries=3, sleep=lambda s: None)
    assert len(provider.calls) == 1


def test_malformed_responses_are_retried_then_reported():
    provider = ScriptedProvider(["oops not json", "still not json", "{broken"])
    with pytest.raises(PipelineError) as exc:
        analysis.analyze(provider=provider, segments=_segments(), duration=150, title="t", language="en",
                         min_seconds=15, max_seconds=90, wanted=8, max_retries=2, sleep=lambda s: None)
    assert exc.value.code == "ai_invalid_response"


def test_token_usage_is_accumulated():
    provider = ScriptedProvider([{"highlights": [_item(6, 26)]}])
    out = analysis.analyze(provider=provider, segments=_segments(), duration=150, title="t", language="en",
                           min_seconds=15, max_seconds=90, wanted=8, max_retries=0, sleep=lambda s: None)
    assert out.usage.input_tokens > 0 and out.usage.output_tokens > 0


def test_prompt_contains_transcript_but_no_credentials():
    provider = ScriptedProvider([{"highlights": []}])
    analysis.analyze(provider=provider, segments=_segments(), duration=150, title="Podcast", language="en",
                     min_seconds=15, max_seconds=90, wanted=8, max_retries=0, sleep=lambda s: None)
    prompt = provider.calls[0]
    assert "transcript" in prompt.lower()
    assert "AIza" not in prompt


def test_merge_overlapping_keeps_the_stronger_candidate():
    weak = analysis.Candidate(6, 30, "weak", "", "", 70, 0.7, "x", True)
    strong = analysis.Candidate(8, 32, "strong", "", "", 90, 0.9, "x", True)
    merged = analysis.merge_overlapping([weak, strong])
    assert [c.title for c in merged] == ["strong"]
