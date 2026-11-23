"""Speech-to-text with faster-whisper (local, no audio leaves the server).

Returns the transcript shape used everywhere else:
    {"language": str, "duration": float,
     "segments": [{"start", "end", "text", "words": [{"start", "end", "word"}]}]}
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

from ..config import settings
from ..errors import PipelineError

logger = logging.getLogger("studio.transcription")
_lock = threading.Lock()
_model = None


def _load_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            logger.info("loading whisper model", extra={"model": settings.WHISPER_MODEL,
                                                         "device": settings.WHISPER_DEVICE})
            _model = WhisperModel(
                settings.WHISPER_MODEL,
                device=settings.WHISPER_DEVICE,
                compute_type=settings.WHISPER_COMPUTE_TYPE,
            )
        return _model


def transcribe(path: str, language: Optional[str] = None) -> Dict:
    try:
        model = _load_model()
        segments_iter, info = model.transcribe(
            path,
            language=language,
            word_timestamps=True,
            vad_filter=True,
            beam_size=5,
        )
        segments = []
        for seg in segments_iter:
            words = [
                {"start": float(w.start), "end": float(w.end), "word": str(w.word)}
                for w in (seg.words or [])
                if w.word and w.word.strip()
            ]
            text = (seg.text or "").strip()
            if not text:
                continue
            segments.append({"start": float(seg.start), "end": float(seg.end), "text": text, "words": words})
    except PipelineError:
        raise
    except Exception as exc:
        logger.error("transcription failed", extra={"error_type": type(exc).__name__})
        raise PipelineError("transcription_failed",
                            "Speech could not be transcribed from this source.", retryable=True) from exc

    if not segments:
        raise PipelineError("no_speech_detected",
                            "No spoken words were detected, so no clips can be selected.", retryable=False)
    return {
        "language": getattr(info, "language", None) or language or "und",
        "duration": float(getattr(info, "duration", 0.0) or segments[-1]["end"]),
        "segments": segments,
    }
