"""Async OpenAI client wrapper using ``openai.AsyncOpenAI``."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config.settings import Settings
from app.utils.network import is_network_restricted_error, network_error_summary

logger = logging.getLogger("job_automation_bot")


class AIClient:
    """Async wrapper around OpenAI-compatible chat completions."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
    ) -> None:
        cfg = settings or Settings()
        self._api_key = cfg.openai_api_key
        self._model = cfg.openai_model
        self._temperature = cfg.openai_temperature
        self._max_tokens = cfg.openai_max_tokens
        self._key_invalid = False
        self._network_blocked = False
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
        return bool(self._api_key) and not self._key_invalid

    @property
    def network_blocked(self) -> bool:
        return self._network_blocked

    async def validate(self) -> bool:
        """Validate the configured API key with a minimal request."""
        if not self._api_key:
            return False

        try:
            await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "Reply with just OK"}],
                max_tokens=10,
            )
            logger.info("AI client validated - API key is working")
            return True
        except AuthenticationError as exc:
            logger.error(
                "AI API key rejected (401): %s - disabling AI features. "
                "Get a key at https://console.groq.com",
                exc,
            )
            self._key_invalid = True
            return False
        except Exception as exc:
            if is_network_restricted_error(exc):
                self._network_blocked = True
                logger.warning(
                    "AI client validation skipped due to blocked network access: %s "
                    "- disabling AI features for this run",
                    network_error_summary(exc),
                )
                self._key_invalid = True
                return True

            logger.warning("AI client validation failed: %s - AI features may not work", exc)
            return bool(self._api_key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=lambda rs: (
            rs.outcome is None
            or (
                rs.outcome.failed
                and not isinstance(rs.outcome.exception(), AuthenticationError)
                and isinstance(
                    rs.outcome.exception(),
                    (APITimeoutError, APIConnectionError, APIStatusError),
                )
            )
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
        """Send a chat completion request and return the response text."""
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
        """Send a chat completion request and parse the response as JSON."""
        text = await self.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        cleaned = text.strip()

        if cleaned.startswith("```"):
            first_nl = cleaned.find("\n")
            if first_nl != -1:
                cleaned = cleaned[first_nl + 1 :]
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
