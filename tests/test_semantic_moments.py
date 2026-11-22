"""Semantic evidence for candidate windows: parsing, scorer units, fail-closed paths and provider cleanup."""
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.discovery import media_pipeline, moment_detection, semantic_moments
from app.services.discovery.semantic_moments import (
    SEMANTIC_FACTORS,
    GeminiWindowEvaluator,
    SemanticAnalysisError,
    default_semantic_fn,
    extract_window,
    parse_model_output,
    scorer_evidence,
)
from app.services.quality_candidate_scorer import QualityCandidateScorer


def model_payload(score=10, **overrides):
    factors = {
        name: {"score": score, "justification": f"visible {name}", "observed_at_s": 3.0}
        for name in SEMANTIC_FACTORS
    }
    factors.update(overrides)
    return {"factors": factors, "suggested_hook": "Watch this", "suggested_caption": "Huge moment"}


def model_text(payload=None, **kwargs):
    return json.dumps(payload if payload is not None else model_payload(**kwargs))


def evidence_for(payload, start=40.0, end=70.0, cand_id="cand_test"):
    parsed = parse_model_output(json.dumps(payload), end - start)
    return scorer_evidence(parsed, model="gemini-test", cand_id=cand_id, source_id="yt_src", window_start=start)


def score_candidate(evidence, start=40.0, end=70.0):
    return QualityCandidateScorer().score_candidate({
        "id": "cand_test", "source_id": "yt_src", "source_video_id": "yt_src",
        "start_time": start, "end_time": end, "duration": end - start,
        "source_duration": 600.0, "analysis_evidence": evidence,
    }, persist_evaluation=False)


# ---------------------------------------------------------------- parsing and scorer units

def test_parse_accepts_complete_output_and_keeps_text_suggestions():
    parsed = parse_model_output(model_text(), 30.0)
    assert set(parsed["factors"]) == set(SEMANTIC_FACTORS)
    assert parsed["suggested_hook"] == "Watch this"


@pytest.mark.parametrize("text", ["not json", "[]", json.dumps({"factors": ["hook"]}), ""])
def test_unreadable_model_output_fails_closed(text):
    with pytest.raises(SemanticAnalysisError) as exc:
        parse_model_output(text, 30.0)
    assert exc.value.code == "semantic_output_invalid"


def test_scores_are_clamped_and_timestamps_kept_inside_the_window():
    parsed = parse_model_output(model_text(hook_strength={"score": 14, "justification": "x", "observed_at_s": 99}), 30.0)
    assert parsed["factors"]["hook_strength"]["score"] == 10.0
    assert parsed["factors"]["hook_strength"]["observed_at_s"] == 30.0


def test_factor_without_justification_or_number_is_dropped_not_defaulted():
    parsed = parse_model_output(model_text(
        story_clarity={"score": 9, "justification": "", "observed_at_s": 2},
        reaction_payoff={"score": "high", "justification": "yes", "observed_at_s": 2},
    ), 30.0)
    assert "story_clarity" not in parsed["factors"]
    assert "reaction_payoff" not in parsed["factors"]


def test_full_marks_convert_to_the_scorer_units_and_reach_100():
    scored = score_candidate(evidence_for(model_payload(10)))
    assert scored["factor_breakdown"] == {
        "hook_strength": 20.0, "entertainment_value": 20.0, "story_clarity": 15.0,
        "reaction_payoff": 15.0, "visual_audio_quality": 10.0, "caption_potential": 10.0,
        "replay_potential": 5.0, "campaign_fit": 0.0,
    }
    assert scored["total_score"] == 95.0  # campaign_fit is 0 without a campaign, so full semantic marks give 95


def test_low_score_of_one_is_not_read_as_full_marks():
    # A raw 1 on a 0-10 scale must mean 10 percent, not the scorer's "1.0 means everything" reading.
    scored = score_candidate(evidence_for(model_payload(1)))
    assert scored["factor_breakdown"]["hook_strength"] == 2.0
    assert scored["factor_breakdown"]["visual_audio_quality"] == 1.0


def test_evidence_timestamps_are_never_empty_even_at_the_window_start():
    evidence = evidence_for(model_payload(10, hook_strength={"score": 8, "justification": "hit", "observed_at_s": 0.0}),
                            start=0.0, end=30.0)
    assert evidence["hook_strength"]["observed_at"] == "0.00s"
    assert evidence["hook_strength"]["evidence_reference"].startswith("gemini:gemini-test:cand_test:")


def test_missing_semantic_factor_is_rejected_with_the_scorer_reason():
    payload = model_payload(10)
    del payload["factors"]["story_clarity"]
    del payload["factors"]["reaction_payoff"]
    scored = score_candidate(evidence_for(payload))
    assert scored["total_score"] < QualityCandidateScorer.MIN_PASSING_SCORE
    assert "Missing story clarity evidence" in scored["rejection_reasons"]
    assert "Missing reaction/payoff evidence" in scored["rejection_reasons"]


