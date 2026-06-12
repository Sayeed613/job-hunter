"""OpenAI-compatible API client for job matching."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.settings import Settings

logger = logging.getLogger("headhunter")

_DEFAULT_TIMEOUT: int = 60


class OpenCodeClient:
    """Client for an OpenAI-compatible chat-completion API.

    Configuration is loaded from :class:`app.config.settings.Settings`,
    which reads the following environment variables:

    - ``OPENCODE_API_KEY`` — API key (required).
    - ``OPENCODE_BASE_URL`` — Base URL (default: ``https://api.openai.com/v1``).
    - ``OPENCODE_MODEL`` — Model identifier (default: ``gpt-4o-mini``).

    Every value can also be overridden via the constructor.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise the client.

        Args:
            settings: Optional :class:`Settings` instance.  When provided
                its ``opencode_*`` fields are used as fallbacks.
            api_key: Override API key.
            model: Override model identifier.
            base_url: Override API base URL.
            timeout: HTTP request timeout in seconds (default 60).
        """
        cfg = settings or Settings()

        self._api_key = api_key or cfg.opencode_api_key or ""
        self._model = model or cfg.opencode_model or "gpt-4o-mini"
        self._base_url = (
            base_url or cfg.opencode_base_url or "https://api.openai.com/v1"
        ).rstrip("/")
        self._timeout = timeout

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

        logger.info(
            "OpenCodeClient initialised",
            extra={
                "model": self._model,
                "base_url": self._base_url,
                "api_key_set": bool(self._api_key),
            },
        )

    # ── Public API ───────────────────────────────────────────

    def chat(self, prompt: str, **kwargs: Any) -> str:
        """Send a chat prompt to the LLM and return the response text.

        Args:
            prompt: The user message / instruction to send.
            **kwargs: Additional parameters forwarded to the request body
                (e.g. ``temperature=0.3``, ``max_tokens=2000``).

        Returns:
            The response content as a plain string.

        Raises:
            ValueError: If no API key is configured.
            requests.RequestException: On HTTP or network failure after
                all retries are exhausted.
        """
        self._require_key()
        body = self._build_body(prompt, **kwargs)
        data = self._post(body)
        return self._extract_text(data)

    def chat_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """Send a chat prompt and parse the response as JSON.

        This is a convenience wrapper around :meth:`chat` that
        additionally runs the response through :func:`json.loads`.

        Args:
            prompt: The user message / instruction to send.
            **kwargs: Additional parameters forwarded to the request body.

        Returns:
            The parsed JSON dict.

        Raises:
            ValueError: If the response is not valid JSON.
        """
        text = self.chat(prompt, **kwargs)
        cleaned = text.strip()

        # Strip markdown code fences if present.
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
            logger.error(
                "Failed to parse JSON from LLM response: %s", exc,
            )
            raise ValueError(
                f"LLM response is not valid JSON: {exc}\n\n"
                f"Raw response:\n{text}"
            ) from exc

    # ── Internal helpers ─────────────────────────────────────

    def _require_key(self) -> None:
        if not self._api_key:
            raise ValueError(
                "OpenCode API key is not configured. Set the "
                "OPENCODE_API_KEY environment variable or pass "
                "``api_key`` to the constructor."
            )

    def _build_body(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        body.update(kwargs)
        return body

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout),
        ),
        reraise=True,
    )
    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        response = self._session.post(url, json=body, timeout=self._timeout)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected API response structure: %s", exc)
            raise ValueError(
                "Failed to parse LLM response. Check that the API "
                "endpoint returns a standard chat completions format."
            ) from exc
