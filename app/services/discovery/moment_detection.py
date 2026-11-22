"""Evidence-based candidate moments from validated footage.

What is measured here: per-frame motion (frame difference) and audio loudness, each with a timestamp.
The strongest measured peaks become candidate windows. Each window is scored with the existing
100-point QualityCandidateScorer, and only a total of 75 or more qualifies.

Measured motion and loudness cannot supply the hook, story, reaction, caption or entertainment
evidence the scorer requires. So a window without semantic analysis evidence is rejected with the
scorer's own reasons. Nothing is scored by default, and the legacy heuristic in
GameplayAnalyzerService.analyze_gameplay_footage is not used, because it assigns fixed or default
scores.
"""
import bisect
import hashlib
import logging
import math
from typing import Any, Callable, Dict, List, Optional

from ..gameplay_analyzer import GameplayAnalyzerService
from ..quality_candidate_scorer import QualityCandidateScorer

logger = logging.getLogger("gta6_shorts.discovery.moments")

MIN_PASSING_SCORE = QualityCandidateScorer.MIN_PASSING_SCORE  # 75.0, the existing minimum
MIN_SIGNAL = 0.6          # combined peak must reach 60% of the strongest measured sample
PEAK_SEPARATION_S = 20.0  # candidate peaks are at least this far apart
WINDOW_LEAD_S = 10.0      # window starts this long before the peak
WINDOW_LENGTH_S = 30.0
MIN_WINDOW_S = 15.0
MAX_CANDIDATES = 8
AUDIO_MATCH_TOLERANCE_S = 1.5
MAX_ANALYSIS_SECONDS = 600.0      # longest footage analysed inside the HTTP request cycle
MAX_BACKGROUND_SECONDS = 4 * 3600.0  # longest footage the background worker will analyse


class MomentAnalysisError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


ScoreFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def _default_score(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return QualityCandidateScorer().score_candidate(candidate, persist_evaluation=False)


def _finite(*values: Any) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def build_signal_timeline(motion: List[Dict[str, Any]], audio: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combine motion and audio on the motion timeline. Each sample keeps its raw measured values."""
    motion_points = sorted(
        (float(m["time"]), float(m["motion"])) for m in motion
        if isinstance(m, dict) and "time" in m and "motion" in m and _finite(m.get("time"), m.get("motion"))
    )
    if not motion_points:
        return []
    audio_points = sorted(
        (float(a["time"]), float(a["energy"])) for a in audio
        if isinstance(a, dict) and "time" in a and "energy" in a and _finite(a.get("time"), a.get("energy"))
    )
    audio_times = [t for t, _ in audio_points]
    max_motion = max(v for _, v in motion_points)
    max_audio = max((v for _, v in audio_points), default=0.0)

    timeline: List[Dict[str, Any]] = []
    for t, motion_value in motion_points:
        motion_norm = motion_value / max_motion if max_motion > 0 else 0.0
        audio_raw: Optional[float] = None
        if audio_points:
            idx = bisect.bisect_left(audio_times, t)
            neighbours = [i for i in (idx - 1, idx) if 0 <= i < len(audio_points)]
            best = min(neighbours, key=lambda i: abs(audio_times[i] - t))
            if abs(audio_times[best] - t) <= AUDIO_MATCH_TOLERANCE_S:
                audio_raw = audio_points[best][1]
        if audio_raw is None:
            combined = motion_norm
        else:
            audio_norm = audio_raw / max_audio if max_audio > 0 else 0.0
            combined = 0.5 * motion_norm + 0.5 * audio_norm
        timeline.append({
            "time": t,
            "motion_raw": motion_value,
            "audio_raw": audio_raw,
            "combined": round(combined, 4),
        })
    return timeline


def find_candidate_peaks(timeline: List[Dict[str, Any]], limit: int = MAX_CANDIDATES) -> List[Dict[str, Any]]:
    picked: List[Dict[str, Any]] = []
    for sample in sorted(timeline, key=lambda s: s["combined"], reverse=True):
        if sample["combined"] < MIN_SIGNAL:
            break
        if any(abs(sample["time"] - p["time"]) < PEAK_SEPARATION_S for p in picked):
            continue
        picked.append(sample)
        if len(picked) >= limit:
            break
    return picked


def window_for_peak(peak_time: float, duration: float) -> Optional[Dict[str, float]]:
    start = max(0.0, peak_time - WINDOW_LEAD_S)
    end = min(duration, start + WINDOW_LENGTH_S)
    if end - start < MIN_WINDOW_S:
        return None
    return {"start": round(start, 2), "end": round(end, 2)}


def _measured_in_window(timeline: List[Dict[str, Any]], start: float, end: float) -> Dict[str, Any]:
    inside = [s for s in timeline if start <= s["time"] <= end]
    audio_values = [s["audio_raw"] for s in inside if s["audio_raw"] is not None]
    return {
        "samples": len(inside),
        "peak_motion_raw": round(max((s["motion_raw"] for s in inside), default=0.0), 3),
        "peak_audio_energy_raw": round(max(audio_values), 3) if audio_values else None,
    }


def _audio_timeout(duration: float) -> int:
    """FFmpeg audio pass gets time in proportion to the footage length."""
    return int(max(60.0, duration * 2.0))


def analyze_footage(
    path: str,
    *,
    item_id: str,
    source_id: str,
    duration: float,
    max_seconds: float = MAX_BACKGROUND_SECONDS,
    score_fn: Optional[ScoreFn] = None,
    motion_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    audio_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Measure motion and audio, pick candidate windows and score each one. Never renders anything."""
    if duration > max_seconds:
        raise MomentAnalysisError(
            "duration_exceeds_background_limit",
            f"Footage is {duration:.0f}s; analysis is limited to {max_seconds:.0f}s.",
        )
    score = score_fn or _default_score
    motion = (motion_fn or GameplayAnalyzerService.detect_visual_motion_peaks)(path, sample_interval=0.5)
    if audio_fn is not None:
        audio = audio_fn(path)
    else:
        audio = GameplayAnalyzerService.detect_audio_energy_peaks(path, timeout=_audio_timeout(duration))
    timeline = build_signal_timeline(motion or [], audio or [])
    if not timeline:
        raise MomentAnalysisError("no_motion_signal", "Motion analysis produced no measurable samples.")

    candidates: List[Dict[str, Any]] = []
    for peak in find_candidate_peaks(timeline):
        window = window_for_peak(peak["time"], duration)
        if window is None:
            continue
        cand_id = "cand_" + hashlib.sha256(
            f"{item_id}:{window['start']}:{window['end']}".encode()
        ).hexdigest()[:12]
        measured = _measured_in_window(timeline, window["start"], window["end"])
        scorer_input = {
            "id": cand_id,
            "source_id": source_id,
            "source_video_id": item_id,
            "start_time": window["start"],
            "end_time": window["end"],
            "duration": round(window["end"] - window["start"], 2),
            "source_duration": duration,
            "analysis_evidence": {},
        }
        result = score(scorer_input)
        total = float(result.get("total_score") or 0.0)
        candidates.append({
            "candidate_id": cand_id,
            "start_time": window["start"],
            "end_time": window["end"],
            "peak_time": round(peak["time"], 2),
            "peak_signal": peak["combined"],
            "measured": measured,
            "total_score": round(total, 2),
            "qualifies": total >= MIN_PASSING_SCORE,
            "rejection_reasons": list(result.get("rejection_reasons") or []),
        })

    candidates.sort(key=lambda c: c["total_score"], reverse=True)
    qualifying = [c for c in candidates if c["qualifies"]]
    logger.info("discovery.moments.analyzed %s", {
        "item_id": item_id,
        "motion_samples": len(motion or []),
        "audio_samples": len(audio or []),
        "audio_available": bool(audio),
        "candidate_windows": len(candidates),
        "qualifying": len(qualifying),
        "minimum_score": MIN_PASSING_SCORE,
    })
    return {
        "signals": {
            "motion_samples": len(motion or []),
            "audio_samples": len(audio or []),
            "audio_available": bool(audio),
        },
        "candidates": candidates,
        "qualifying": qualifying,
    }
