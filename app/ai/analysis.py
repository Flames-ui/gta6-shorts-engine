"""Chunked, timestamp-grounded highlight analysis.

* The transcript is split into overlapping windows so long sources stay within
  model limits and no moment is missed at a window boundary.
* Each window is sent as timestamped lines, so the model can only point at real
  transcript timing.
* Every response is parsed and validated field by field. Invalid items are
  dropped with a reason; a response with no valid items is retried.
"""
from __future__ import annotations

import json
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..errors import PipelineError
from .provider import HighlightProvider, ModelUsage, ProviderError

logger = logging.getLogger("studio.ai.analysis")
CHUNK_SECONDS = 900
OVERLAP_SECONDS = 60
REQUIRED_FIELDS = ("start_seconds", "end_seconds", "title", "description", "rationale",
                   "engagement_score", "confidence", "category", "standalone")


@dataclass
class Candidate:
    start: float
    end: float
    title: str
    description: str
    rationale: str
    engagement_score: int
    confidence: float
    category: str
    standalone: bool
    source_window: Tuple[float, float] = (0.0, 0.0)


@dataclass
class AnalysisOutcome:
    candidates: List[Candidate] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    usage: ModelUsage = field(default_factory=ModelUsage)
    windows: int = 0
    provider: str = ""
    model: str = ""


def chunk_windows(duration: float, chunk: int = CHUNK_SECONDS, overlap: int = OVERLAP_SECONDS) -> List[Tuple[float, float]]:
    if duration <= chunk:
        return [(0.0, duration)]
    windows, start = [], 0.0
    while start < duration:
        end = min(duration, start + chunk)
        windows.append((start, end))
        if end >= duration:
            break
        start = end - overlap
    return windows


def transcript_lines(segments: Sequence[Dict], window: Tuple[float, float]) -> str:
    lines = []
    for seg in segments:
        s, e = float(seg["start"]), float(seg["end"])
        if e <= window[0] or s >= window[1]:
            continue
        text = " ".join(str(seg.get("text", "")).split())
        if text:
            lines.append(f"[{s:.2f}-{e:.2f}] {text}")
    return "\n".join(lines)


def build_prompt(*, title: str, duration: float, window: Tuple[float, float], lines: str,
                 min_seconds: int, max_seconds: int, wanted: int, language: Optional[str]) -> str:
    return f"""You are selecting standalone short-form clips from a long video transcript.

Source title: {title[:200]!r}
Source duration: {duration:.1f} seconds
Only consider moments that start and end between {window[0]:.2f} and {window[1]:.2f} seconds.
Transcript language: {language or 'auto-detected'}

Transcript lines are formatted as [start-end] text, with times in seconds from the start of the source.

Task: propose up to {wanted} moments that would work on their own as vertical short-form videos.
Good moments: a clear claim, a question with an answer, a concrete insight, a surprising or emotional statement,
a complete explanation, a strong opening line with payoff. Avoid greetings, housekeeping, sponsor reads,
repetition, incomplete thoughts, and moments that need earlier context to make sense.

Rules:
- start_seconds must be the start time of a transcript line, and end_seconds must be the end time of a transcript line.
- Each clip must last between {min_seconds} and {max_seconds} seconds.
- Do not invent times or text. Quote the topic faithfully.
- engagement_score is an integer 0-100 reflecting how compelling the moment is. Be strict: most moments score below 70.
- confidence is a number 0-1 for how sure you are the moment is self-contained and accurate.
- standalone is true only if a viewer with no other context would follow the moment.

Return only JSON in this shape:
{{"highlights": [{{"start_seconds": 0.0, "end_seconds": 0.0, "title": "max 80 chars",
"description": "one or two sentences", "rationale": "why this works", "engagement_score": 0,
"confidence": 0.0, "category": "short label", "standalone": true}}]}}

Transcript:
{lines}
"""


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_candidates(text: str, window: Tuple[float, float], min_s: int, max_s: int) -> Tuple[List[Candidate], List[str]]:
    """Validate a model response. Returns (valid candidates, rejection reasons). Raises ValueError if not JSON."""
    cleaned = _FENCE.sub("", text.strip()).strip()
    data = json.loads(cleaned)
    items = data.get("highlights") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("response has no highlights list")

    valid: List[Candidate] = []
    rejected: List[str] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            rejected.append(f"item {idx}: not an object")
            continue
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            rejected.append(f"item {idx}: missing {', '.join(missing)}")
            continue
        try:
            start = float(item["start_seconds"])
            end = float(item["end_seconds"])
            score = int(round(float(item["engagement_score"])))
            confidence = float(item["confidence"])
        except (TypeError, ValueError):
            rejected.append(f"item {idx}: non-numeric timing, score or confidence")
            continue
        if not all(math.isfinite(v) for v in (start, end, confidence)):
            rejected.append(f"item {idx}: non-finite values")
            continue
        if not (0 <= score <= 100) or not (0.0 <= confidence <= 1.0):
            rejected.append(f"item {idx}: score or confidence out of range")
            continue
        if not isinstance(item["standalone"], bool):
            rejected.append(f"item {idx}: standalone must be a boolean")
            continue
        title = str(item["title"]).strip()[:120]
        if not title:
            rejected.append(f"item {idx}: empty title")
            continue
        duration = end - start
        if duration < min_s - 0.5 or duration > max_s + 0.5:
            rejected.append(f"item {idx}: duration {duration:.1f}s outside {min_s}-{max_s}s")
            continue
        if start < window[0] - 0.5 or end > window[1] + 0.5:
            rejected.append(f"item {idx}: outside analysed window")
            continue
        valid.append(Candidate(
            start=max(0.0, start), end=end, title=title,
            description=str(item["description"]).strip()[:1000],
            rationale=str(item["rationale"]).strip()[:1000],
            engagement_score=score, confidence=confidence,
            category=str(item["category"]).strip()[:60] or "general",
            standalone=item["standalone"], source_window=window,
        ))
    return valid, rejected


