"""Turn word-level transcript timing into readable, synchronised caption cues.

Cues never start before their first spoken word, never run past the clip window,
never overlap, and break at pauses and sentence ends. Word-level times come
from the transcription engine; when it did not provide them, the segment text
is spread evenly across the segment and the result is marked as approximate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass
class Cue:
    start: float
    end: float
    lines: List[List[Word]] = field(default_factory=list)

    @property
    def words(self) -> List[Word]:
        return [w for line in self.lines for w in line]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass(frozen=True)
class CaptionStyle:
    name: str
    font: str
    size: int
    bold: bool
    primary: str
    highlight: str
    outline_color: str
    back_color: str
    border_style: int  # 1 = outline, 3 = opaque box
    outline: int
    shadow: int
    margin_v: int
    max_chars: int
    max_lines: int
    karaoke: bool


PRESETS: Dict[str, CaptionStyle] = {
    "clean_minimal": CaptionStyle("clean_minimal", "DejaVu Sans", 54, False, "#FFFFFF", "#FFFFFF",
                                  "#000000", "#00000000", 1, 2, 0, 330, 26, 2, False),
    "bold_creator": CaptionStyle("bold_creator", "DejaVu Sans", 82, True, "#FFFFFF", "#FFD400",
                                 "#000000", "#00000000", 1, 6, 2, 330, 18, 2, True),
    "high_contrast": CaptionStyle("high_contrast", "DejaVu Sans", 78, True, "#FFFFFF", "#FFFFFF",
                                  "#000000", "#000000E0", 3, 0, 0, 330, 22, 2, False),
    "modern_short": CaptionStyle("modern_short", "DejaVu Sans", 74, True, "#FFFFFF", "#00E5FF",
                                 "#000000", "#00000000", 1, 4, 0, 330, 20, 2, True),
    "highlight_keywords": CaptionStyle("highlight_keywords", "DejaVu Sans", 80, True, "#FFFFFF", "#7CFF6B",
                                       "#000000", "#00000000", 1, 5, 1, 330, 20, 2, True),
}

MAX_CUE_SECONDS = 3.2
PAUSE_SECONDS = 0.7
MIN_CUE_SECONDS = 0.5


def get_style(name: str) -> CaptionStyle:
    if name not in PRESETS:
        raise ValueError(f"Unknown caption preset {name!r}. Choose one of: {', '.join(PRESETS)}")
    return PRESETS[name]


def words_in_window(segments: Sequence[Dict], start: float, end: float) -> Tuple[List[Word], bool]:
    """Words whose timing falls inside [start, end], shifted to clip-relative time.

    Returns (words, word_level_timing). word_level_timing is False when any
    segment had to be approximated from its text.
    """
    out: List[Word] = []
    exact = True
    for seg in segments:
        s_start, s_end = float(seg["start"]), float(seg["end"])
        if s_end <= start or s_start >= end:
            continue
        words = seg.get("words") or []
        if words:
            for w in words:
                ws, we = float(w["start"]), float(w["end"])
                if we <= start or ws >= end:
                    continue
                text = str(w["word"]).strip()
                if text:
                    out.append(Word(max(ws, start) - start, min(we, end) - start, text))
        else:
            exact = False
            tokens = str(seg.get("text", "")).split()
            if not tokens:
                continue
            span = max(s_end - s_start, 0.01)
            step = span / len(tokens)
            for i, tok in enumerate(tokens):
                ws = s_start + i * step
                we = ws + step
                if we <= start or ws >= end:
                    continue
                out.append(Word(max(ws, start) - start, min(we, end) - start, tok))
    out.sort(key=lambda w: (w.start, w.end))
    return out, exact


def _wrap(words: List[Word], max_chars: int) -> List[List[Word]]:
    lines: List[List[Word]] = []
    current: List[Word] = []
    length = 0
    for w in words:
        extra = len(w.text) + (1 if current else 0)
        if current and length + extra > max_chars:
            lines.append(current)
            current, length = [w], len(w.text)
        else:
            current.append(w)
            length += extra
    if current:
        lines.append(current)
    return lines


def build_cues(words: List[Word], style: CaptionStyle, clip_duration: float) -> List[Cue]:
    cues: List[Cue] = []
    pending: List[Word] = []

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        lines = _wrap(pending, style.max_chars)
        start = pending[0].start
        end = max(pending[-1].end, start + MIN_CUE_SECONDS)
        cues.append(Cue(start=max(0.0, start), end=min(end, clip_duration), lines=lines))
        pending = []

    for w in words:
        if pending:
            gap = w.start - pending[-1].end
            span = w.end - pending[0].start
            candidate_lines = _wrap(pending + [w], style.max_chars)
            if gap > PAUSE_SECONDS or span > MAX_CUE_SECONDS or len(candidate_lines) > style.max_lines:
                flush()
        pending.append(w)
        if w.text.rstrip("\"')").endswith((".", "?", "!")) and len(pending) >= 3:
            flush()
    flush()

    # Resolve any overlap so each cue owns its time range.
    for i in range(len(cues) - 1):
        nxt = cues[i + 1].start
        if cues[i].end > nxt:
            cues[i].end = max(cues[i].start + 0.1, nxt - 0.01)
    return [c for c in cues if c.end > c.start and c.text.strip()]
