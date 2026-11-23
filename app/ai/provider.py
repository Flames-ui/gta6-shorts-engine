"""Highlight-analysis provider abstraction.

A provider turns a prompt into text plus token usage. Everything model-specific
(SDK, auth, retries, error mapping) lives behind this interface, so the
pipeline never depends on one vendor. Responses are always validated before use.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Tuple


class ProviderError(Exception):
    """A provider call failed. `code` is stable; `retryable` decides whether to retry."""

    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class HighlightProvider(Protocol):
    name: str
    model: str

    def generate_json(self, prompt: str) -> Tuple[str, ModelUsage]:
        """Return the raw JSON text produced for `prompt`. Raise ProviderError on failure."""
        ...
