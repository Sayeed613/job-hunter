"""WhatsApp notifier with local fallback.

Uses Twilio WhatsApp when configured. If Twilio is unavailable or a send
fails, notifications fall back to the local notifier for the rest of the
current cycle.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.config.settings import Settings
from app.notifier.local_notifier import LocalNotifier
from app.utils.network import is_network_restricted_error

logger = logging.getLogger("job_automation_bot")

try:
    from twilio.rest import Client as TwilioClient

    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False
    logger.warning("twilio package not installed - WhatsApp disabled")


class WhatsAppNotifier:
    """Send notifications via WhatsApp with local file fallback."""

    def __init__(self) -> None:
        self._settings = Settings()
        self._fallback = LocalNotifier()
        self._available = False
        self._client: Optional[TwilioClient] = None
        self._from_number = ""
        self._to_number = ""
        self._failed_once = False

        if not TWILIO_AVAILABLE:
            logger.info("WhatsApp not available - using local file logging")
            return

        sid = self._settings.twilio_account_sid
        token = self._settings.twilio_auth_token
        from_num = self._settings.twilio_whatsapp_number
        to_num = self._settings.whatsapp_number

        if not all([sid, token, from_num, to_num]):
            logger.info(
                "WhatsApp not configured - set TWILIO_ACCOUNT_SID, "
                "TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER, and WHATSAPP_NUMBER"
            )
            return

        try:
            self._client = TwilioClient(sid, token)
            self._from_number = f"whatsapp:{from_num}"
            self._to_number = f"whatsapp:{to_num}"
            self._available = True
            logger.info("WhatsApp notifier active")
        except Exception as exc:
            logger.warning(
                "Failed to initialise Twilio client: %s - using local file logging",
                exc,
            )

    @staticmethod
    def _twilio_error_hint(error: Exception) -> str:
        """Return a targeted hint for common Twilio WhatsApp errors."""
        code = getattr(error, "code", None)
        if code == 63007:
            return (
                "Twilio could not find a WhatsApp channel for the configured sender. "
                "Check that this account owns the sender, the WhatsApp sandbox or "
                "production sender is enabled, and TWILIO_WHATSAPP_NUMBER matches it."
            )
        if code == 63016:
            return (
                "The destination number has not joined the Twilio WhatsApp sandbox yet. "
                "Send the sandbox join code to the Twilio sandbox number first."
            )
        if is_network_restricted_error(error):
            return (
                "Outbound network access is blocked in this environment, so WhatsApp "
                "delivery is unavailable for this run."
            )
        return (
            "Verify the Twilio account credentials, sender number, and recipient "
            "number configured in .env."
        )

    async def send_message(self, text: str) -> bool:
        """Send a WhatsApp message or fall back to the local notifier."""
        if not self._available or not self._client or self._failed_once:
            return await self._fallback.send_message(text)

        try:
            import asyncio

            def _send() -> None:
                self._client.messages.create(
                    body=text,
                    from_=self._from_number,
                    to=self._to_number,
                )

            await asyncio.to_thread(_send)
            return True
        except Exception as exc:
            logger.warning(
                "WhatsApp send failed: %s - disabling WhatsApp for this cycle. %s",
                exc,
                self._twilio_error_hint(exc),
            )
            self._failed_once = True
            return await self._fallback.send_message(text)

    async def send_document(self, file_path: str, caption: str = "") -> bool:
        """Log saved files locally and optionally mention them over WhatsApp."""
        msg = f"File saved: {file_path}"
        if caption:
            msg = f"{caption} - saved at {file_path}"
        if self._available and not self._failed_once:
            await self.send_message(msg)
        return await self._fallback.send_document(file_path, caption)

    async def cycle_started(self, platform_count: int) -> bool:
        return await self.send_message(
            f"Job Bot cycle started with {platform_count} providers"
        )

    async def jobs_found(self, new_count: int, total: int) -> bool:
        return await self.send_message(
            f"{new_count} new jobs found after filtering from {total} total"
        )

    async def job_processing(
        self,
        i: int,
        total: int,
        title: str,
        company: str,
        location: str,
        remote_type: str,
        salary: str,
        apply_url: str,
    ) -> bool:
        _ = apply_url
        parts = [f"[{i}/{total}] {title} @ {company}"]
        if location:
            parts.append(location)
        if remote_type:
            parts.append(remote_type)
        if salary:
            parts.append(salary)
        return await self.send_message(" | ".join(parts))

    async def tailoring(self, matched_count: int, jd_keyword_count: int) -> bool:
        return await self.send_message(
            f"AI tailoring matched {matched_count}/{jd_keyword_count} skills"
        )

    async def applying(self, method: str) -> bool:
        return await self.send_message(f"Applying via {method}")

    async def success(
        self,
        title: str,
        company: str,
        resume_path: str = "",
        cover_letter_path: str = "",
        salary: str = "",
        location: str = "",
    ) -> bool:
        parts = [f"Applied: {title} @ {company}"]
        if location:
            parts.append(location)
        if salary:
            parts.append(salary)
        if resume_path:
            parts.append(f"Resume: {Path(resume_path).name}")

        sent = await self.send_message("\n".join(parts))

        if resume_path:
            await self._fallback.send_document(
                resume_path,
                f"Resume for {title} @ {company}",
            )
        if cover_letter_path:
            await self._fallback.send_document(
                cover_letter_path,
                f"Cover letter for {title} @ {company}",
            )
        return sent

    async def failure(
        self,
        title: str,
        company: str,
        error_message: str,
        apply_url: str,
    ) -> bool:
        _ = apply_url
        return await self.send_message(
            f"Failed: {title} @ {company}\nReason: {error_message}"
        )

    async def cycle_summary(
        self,
        success_count: int,
        fail_count: int,
        skip_count: int,
        next_run: str,
    ) -> bool:
        return await self.send_message(
            "Cycle complete\n"
            f"Applied: {success_count}\n"
            f"Failed: {fail_count}\n"
            f"Skipped: {skip_count}\n"
            f"Next: {next_run}"
        )

    async def daily_summary(
        self,
        date_str: str,
        total_applications: int,
        platforms: list[str],
        top_roles: list[str],
        success_rate: float,
        all_time_total: int,
    ) -> bool:
        return await self.send_message(
            f"Daily report - {date_str}\n"
            f"Total: {total_applications}\n"
            f"Platforms: {', '.join(platforms[:3])}\n"
            f"Top roles: {', '.join(top_roles[:3])}\n"
            f"Rate: {success_rate:.0%}\n"
            f"All-time: {all_time_total}"
        )
