"""Semantic evidence for candidate windows, from a multimodal model that sees and hears each window.

Motion and loudness cannot supply hook, story, reaction or caption evidence. This module cuts one
candidate window out of the validated footage, sends that short clip (video and audio) to Gemini,
and turns the model's per-factor judgments into the evidence records QualityCandidateScorer reads.

Fail closed: a missing key, an upload or model error, or an unreadable answer raises
SemanticAnalysisError. A factor the model did not justify is left out, so the scorer rejects the
window with its own "Missing ... evidence" reason. Nothing is given a default score.
Uploaded clips are deleted from the provider after each window, whether or not the call succeeds.
"""
import json
import logging
import math
import os
import re
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, Optional

from ...config import settings

logger = logging.getLogger("gta6_shorts.discovery.semantic")

SEMANTIC_FACTORS = (
    "hook_strength",
    "entertainment_value",
    "story_clarity",
    "reaction_payoff",
    "visual_audio_quality",
    "caption_potential",
    "replay_potential",
)
# The scorer reads these four as fractions (0 to 1) and the other three as points in their own scale.
# Model scores are 0 to 10, so each factor is converted to the unit its scorer branch expects.
FRACTION_FACTORS = frozenset({"hook_strength", "entertainment_value", "story_clarity", "reaction_payoff"})
HALF_SCALE_FACTORS = frozenset({"replay_potential"})  # replay is worth 5 points

WINDOW_MAX_SECONDS = 60.0
FFMPEG_TIMEOUT_SECONDS = 180
UPLOAD_POLL_SECONDS = 2.0
UPLOAD_TIMEOUT_SECONDS = 300.0
JUSTIFICATION_MAX_CHARS = 300

PROMPT_TEMPLATE = """You are reviewing one short window of gaming footage, with its audio, for a vertical short-form clip.
The window is {length:.1f} seconds long. Judge only what you can see or hear inside it. Do not assume
story, characters or events beyond what is shown.

For each factor below, return an integer score from 0 to 10, a one-sentence justification that names
what you saw or heard, and observed_at_s: the second inside this window (0 to {length:.1f}) where that
factor is most evident. If a factor is absent, score it low and say why. Do not give credit for
anything you cannot observe.

Factors:
- hook_strength: does the opening grab attention within the first three seconds
- entertainment_value: is it funny, tense, impressive or otherwise engaging to watch
- story_clarity: can a viewer follow what happens without any context
- reaction_payoff: is there a visible or audible reaction or result that pays off the set-up
- visual_audio_quality: is the picture and sound clear enough to publish
- caption_potential: would a short on-screen line fit this moment
- replay_potential: would a viewer want to watch it again

Also return suggested_hook and suggested_caption as short strings, or empty strings if none fit.
Return only JSON in this shape:
{{"factors": {{"<factor>": {{"score": 0, "justification": "...", "observed_at_s": 0.0}}}},
  "suggested_hook": "", "suggested_caption": ""}}
"""


class SemanticAnalysisError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# (video_path, window_start, window_end, candidate_id, source_id) -> evidence bundle
SemanticFn = Callable[[str, float, float, str, str], Dict[str, Any]]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:80] or "window"


