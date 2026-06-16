"""Telegram interaction service — sends questions and waits for user replies.

Uses the Telegram Bot API's `getUpdates` long-polling method so the bot
can send a multiple-choice question to the user and wait for their reply
without requiring a webhook or the python-telegram-bot library.

Usage:
    async with TelegramInteraction(token, chat_id) as tg:
        ok = await tg.send_question("Gender?", ["Male", "Female"])
        if ok:
            reply = await tg.poll_for_reply(timeout=120)
            if reply:
                print(f"User chose: {reply}")
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger("job_automation_bot")

_API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramInteraction:
    """Send questions and wait for user replies via Telegram.

    Attributes:
        available: True if token and chat_id are configured.
    """

    def __init__(self, token: str = "", chat_id: str = "") -> None:
        self._token = token
        self._chat_id = str(chat_id) if chat_id else ""
        self.available = bool(token and chat_id)
        self._last_update_id = 0
        self._session: Optional[aiohttp.ClientSession] = None

    # ── Public API ───────────────────────────────────────────

    async def send_question(
        self,
        question: str,
        options: list[str],
    ) -> bool:
        """Send a multiple-choice question to the user.

        Args:
            question: The question text (e.g. "What is your gender?").
            options: List of option texts.

        Returns:
            True if the message was sent successfully.
        """
        if not self.available:
            return False

        lines = [f"🤔 *{question}*", ""]
        for i, opt in enumerate(options, 1):
            lines.append(f"{i}. {opt}")
        lines.extend([
            "",
            "_Reply with the number or the option text_",
        ])

        return await self._send_message("\n".join(lines))

    async def send_simple(self, text: str) -> bool:
        """Send a simple text message (no options)."""
        return await self._send_message(text)

    async def poll_for_reply(
        self,
        timeout: int = 300,
        interval: float = 5.0,
    ) -> Optional[str]:
        """Poll Telegram for the user's reply to the last question.

        Args:
            timeout: Maximum seconds to wait for a reply (default 5 min).
            interval: Seconds between poll requests.

        Returns:
            The reply text, or None if timed out.
        """
        if not self.available:
            return None

        start = time.time()
        while time.time() - start < timeout:
            try:
                reply = await self._fetch_new_message()
                if reply is not None:
                    return reply
            except Exception as e:
                logger.debug("Telegram poll error: %s", e)

            await asyncio.sleep(interval)

        logger.info("Telegram poll timed out after %ds", timeout)
        return None

    async def ask(
        self,
        question: str,
        options: list[str],
        timeout: int = 300,
    ) -> Optional[str]:
        """Convenience: send question + poll for reply.

        Args:
            question: Question text.
            options: List of option texts.
            timeout: Max seconds to wait.

        Returns:
            The reply text, or None if timed out / unavailable.
        """
        if not self.available:
            return None
        ok = await self.send_question(question, options)
        if not ok:
            return None
        return await self.poll_for_reply(timeout=timeout)

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "TelegramInteraction":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ── Internal ─────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _send_message(self, text: str) -> bool:
        """Send a Telegram message."""
        session = await self._get_session()
        url = _API_BASE.format(token=self._token, method="sendMessage")
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug("Telegram send failed: %s", e)
            return False

    async def _fetch_new_message(self) -> Optional[str]:
        """Fetch the latest message from the user using getUpdates."""
        session = await self._get_session()
        url = _API_BASE.format(token=self._token, method="getUpdates")
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 5,
            "allowed_updates": ["message"],
        }
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except Exception as e:
            logger.debug("getUpdates failed: %s", e)
            return None

        if not data.get("ok"):
            return None

        for update in data.get("result", []):
            update_id = update.get("update_id", 0)
            if update_id > self._last_update_id:
                self._last_update_id = update_id

            message = update.get("message", {})
            chat = message.get("chat", {})
            if str(chat.get("id")) != self._chat_id:
                continue

            text = message.get("text", "").strip()
            if text:
                return text

        return None