# ---------------------------------------------------------------- moment detection with semantic evidence

def _peaked_signals():
    motion = [{"time": t * 0.5, "motion": 1.0 if t != 40 else 9.0} for t in range(0, 120)]
    audio = [{"time": float(t), "energy": 1.0 if t != 20 else 9.5} for t in range(0, 60)]
    return motion, audio


def _semantic_returning(payload):
    calls = []

    def fake(path, start, end, cand_id, source_id):
        calls.append((path, start, end, cand_id, source_id))
        evidence = scorer_evidence(parse_model_output(json.dumps(payload), end - start), model="test-model",
                                   cand_id=cand_id, source_id=source_id, window_start=start)
        return {"analysis_evidence": evidence, "suggested_hook": "hook", "suggested_caption": "cap", "model": "test-model"}

    return fake, calls


def test_strong_semantic_evidence_qualifies_a_window_at_or_above_75():
    motion, audio = _peaked_signals()
    fake, calls = _semantic_returning(model_payload(10))
    result = moment_detection.analyze_footage(
        "footage.video", item_id="yt_x", source_id="yt_src", duration=60.0,
        motion_fn=lambda *a, **k: motion, audio_fn=lambda *a, **k: audio, semantic_fn=fake,
    )
    assert result["qualifying"], "full semantic evidence on the measured peak must qualify"
    top = result["qualifying"][0]
    assert top["total_score"] >= moment_detection.MIN_PASSING_SCORE
    assert top["evidence"]["hook_strength"]["justification"] == "visible hook_strength"
    assert top["semantic"]["model"] == "test-model"
    assert calls and calls[0][0] == "footage.video" and calls[0][4] == "yt_src"
    assert result["signals"]["semantic_windows_evaluated"] == len(calls)


def test_weak_semantic_evidence_is_not_qualifying():
    motion, audio = _peaked_signals()
    fake, _ = _semantic_returning(model_payload(2))
    result = moment_detection.analyze_footage(
        "footage.video", item_id="yt_x", source_id="yt_src", duration=60.0,
        motion_fn=lambda *a, **k: motion, audio_fn=lambda *a, **k: audio, semantic_fn=fake,
    )
    assert result["qualifying"] == []


def test_semantic_provider_error_fails_the_analysis_with_its_code():
    motion, audio = _peaked_signals()

    def broken(*args):
        raise SemanticAnalysisError("semantic_provider_error", "RuntimeError")

    with pytest.raises(moment_detection.MomentAnalysisError) as exc:
        moment_detection.analyze_footage(
            "footage.video", item_id="yt_x", source_id="yt_src", duration=60.0,
            motion_fn=lambda *a, **k: motion, audio_fn=lambda *a, **k: audio, semantic_fn=broken,
        )
    assert exc.value.code == "semantic_provider_error"


# ---------------------------------------------------------------- Gemini evaluator with a fake client

class FakeFiles:
    def __init__(self, state="ACTIVE"):
        self.state = state
        self.uploads = []
        self.deleted = []

    def upload(self, file, config):
        self.uploads.append({"file": file, "config": config, "existed": os.path.isfile(file)})
        return SimpleNamespace(name="files/abc", state=SimpleNamespace(name=self.state))

    def get(self, name):
        return SimpleNamespace(name=name, state=SimpleNamespace(name=self.state))

    def delete(self, name):
        self.deleted.append(name)


class FakeModels:
    def __init__(self, text=None, error=None):
        self.text = text
        self.error = error
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


class FakeClient:
    def __init__(self, text=None, error=None, state="ACTIVE"):
        self.files = FakeFiles(state)
        self.models = FakeModels(text, error)


def make_source_video(path, seconds=6):
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True, capture_output=True)
    return str(path)


def test_window_extraction_cuts_the_requested_length(tmp_path):
    source = make_source_video(tmp_path / "source.mp4", seconds=6)
    dest = tmp_path / "window.mp4"
    extract_window(source, 1.0, 2.0, str(dest))
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(dest)], capture_output=True, text=True, check=True)
    assert abs(float(probe.stdout.strip()) - 2.0) < 0.3


def test_gemini_evaluator_uploads_json_mode_request_and_deletes_the_upload(tmp_path):
    source = make_source_video(tmp_path / "source.mp4")
    client = FakeClient(text=model_text(model_payload(9)))
    evaluator = GeminiWindowEvaluator(model="gemini-test", client=client)

    result = evaluator(source, 1.0, 4.0, "cand_x", "yt_src")

    upload = client.files.uploads[0]
    assert upload["existed"] is True
    assert upload["config"] == {"mime_type": "video/mp4"}
    call = client.models.calls[0]
    assert call["model"] == "gemini-test"
    assert call["config"]["response_mime_type"] == "application/json"
    assert call["config"]["temperature"] == 0
    assert client.files.deleted == ["files/abc"]
    assert result["analysis_evidence"]["hook_strength"]["score"] == 0.9
    assert result["analysis_evidence"]["hook_strength"]["source_id"] == "yt_src"
    assert result["suggested_caption"] == "Huge moment"
    assert not os.path.exists(upload["file"]), "the local window clip is removed after analysis"


