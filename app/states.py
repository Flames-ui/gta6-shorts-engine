"""Editing job state machine. Every status change goes through `allowed()`."""
from __future__ import annotations

from typing import Dict, FrozenSet

QUEUED = "queued"
VALIDATING = "validating"
RETRIEVING = "retrieving"
UPLOADING_SOURCE = "uploading_source"
ANALYZING = "analyzing"
TRANSCRIBING = "transcribing"
SELECTING = "selecting_highlights"
EXTRACTING = "extracting_clips"
RENDERING = "rendering"
UPLOADING_OUTPUTS = "uploading_outputs"
VALIDATING_OUTPUTS = "validating_outputs"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE: FrozenSet[str] = frozenset({
    VALIDATING, RETRIEVING, UPLOADING_SOURCE, ANALYZING, TRANSCRIBING, SELECTING,
    EXTRACTING, RENDERING, UPLOADING_OUTPUTS, VALIDATING_OUTPUTS,
})
TERMINAL: FrozenSet[str] = frozenset({COMPLETED, FAILED, CANCELLED})
ALL: FrozenSet[str] = ACTIVE | TERMINAL | {QUEUED}

# Pipeline order of active stages. Used for progress and recovery.
PIPELINE_ORDER = (
    VALIDATING, RETRIEVING, UPLOADING_SOURCE, ANALYZING, TRANSCRIBING, SELECTING,
    EXTRACTING, RENDERING, UPLOADING_OUTPUTS, VALIDATING_OUTPUTS,
)

_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    QUEUED: frozenset({VALIDATING, FAILED, CANCELLED}),
    VALIDATING: frozenset({RETRIEVING, UPLOADING_SOURCE, ANALYZING, FAILED, CANCELLED, QUEUED}),
    RETRIEVING: frozenset({UPLOADING_SOURCE, VALIDATING, FAILED, CANCELLED, QUEUED}),
    UPLOADING_SOURCE: frozenset({ANALYZING, FAILED, CANCELLED, QUEUED}),
    ANALYZING: frozenset({TRANSCRIBING, FAILED, CANCELLED, QUEUED}),
    TRANSCRIBING: frozenset({SELECTING, FAILED, CANCELLED, QUEUED}),
    SELECTING: frozenset({EXTRACTING, FAILED, CANCELLED, QUEUED}),
    EXTRACTING: frozenset({RENDERING, FAILED, CANCELLED, QUEUED}),
    RENDERING: frozenset({UPLOADING_OUTPUTS, FAILED, CANCELLED, QUEUED}),
    UPLOADING_OUTPUTS: frozenset({VALIDATING_OUTPUTS, FAILED, CANCELLED, QUEUED}),
    VALIDATING_OUTPUTS: frozenset({COMPLETED, FAILED, CANCELLED, QUEUED}),
    COMPLETED: frozenset(),
    FAILED: frozenset({QUEUED}),      # explicit retry only
    CANCELLED: frozenset(),
}

# Stage name -> progress percentage at the start of that stage.
STAGE_PROGRESS: Dict[str, int] = {
    QUEUED: 0, VALIDATING: 3, RETRIEVING: 8, UPLOADING_SOURCE: 15, ANALYZING: 20,
    TRANSCRIBING: 35, SELECTING: 50, EXTRACTING: 58, RENDERING: 65,
    UPLOADING_OUTPUTS: 92, VALIDATING_OUTPUTS: 96, COMPLETED: 100, FAILED: 0, CANCELLED: 0,
}


def allowed(from_status: str, to_status: str) -> bool:
    return to_status in _TRANSITIONS.get(from_status, frozenset())
