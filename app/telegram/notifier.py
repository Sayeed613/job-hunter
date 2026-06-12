"""Telegram notifier — sends application and status updates."""

from __future__ import annotations

import logging

from app.config.settings import Settings

logger = logging.getLogger("headhunter")

try:
    from telegram import Bot as TelegramBot
    from telegram.error import TelegramError

    _HAS_TELEGRAM = True
except ImportError:
    _HAS_TELEGRAM = False
    TelegramBot = None  # type: ignore[assignment]
    TelegramError = Exception  # type: ignore[assignment]


class Notifier:
    """Sends push notifications to a Telegram chat.

    Reads the bot token and target chat ID from :class:`Settings`
    (``telegram_bot_token``, ``telegram_chat_id``).
    All send methods are **non-blocking** — errors are logged but
    not propagated to the caller.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        """Initialise the notifier.

        Args:
            settings: Optional :class:`Settings` instance.
            token: Override bot token.
            chat_id: Override target chat ID.
        """
        cfg = settings or Settings()
        self._token = token or cfg.telegram_bot_token or ""
        self._chat_id = chat_id or cfg.telegram_chat_id or ""
        self._available = bool(self._token and self._chat_id and _HAS_TELEGRAM)

        if self._available:
            self._bot = TelegramBot(token=self._token)
        else:
            self._bot = None

        if not self._token or not self._chat_id:
            logger.warning(
                "Telegram notifier: TELEGRAM_BOT_TOKEN or "
                "TELEGRAM_CHAT_ID not set — notifications disabled."
            )
        elif not _HAS_TELEGRAM:
            logger.warning(
                "python-telegram-bot not installed — notifications disabled."
            )

    # ── Public API ───────────────────────────────────────────

    def send_message(self, text: str) -> bool:
        """Send a plain-text message to the configured chat.

        Args:
            text: The message body.

        Returns:
            ``True`` if the message was sent successfully, ``False``
            otherwise (or if not configured).
        """
        if not self._available or not self._bot:
            return False
        try:
            self._bot.send_message(
                chat_id=self._chat_id, text=text, parse_mode="Markdown",
            )
            logger.info("Telegram message sent", extra={"length": len(text)})
            return True
        except TelegramError:
            logger.exception("Failed to send Telegram message")
            return False

    def send_application_update(
        self,
        company: str,
        role: str,
        status: str,
        match_score: float | None = None,
        job_url: str = "",
    ) -> bool:
        """Send a structured application update notification.

        Args:
            company: Company name.
            role: Job title.
            status: Application status (e.g. ``NEW``, ``APPLIED``).
            match_score: Optional ATS / AI match score.
            job_url: Optional link to the job posting.

        Returns:
            ``True`` if sent successfully.
        """
        score_line = (
            f"  Score: {match_score:.0%}\n" if match_score is not None else ""
        )
        link_line = f"  Link: {job_url}\n" if job_url else ""

        text = (
            f"📋 *Application Update*\n"
            f"  Company: {company}\n"
            f"  Role: {role}\n"
            f"  Status: {status}\n"
            f"{score_line}{link_line}"
        )
        return self.send_message(text)

    def send_collection_summary(
        self,
        total_found: int,
        new_jobs: int,
        duplicates: int,
    ) -> bool:
        """Send a job-collection cycle summary.

        Args:
            total_found: Number of raw job listings fetched.
            new_jobs: Number of new jobs saved.
            duplicates: Number of duplicates skipped.

        Returns:
            ``True`` if sent successfully.
        """
        text = (
            f"🔄 *Collection Cycle*\n"
            f"  Found: {total_found}\n"
            f"  New: {new_jobs}\n"
            f"  Duplicates: {duplicates}\n"
        )
        return self.send_message(text)