def test_provider_failure_is_reported_and_the_upload_is_still_deleted(tmp_path):
    source = make_source_video(tmp_path / "source.mp4")
    client = FakeClient(error=RuntimeError("quota exceeded for project"))
    evaluator = GeminiWindowEvaluator(model="gemini-test", client=client)

    with pytest.raises(SemanticAnalysisError) as exc:
        evaluator(source, 1.0, 4.0, "cand_x", "yt_src")
    assert exc.value.code == "semantic_provider_error"
    assert "quota" not in str(exc.value), "provider payloads are not copied into the error"
    assert client.files.deleted == ["files/abc"]


def test_file_that_never_becomes_active_fails_closed(tmp_path):
    source = make_source_video(tmp_path / "source.mp4")
    client = FakeClient(text=model_text(), state="FAILED")
    with pytest.raises(SemanticAnalysisError) as exc:
        GeminiWindowEvaluator(model="gemini-test", client=client)(source, 1.0, 4.0, "cand_x", "yt_src")
    assert exc.value.code == "semantic_upload_failed"
    assert client.models.calls == [], "the model is never asked about a file that failed to process"
    assert client.files.deleted == ["files/abc"]


def test_windows_longer_than_the_limit_are_refused(tmp_path):
    source = make_source_video(tmp_path / "source.mp4")
    evaluator = GeminiWindowEvaluator(model="gemini-test", client=FakeClient(text=model_text()))
    with pytest.raises(SemanticAnalysisError) as exc:
        evaluator(source, 0.0, semantic_moments.WINDOW_MAX_SECONDS + 1, "cand_x", "yt_src")
    assert exc.value.code == "window_length_invalid"


# ---------------------------------------------------------------- configuration and the pipeline gate

def test_no_api_key_means_no_semantic_provider(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    assert default_semantic_fn() is None


def test_api_key_selects_the_gemini_evaluator(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key-not-real")
    assert isinstance(default_semantic_fn(), GeminiWindowEvaluator)


def test_pipeline_blocks_analysis_without_a_semantic_provider(monkeypatch):
    item = {"id": "yt_x", "source_id": "src_x", "media_status": "validated", "media_sha256": "ab" * 32,
            "media_probe": {"duration_seconds": 60.0}}
    updates = []
    monkeypatch.setattr(media_pipeline, "default_semantic_fn", lambda: None)
    monkeypatch.setattr(media_pipeline, "_load", lambda item_id: dict(item))
    monkeypatch.setattr(media_pipeline, "_load_source", lambda it: {"id": "src_x"})
    monkeypatch.setattr(media_pipeline, "_rights_decision", lambda source, now=None: (None, None))
    monkeypatch.setattr(media_pipeline, "_update_analysis", lambda item_id, values: updates.append(values))

    def must_not_run(*args, **kwargs):
        raise AssertionError("motion and audio analysis must not run without semantic evidence")

    monkeypatch.setattr(media_pipeline, "analyze_footage", must_not_run)
    result = media_pipeline.analyze_item("yt_x")

    assert result["analysis_status"] == "blocked"
    assert result["analysis_reason"] == "semantic_provider_not_configured"
    assert updates[-1]["analysis_status"] == "blocked"


def test_pipeline_passes_the_semantic_provider_into_analysis(monkeypatch):
    item = {"id": "yt_x", "source_id": "src_x", "media_status": "validated", "media_sha256": "ab" * 32,
            "media_probe": {"duration_seconds": 60.0}, "media_url": "https://media.example/x.mp4"}
    provider = lambda *args: {"analysis_evidence": {}}
    seen = {}

    def fake_analyze(path, **kwargs):
        seen.update(kwargs)
        return {"signals": {}, "candidates": [], "qualifying": []}

    monkeypatch.setattr(media_pipeline, "_load", lambda item_id: dict(item))
    monkeypatch.setattr(media_pipeline, "_load_source", lambda it: {"id": "src_x"})
    monkeypatch.setattr(media_pipeline, "_rights_decision", lambda source, now=None: (None, None))
    monkeypatch.setattr(media_pipeline, "_update_analysis", lambda item_id, values: None)
    monkeypatch.setattr(media_pipeline, "analyze_footage", fake_analyze)
    monkeypatch.setattr(media_pipeline.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(media_pipeline, "render_gate", lambda item, source: {"allowed": False, "reasons": []})

    result = media_pipeline.analyze_item("yt_x", semantic_fn=provider)

    assert result["analysis_status"] == "completed"
    assert seen["semantic_fn"] is provider
