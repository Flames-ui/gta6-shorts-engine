"""SRT, WebVTT and ASS writers, parsers used for round-trip checks, and sync validation."""
from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

from .timing import CaptionStyle, Cue, Word

Cue_T = Tuple[float, float, str]


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(t: float) -> str:
    return _srt_time(t).replace(",", ".")


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _plain_lines(cue: Cue) -> List[str]:
    return [" ".join(w.text for w in line) for line in cue.lines]


def to_srt(cues: Sequence[Cue]) -> str:
    blocks = []
    for i, cue in enumerate(cues, 1):
        blocks.append(f"{i}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n" + "\n".join(_plain_lines(cue)))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_vtt(cues: Sequence[Cue]) -> str:
    blocks = ["WEBVTT"]
    for cue in cues:
        blocks.append(f"{_vtt_time(cue.start)} --> {_vtt_time(cue.end)}\n" + "\n".join(_plain_lines(cue)))
    return "\n\n".join(blocks) + "\n"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _hex_to_ass(color: str) -> str:
    """#RRGGBB or #RRGGBBAA -> &HAABBGGRR as ASS expects (AA: 00 opaque, FF transparent)."""
    c = color.lstrip("#")
    if len(c) == 6:
        r, g, b, a = c[0:2], c[2:4], c[4:6], "00"
    elif len(c) == 8:
        r, g, b, a = c[0:2], c[2:4], c[4:6], c[6:8]
        a = format(255 - int(a, 16), "02X")  # CSS alpha -> ASS alpha
    else:
        raise ValueError(f"Invalid colour {color!r}")
    return f"&H{a.upper()}{b.upper()}{g.upper()}{r.upper()}"


def to_ass(cues: Sequence[Cue], style: CaptionStyle, play_w: int, play_h: int) -> str:
    bold = -1 if style.bold else 0
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_w}",
        f"PlayResY: {play_h}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{style.font},{style.size},{_hex_to_ass(style.primary)},{_hex_to_ass(style.highlight)},"
        f"{_hex_to_ass(style.outline_color)},{_hex_to_ass(style.back_color)},{bold},0,0,0,100,100,0,0,"
        f"{style.border_style},{style.outline},{style.shadow},2,80,80,{style.margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = []
    for cue in cues:
        pieces = []
        for li, line in enumerate(cue.lines):
            parts = []
            for w in line:
                token = _ass_escape(w.text)
                if style.karaoke:
                    cs = max(1, int(round((w.end - w.start) * 100)))
                    parts.append(f"{{\\k{cs}}}{token}")
                else:
                    parts.append(token)
            pieces.append(" ".join(parts))
        text = "\\N".join(pieces)
        events.append(f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Default,,0,0,0,,{text}")
    return "\n".join(header + events) + "\n"


# ---------------------------------------------------------------- parsing (round-trip checks)
_SRT_TIME = r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(text: str) -> List[Cue_T]:
    pattern = re.compile(rf"{_SRT_TIME} --> {_SRT_TIME}\n((?:.+\n?)+)")
    out = []
    for m in pattern.finditer(text.replace("\r\n", "\n") + "\n"):
        start = _to_seconds(*m.group(1, 2, 3, 4))
        end = _to_seconds(*m.group(5, 6, 7, 8))
        out.append((start, end, m.group(9).strip()))
    return out


def parse_vtt(text: str) -> List[Cue_T]:
    if not text.startswith("WEBVTT"):
        raise ValueError("Missing WEBVTT header")
    pattern = re.compile(rf"{_SRT_TIME} --> {_SRT_TIME}[^\n]*\n((?:.+\n?)+)")
    out = []
    for m in pattern.finditer(text.replace("\r\n", "\n") + "\n"):
        start = _to_seconds(*m.group(1, 2, 3, 4))
        end = _to_seconds(*m.group(5, 6, 7, 8))
        out.append((start, end, m.group(9).strip()))
    return out


def parse_ass_events(text: str) -> List[Cue_T]:
    if "[Events]" not in text or "[V4+ Styles]" not in text:
        raise ValueError("Missing ASS sections")
    out = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line[len("Dialogue:"):].split(",", 9)
        if len(fields) != 10:
            raise ValueError("Malformed ASS dialogue line")
        out.append((_ass_seconds(fields[1]), _ass_seconds(fields[2]),
                    re.sub(r"\{[^}]*\}", "", fields[9]).replace("\\N", " ").strip()))
    return out


def _ass_seconds(value: str) -> float:
    h, m, rest = value.strip().split(":")
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0


def validate_sync(cues: Sequence[Cue], clip_duration: float, words: Sequence[Word],
                  formats: Dict[str, str]) -> Dict:
    """Check captions against the clip: timing, ordering, text, and each written file's syntax."""
    issues: List[str] = []
    eps = 0.001
    prev_end = 0.0
    for i, cue in enumerate(cues, 1):
        if cue.start < -eps:
            issues.append(f"cue {i} starts before the clip (negative timestamp)")
        if cue.end <= cue.start:
            issues.append(f"cue {i} has non-positive duration")
        if cue.end > clip_duration + eps:
            issues.append(f"cue {i} ends after the clip ({cue.end:.2f}s > {clip_duration:.2f}s)")
        if cue.start + eps < prev_end:
            issues.append(f"cue {i} overlaps the previous cue")
        if not cue.text.strip():
            issues.append(f"cue {i} is empty")
        for w in cue.words:
            if w.start + eps < cue.start or w.end > cue.end + 0.05:
                issues.append(f"cue {i} does not contain its word '{w.text}' timing")
                break
        prev_end = cue.end
    if words and cues:
        first_word = min(w.start for w in words)
        if cues[0].start > first_word + 0.5:
            issues.append("captions begin noticeably after the first spoken word")
    expected = len(cues)
    if "srt" in formats:
        if len(parse_srt(formats["srt"])) != expected:
            issues.append("SRT cue count does not match")
    if "vtt" in formats:
        try:
            if len(parse_vtt(formats["vtt"])) != expected:
                issues.append("VTT cue count does not match")
        except ValueError as exc:
            issues.append(f"VTT is malformed: {exc}")
    if "ass" in formats:
        try:
            if len(parse_ass_events(formats["ass"])) != expected:
                issues.append("ASS event count does not match")
        except ValueError as exc:
            issues.append(f"ASS is malformed: {exc}")
    return {
        "ok": not issues,
        "issues": issues,
        "cue_count": expected,
        "first_cue_start": round(cues[0].start, 3) if cues else None,
        "last_cue_end": round(cues[-1].end, 3) if cues else None,
    }
