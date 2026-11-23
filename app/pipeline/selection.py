"""Deterministic clip selection and boundary rules.

The model proposes candidates; this module decides. A candidate is accepted only
when it passes every rule below, and the job renders exactly five accepted clips
or fails with the reasons. Nothing is padded to reach five.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..ai.analysis import Candidate
from ..captions.timing import Word
from ..errors import PipelineError
from ..media.probe import silent_fraction

SNAP_TOLERANCE_S = 1.5
MIN_CONFIDENCE = 0.6
MAX_SILENT_FRACTION = 0.4
MIN_WORDS = 12
PAUSE_END_S = 0.6
DUPLICATE_SIMILARITY = 0.6
_INTRO = re.compile(r"^\s*(hey|hi|hello|welcome back|what'?s up|good (morning|afternoon|evening))\b", re.I)
_STOPWORDS = frozenset("a an and are as at be but by for from has have i in is it of on or that the this to was we with you".split())


@dataclass
class Decision:
    candidate: Candidate
    start: float
    end: float
    accepted: bool
    reason: Optional[str]
    rank: Optional[int] = None
    text: str = ""
    snapped: bool = False

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


def _nearest(values: Sequence[float], target: float) -> Optional[float]:
    best = None
    for v in values:
        if abs(v - target) <= SNAP_TOLERANCE_S and (best is None or abs(v - target) < abs(best - target)):
            best = v
    return best


def snap_to_segments(segments: Sequence[Dict], start: float, end: float) -> Tuple[float, float, bool]:
    starts = [float(s["start"]) for s in segments]
    ends = [float(s["end"]) for s in segments]
    ns, ne = _nearest(starts, start), _nearest(ends, end)
    snapped = ns is not None and ne is not None
    return (ns if ns is not None else start), (ne if ne is not None else end), snapped


def window_text(words: Sequence[Word]) -> str:
    return " ".join(w.text for w in words)


def _cuts_word(words: Sequence[Dict], start: float, end: float) -> bool:
    for w in words:
        ws, we = float(w["start"]), float(w["end"])
        if ws < start - 0.05 < we - 0.05 or ws < end - 0.05 < we - 0.05:
            if ws > start + 0.05 or we < end - 0.05:
                return True
    return False


def _ends_cleanly(words: Sequence[Dict], end: float, duration: float) -> bool:
    inside = [w for w in words if float(w["end"]) <= end + 0.05 and float(w["end"]) > end - 4.0]
    if not inside:
        return False
    last = inside[-1]
    if re.search(r"[.?!\"')\]]$", str(last["word"]).strip()):
        return True
    following = [w for w in words if float(w["start"]) >= end - 0.05]
    if not following:
        return end >= duration - 0.5
    return float(following[0]["start"]) - end >= PAUSE_END_S


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9']+", text.lower()) if t not in _STOPWORDS and len(t) > 2}


def _similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def evaluate(
    candidates: Sequence[Candidate],
    *,
    segments: Sequence[Dict],
    words: Sequence[Dict],
    silence: Sequence[Tuple[float, float]],
    duration: float,
    min_seconds: int,
    max_seconds: int,
    min_score: int,
) -> List[Decision]:
    decisions: List[Decision] = []
    for c in candidates:
        start, end, snapped = snap_to_segments(segments, c.start, c.end)
        start, end = max(0.0, start), min(duration, end)
        span_words = [w for w in words if float(w["end"]) > start and float(w["start"]) < end]
        text = " ".join(str(w["word"]).strip() for w in span_words)
        d = Decision(candidate=c, start=round(start, 3), end=round(end, 3), accepted=False, reason=None,
                     text=text, snapped=snapped)

        reason = None
        if end <= start:
            reason = "invalid_range"
        elif end - start < min_seconds:
            reason = "too_short"
        elif end - start > max_seconds:
            reason = "too_long"
        elif not c.standalone:
            reason = "not_standalone"
        elif c.confidence < MIN_CONFIDENCE:
            reason = "low_confidence"
        elif c.engagement_score < min_score:
            reason = "below_quality_threshold"
        elif silent_fraction(list(silence), start, end) > MAX_SILENT_FRACTION:
            reason = "mostly_silent"
        elif len(span_words) < MIN_WORDS:
            reason = "too_little_speech"
        elif _INTRO.search(text):
            reason = "introductory_filler"
        elif _cuts_word(words, start, end):
            reason = "cuts_through_word"
        elif not _ends_cleanly(words, end, duration):
            reason = "incomplete_thought"

        d.reason = reason
        d.accepted = reason is None
        decisions.append(d)
    return decisions


def choose(decisions: List[Decision], count: int) -> Tuple[List[Decision], List[Decision]]:
    """Pick up to `count` accepted decisions, best first, with no overlap and no repeated moments."""
    pool = sorted(
        (d for d in decisions if d.accepted),
        key=lambda d: (-d.candidate.engagement_score, -d.candidate.confidence, d.start),
    )
    chosen: List[Decision] = []
    for d in pool:
        if len(chosen) >= count:
            break
        if any(min(d.end, c.end) - max(d.start, c.start) > 0 for c in chosen):
            d.accepted, d.reason = False, "overlaps_selected_clip"
            continue
        if any(_similar(d.text, c.text) >= DUPLICATE_SIMILARITY for c in chosen):
            d.accepted, d.reason = False, "repeats_selected_moment"
            continue
        chosen.append(d)
    for i, d in enumerate(chosen, 1):
        d.rank = i
    for d in decisions:
        if d.accepted and d not in chosen:
            d.accepted, d.reason = False, d.reason or "not_in_top_candidates"
    return chosen, decisions


def insufficient_message(decisions: List[Decision], count: int, chosen: int) -> str:
    reasons: Dict[str, int] = {}
    for d in decisions:
        if d.reason:
            reasons[d.reason] = reasons.get(d.reason, 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])) or "none proposed"
    return (f"Only {chosen} of {count} moments met the quality bar, so no clips were rendered. "
            f"Rejections: {summary}.")


def ensure_five(chosen: List[Decision], decisions: List[Decision], count: int) -> None:
    if len(chosen) < count:
        raise PipelineError("insufficient_highlights", insufficient_message(decisions, count, len(chosen)),
                            retryable=False)
