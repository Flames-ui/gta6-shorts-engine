"""Test helpers: real media fixtures generated with FFmpeg, and deterministic speech timelines."""
from __future__ import annotations

import os
import random
import subprocess
from typing import Dict, List

# Distinct content vocabulary so that each moment's text is clearly different (duplicate detection).
WORD_BANK = (
    "budget forecast kitchen garden railway quantum violin harbor glacier ledger mentor "
    "orchard pottery satellite tundra venture walnut zebra beacon compass dynamo ember falcon "
    "gravel hammock igloo jigsaw kettle lantern magnet nectar oxygen pebble quartz ribbon saddle "
    "thimble umbrella vortex whistle xylophone yacht zephyr anchor bramble cobalt dune eclipse "
    "fjord goblet hazel iceberg juniper kayak lichen meadow nebula opal prairie quiver rapids "
    "sapphire timber upland velvet willow yarn acorn bishop cactus delta engine fossil granite "
    "helmet inlet jungle kernel lagoon marble noodle outpost plateau rooftop sparrow thunder "
    "alder basalt canyon drift estuary fennel gentian harrier isthmus juniper lattice mosaic "
    "nutmeg orbit parsley quarry rivet sorrel tundra utopia vellum wicket yarrow zinnia"
).split()


def make_video(path: str, *, duration: float = 12, width: int = 1280, height: int = 720,
               audio: bool = True, audio_gate: bool = False) -> str:
    """Create a real H.264/AAC MP4. audio_gate=True makes the first 3 s silent and then a tone."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    args = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30:duration={duration}",
    ]
    if audio:
        # Commas inside a lavfi expression must be escaped.
        expr = r"if(lt(t\,3)\,0\,0.3*sin(2*PI*440*t))" if audio_gate else "0.3*sin(2*PI*440*t)"
        args += ["-f", "lavfi", "-i", f"aevalsrc={expr}:s=48000:d={duration}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    args += ["-c:a", "aac", "-b:a", "96k"] if audio else ["-an"]
    args += [path]
    subprocess.run(args, check=True, timeout=300)
    return path


def make_sentences(count: int, seed: int = 7) -> List[str]:
    """Distinct sentences of 14 words each (mostly unique content words)."""
    rng = random.Random(seed)
    bank = WORD_BANK[:]
    rng.shuffle(bank)
    sentences = []
    for i in range(count):
        chunk = [bank[(i * 4 + j) % len(bank)] for j in range(4)]
        text = (f"The lesson is that {chunk[0]} and {chunk[1]} matter because {chunk[2]} "
                f"changes how {chunk[3]} works.")  # exactly 14 words
        sentences.append(text)
    return sentences


def speech_timeline(moment_starts: List[float], sentences_per_moment: int = 5, word_seconds: float = 0.28,
                    gap: float = 0.08, filler_every: int = 0) -> Dict:
    """Build a transcript (segments with word timings) placing sentences at given moment starts.

    Each sentence is one segment with 14 words. Returns {"segments", "duration", "language", "moments"}.
    """
    total_sentences = len(moment_starts) * sentences_per_moment
    sentences = make_sentences(total_sentences)
    segments, moments = [], []
    idx = 0
    for m_start in moment_starts:
        t = m_start
        m_first = None
        for _ in range(sentences_per_moment):
            words_text = sentences[idx].split()
            words, wt = [], t
            for w in words_text:
                words.append({"word": w, "start": round(wt, 3), "end": round(wt + word_seconds, 3)})
                wt += word_seconds
            seg = {"start": round(t, 3), "end": round(wt, 3), "text": sentences[idx], "words": words}
            segments.append(seg)
            if m_first is None:
                m_first = seg["start"]
            t = wt + gap
            idx += 1
        moments.append({"start": m_first, "end": round(segments[-1]["end"], 3), "text": " ".join(
            s["text"] for s in segments[-sentences_per_moment:])})
    return {"segments": segments, "language": "en", "moments": moments,
            "duration": max(s["end"] for s in segments) + 2.0}