def _backoff(attempt: int, provider_error: ProviderError) -> float:
    base = 15.0 if provider_error.code == "rate_limited" else 2.0
    return min(60.0, base * (2 ** attempt)) + random.uniform(0, 0.5)


def analyze(
    *,
    provider: HighlightProvider,
    segments: Sequence[Dict],
    duration: float,
    title: str,
    language: Optional[str],
    min_seconds: int,
    max_seconds: int,
    wanted: int,
    max_retries: int,
    sleep: Callable[[float], None] = time.sleep,
    on_window: Optional[Callable[[int, int], None]] = None,
) -> AnalysisOutcome:
    outcome = AnalysisOutcome(provider=provider.name, model=provider.model)
    windows = chunk_windows(duration)
    for n, window in enumerate(windows, 1):
        if on_window:
            on_window(n, len(windows))
        lines = transcript_lines(segments, window)
        if not lines:
            outcome.rejected.append(f"window {n}: no transcript text")
            continue
        prompt = build_prompt(title=title, duration=duration, window=window, lines=lines,
                              min_seconds=min_seconds, max_seconds=max_seconds, wanted=wanted, language=language)
        attempt = 0
        while True:
            try:
                text, usage = provider.generate_json(prompt)
            except ProviderError as err:
                if err.retryable and attempt < max_retries:
                    delay = _backoff(attempt, err)
                    logger.warning("analysis window %d retry after %s", n, err.code,
                                   extra={"window": n, "attempt": attempt + 1, "delay_s": round(delay, 1)})
                    sleep(delay)
                    attempt += 1
                    continue
                raise PipelineError(f"ai_{err.code}", err.message, retryable=err.retryable) from err
            try:
                valid, rejected = parse_candidates(text, window, min_seconds, max_seconds)
            except (ValueError, json.JSONDecodeError) as exc:
                if attempt < max_retries:
                    logger.warning("analysis window %d returned invalid JSON; retrying", n)
                    sleep(_backoff(attempt, ProviderError("invalid_response", "", True)))
                    attempt += 1
                    continue
                raise PipelineError("ai_invalid_response",
                                    "The AI provider returned a response that could not be validated.",
                                    retryable=True) from exc
            outcome.candidates.extend(valid)
            outcome.rejected.extend(f"window {n}: {r}" for r in rejected)
            outcome.usage = ModelUsage(outcome.usage.input_tokens + usage.input_tokens,
                                       outcome.usage.output_tokens + usage.output_tokens)
            break
        outcome.windows += 1
    return outcome


def merge_overlapping(cands: List[Candidate]) -> List[Candidate]:
    """Windows overlap, so the same moment can be proposed twice. Keep the stronger of any overlapping pair."""
    ordered = sorted(cands, key=lambda c: (-c.engagement_score, -c.confidence, c.start))
    kept: List[Candidate] = []
    for c in ordered:
        if any(min(c.end, k.end) - max(c.start, k.start) > 0.5 for k in kept):
            continue
        kept.append(c)
    return sorted(kept, key=lambda c: c.start)