def parse_model_output(text: str, window_length: float) -> Dict[str, Any]:
    """Validate the model's JSON. Scores are clamped to 0-10 and timestamps to the window; bad factors are dropped."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise SemanticAnalysisError("semantic_output_invalid", "model output is not JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("factors"), dict):
        raise SemanticAnalysisError("semantic_output_invalid", "model output has no factors object")

    factors: Dict[str, Dict[str, Any]] = {}
    for name in SEMANTIC_FACTORS:
        item = data["factors"].get(name)
        if not isinstance(item, dict):
            continue
        score = _number(item.get("score"))
        observed = _number(item.get("observed_at_s"))
        justification = str(item.get("justification") or "").strip()[:JUSTIFICATION_MAX_CHARS]
        if score is None or observed is None or not justification:
            continue  # no score without a timestamp and a reason: the scorer will reject the window
        factors[name] = {
            "score": min(10.0, max(0.0, score)),
            "observed_at_s": min(max(0.0, window_length), max(0.0, observed)),
            "justification": justification,
        }
    return {
        "factors": factors,
        "suggested_hook": str(data.get("suggested_hook") or "").strip()[:120],
        "suggested_caption": str(data.get("suggested_caption") or "").strip()[:150],
    }


def scorer_evidence(parsed: Dict[str, Any], *, model: str, cand_id: str, source_id: str,
                    window_start: float) -> Dict[str, Dict[str, Any]]:
    """Build the per-factor records QualityCandidateScorer accepts: a reference, source, timestamp and score."""
    evidence: Dict[str, Dict[str, Any]] = {}
    for name, item in parsed["factors"].items():
        model_score = item["score"]
        if name in FRACTION_FACTORS:
            value = model_score / 10.0
        elif name in HALF_SCALE_FACTORS:
            value = model_score * 0.5
        else:
            value = model_score
        evidence[name] = {
            "score": round(value, 4),
            "evidence_reference": f"gemini:{model}:{cand_id}:{name}",
            "source_id": source_id,
            # Never 0 or empty: the scorer treats a falsy timestamp as missing evidence.
            "observed_at": f"{window_start + item['observed_at_s']:.2f}s",
            "model_score_0_10": round(model_score, 2),
            "justification": item["justification"],
        }
    return evidence


def extract_window(source_path: str, start: float, length: float, dest_path: str) -> None:
    """Cut one window out of the footage as a small H.264 clip with its audio. The source is never modified."""
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.2f}", "-i", source_path, "-t", f"{length:.2f}",
        "-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", dest_path,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as exc:
        raise SemanticAnalysisError("window_extract_failed", str(exc)[:200]) from exc
    if not os.path.isfile(dest_path) or os.path.getsize(dest_path) == 0:
        raise SemanticAnalysisError("window_extract_failed", "ffmpeg produced no output")


def _state_name(file_obj: Any) -> str:
    state = getattr(file_obj, "state", None)
    return str(getattr(state, "name", state) or "").upper()


class GeminiWindowEvaluator:
    """Asks Gemini about one window at a time. Construct with an API key, or with a client for tests."""

    def __init__(self, api_key: str = "", model: str = "", client: Any = None):
        self.model = model or settings.GEMINI_MODEL
        if client is None:
            if not api_key:
                raise SemanticAnalysisError("semantic_provider_not_configured", "GEMINI_API_KEY is not set")
            from google import genai  # imported lazily so the module loads without the SDK configured
            client = genai.Client(api_key=api_key)
        self.client = client

    def __call__(self, video_path: str, start: float, end: float, cand_id: str, source_id: str) -> Dict[str, Any]:
        length = end - start
        if not (0.0 < length <= WINDOW_MAX_SECONDS):
            raise SemanticAnalysisError("window_length_invalid", f"window length {length:.2f}s")
        with tempfile.TemporaryDirectory(prefix="gta6_semantic_") as tmp:
            clip = os.path.join(tmp, f"{_safe(cand_id)}.mp4")
            extract_window(video_path, start, length, clip)
            text = self._ask_model(clip, length)
        parsed = parse_model_output(text, length)
        evidence = scorer_evidence(parsed, model=self.model, cand_id=cand_id, source_id=source_id,
                                   window_start=start)
        logger.info("discovery.semantic.evaluated %s", {
            "candidate_id": cand_id,
            "source_id": source_id,
            "model": self.model,
            "window_start": round(start, 2),
            "window_end": round(end, 2),
            "factors_scored": sorted(parsed["factors"]),
            "factors_missing": sorted(set(SEMANTIC_FACTORS) - set(parsed["factors"])),
        })
        return {
            "analysis_evidence": evidence,
            "suggested_hook": parsed["suggested_hook"],
            "suggested_caption": parsed["suggested_caption"],
            "model": self.model,
        }

    def _ask_model(self, clip_path: str, length: float) -> str:
        file_name = None
        try:
            uploaded = self.client.files.upload(file=clip_path, config={"mime_type": "video/mp4"})
            file_name = uploaded.name
            uploaded = self._wait_until_active(uploaded)
            response = self.client.models.generate_content(
                model=self.model,
                contents=[uploaded, PROMPT_TEMPLATE.format(length=length)],
                config={"response_mime_type": "application/json", "temperature": 0},
            )
            text = getattr(response, "text", None)
        except SemanticAnalysisError:
            raise
        except Exception as exc:  # provider errors are reported with their type, never with the payload
            raise SemanticAnalysisError("semantic_provider_error", type(exc).__name__) from exc
        finally:
            if file_name:
                self._delete_upload(file_name)
        if not text:
            raise SemanticAnalysisError("semantic_output_empty", "model returned no text")
        return text

    def _wait_until_active(self, uploaded: Any) -> Any:
        deadline = time.monotonic() + UPLOAD_TIMEOUT_SECONDS
        state = _state_name(uploaded)
        while state == "PROCESSING":
            if time.monotonic() > deadline:
                raise SemanticAnalysisError("semantic_upload_timeout", "provider still processing the clip")
            time.sleep(UPLOAD_POLL_SECONDS)
            uploaded = self.client.files.get(name=uploaded.name)
            state = _state_name(uploaded)
        if state != "ACTIVE":
            raise SemanticAnalysisError("semantic_upload_failed", f"provider file state {state or 'unknown'}")
        return uploaded

    def _delete_upload(self, file_name: str) -> None:
        try:
            self.client.files.delete(name=file_name)
        except Exception as exc:  # cleanup must not hide the analysis result
            logger.warning("discovery.semantic.cleanup_failed %s", {"error": type(exc).__name__})


def default_semantic_fn() -> Optional[SemanticFn]:
    """The configured Gemini evaluator, or None when no key is set. None means analysis must not run."""
    if not settings.GEMINI_API_KEY:
        return None
    return GeminiWindowEvaluator(api_key=settings.GEMINI_API_KEY)
