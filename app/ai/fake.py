"""Deterministic provider for tests and local development only (rejected in production by config)."""
from __future__ import annotations

import json
from typing import Callable, List, Tuple, Union

from .provider import ModelUsage, ProviderError


class ScriptedProvider:
    """Returns scripted responses in order. Each item is a JSON-able object, a raw string, or a ProviderError."""

    name = "fake"
    model = "scripted"

    def __init__(self, responses: Union[List, Callable[[str], object]]):
        self._responses = list(responses) if isinstance(responses, list) else None
        self._fn = responses if callable(responses) else None
        self.calls: List[str] = []

    def generate_json(self, prompt: str) -> Tuple[str, ModelUsage]:
        self.calls.append(prompt)
        item = self._fn(prompt) if self._fn else self._responses.pop(0)
        if isinstance(item, ProviderError):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return text, ModelUsage(input_tokens=len(prompt) // 4, output_tokens=len(text) // 4)
