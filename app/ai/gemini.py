"""Google Gemini provider (text analysis of timestamped transcript chunks).

Only transcript text and timing are sent. No video or audio bytes leave the
server through this provider.
"""
from __future__ import annotations

import logging
from typing import Tuple

from ..config import settings
from .provider import ModelUsage, ProviderError

logger = logging.getLogger("studio.ai.gemini")


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str = "", model: str = "", timeout_seconds: int = 0):
        from google import genai
        from google.genai import types

        self._types = types
        self.model = model or settings.GEMINI_MODEL
        self._timeout_ms = int(1000 * (timeout_seconds or settings.AI_TIMEOUT_SECONDS))
        self._client = genai.Client(api_key=api_key or settings.GEMINI_API_KEY)

    def generate_json(self, prompt: str) -> Tuple[str, ModelUsage]:
        from google.genai import errors as genai_errors

        types = self._types
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
            max_output_tokens=4096,
            http_options=types.HttpOptions(timeout=self._timeout_ms),
        )
        try:
            response = self._client.models.generate_content(model=self.model, contents=prompt, config=config)
        except genai_errors.APIError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            if code == 429:
                raise ProviderError("rate_limited", "The AI provider is rate-limiting requests.", True) from exc
            if code in (401, 403):
                raise ProviderError("auth_failed", "The AI provider rejected the server's credentials.", False) from exc
            if code >= 500:
                raise ProviderError("unavailable", "The AI provider is temporarily unavailable.", True) from exc
            raise ProviderError("provider_error", "The AI provider rejected the request.", False) from exc
        except (TimeoutError, OSError) as exc:
            raise ProviderError("timeout", "The AI provider did not respond in time.", True) from exc
        except Exception as exc:  # network / SDK failure
            name = type(exc).__name__.lower()
            retryable = "timeout" in name or "connect" in name
            raise ProviderError("timeout" if retryable else "unavailable",
                                "The AI provider could not be reached.", True) from exc

        text = getattr(response, "text", None)
        if not text:
            raise ProviderError("invalid_response", "The AI provider returned an empty response.", True)
        usage = getattr(response, "usage_metadata", None)
        return text, ModelUsage(
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
        )
