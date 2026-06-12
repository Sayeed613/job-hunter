"""Async OpenAI client wrapper using openai.AsyncOpenAI.

Supports both direct OpenAI and OpenRouter (via custom base_url).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from openai import APITimeoutError, APIStatusError, APIConnectionError

from app.config.settings import Settings

logger = logging.getLogger("job_automation_bot")


class AIClient:
    """Async wrapper around openai.AsyncOpenAI for chat completions.

    Reads configuration from Settings (which loads from .env).
    Supports OpenRouter: set OPENAI_BASE_URL=https://openrouter.ai/api/v1
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
    ) -> None:
        cfg = settings or Settings()
        self._api_key = cfg.openai_api_key
        self._model = cfg.openai_model
        self._temperature = cfg.openai_temperature
        self._max_tokens = cfg.openai_max_tokens
        base_url = cfg.openai_base_url.rstrip("/")

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=base_url,
        )

        logger.info(
            "AIClient initialised",
            extra={
                "model": self._model,
                "base_url": base_url,
                "api_key_set": bool(self._api_key),
            },
        )

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    # ── Public API ───────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(
            (APITimeoutError, APIConnectionError, APIStatusError),
        ),
        reraise=True,
    )
    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Send a chat completion request and return the response text.

        Args:
            system_prompt: System-level instruction.
            user_prompt: User message content.
            temperature: Override temperature (default from settings).
            max_tokens: Override max tokens (default from settings).

        Returns:
            The response content as a plain string.
        """
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature if temperature is not None else self._temperature,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
        )
        return response.choices[0].message.content or ""

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        """Send a chat completion and parse the response as JSON.

        Returns:
            Parsed JSON dictionary.
        """
        text = await self.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        cleaned = text.strip()
        # Strip markdown code fences if present.
        if cleaned.startswith("```"):
            first_nl = cleaned.find("\n")
            if first_nl != -1:
                cleaned = cleaned[first_nl + 1:]
            else:
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
            elif "```" in cleaned:
                cleaned = cleaned[: cleaned.rindex("```")].strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse JSON from LLM response: %s", exc)
            raise ValueError(
                f"LLM response is not valid JSON: {exc}\n\nRaw response:\n{text}"
            ) from exc
