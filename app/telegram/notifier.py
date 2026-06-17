"""Async Telegram notifier — sends detailed application updates and daily summaries.

Uses aiohttp for async HTTP calls instead of blocking on python-telegram-bot's sync API.
Message templates match spec Section 6 exactly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp

logger = logging.getLogger("job_automation_bot")

_API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    """Sends detailed Telegram notifications via async HTTP calls.

    Uses the Telegram Bot API directly via aiohttp to avoid blocking the event loop.
    """

    NOTIFY_EVENTS = {"applied", "error", "question_needed", "cycle_summary", "daily_summary", "failure"}

    def __init__(self, token: str = "", chat_id: str = "") -> None:
        self._token = token
        self._chat_id = chat_id
        self._available = bool(token and chat_id)

        if not self._available:
            logger.warning("Telegram notifier not configured — notifications disabled")

    # ── Core async send ──────────────────────────────────────

    async def send_message(self, text: str) -> bool:
        """Send a message via async HTTP using HTML parse mode (more forgiving than Markdown)."""
        if not self._available:
            return False
        try:
            url = _API_BASE.format(token=self._token, method="sendMessage")
            # Convert Markdown-style bold (**text**) to HTML <b>text</b>
            import re
            html_text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            # Escape HTML special characters in the rest
            html_text = html_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            # Restore the <b> tags we just created
            html_text = html_text.replace('&lt;b&gt;', '<b>').replace('&lt;/b&gt;', '</b>')
            payload = {
                "chat_id": self._chat_id,
                "text": html_text,
                "parse_mode": "HTML",
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        if "chat not found" not in body:
                            logger.warning("Telegram API returned %d: %s", resp.status, body[:200])
                    return resp.status == 200
        except Exception:
            logger.exception("Telegram send failed")
            return False

    # ── File upload ───────────────────────────────────────

    async def send_document(self, file_path: str, caption: str = "") -> bool:
        """Send a file as a Telegram document attachment."""
        if not self._available:
            return False
        from pathlib import Path

        path = Path(file_path)
        if not path.exists():
            await self.send_message(
                f"⚠️ Resume file not found: {file_path}"
            )
            return False

        try:
            url = _API_BASE.format(token=self._token, method="sendDocument")
            async with aiohttp.ClientSession() as session:
                with open(path, "rb") as f:
                    data = aiohttp.FormData()
                    data.add_field("chat_id", self._chat_id)
                    data.add_field("document", f, filename=path.name)
                    if caption:
                        data.add_field("caption", caption)
                    async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            logger.warning("Telegram sendDocument returned %d: %s", resp.status, body[:200])
                        return resp.status == 200
        except Exception:
            logger.exception("Telegram sendDocument failed")
            return False

    # ── Event templates (spec Section 6) ─────────────────────

    async def cycle_started(self, platform_count: int) -> bool:
        return False

    async def jobs_found(self, new_count: int, total: int) -> bool:
        return False

    async def job_processing(self, i: int, total: int, title: str, company: str,
                             location: str, remote_type: str, salary: str,
                             apply_url: str) -> bool:
        return False

    async def tailoring(self, matched_count: int, jd_keyword_count: int) -> bool:
        return False

    async def applying(self, method: str) -> bool:
        return False

    async def success(self, title: str, company: str, resume_path: str = "", cover_letter_path: str = "") -> bool:
        now = datetime.now().strftime("%H:%M")
        if resume_path:
            await self.send_document(resume_path, caption=f"✅ Applied: {title} @ {company}")
        if cover_letter_path:
            await self.send_document(cover_letter_path, caption=f"📝 Cover letter: {title} @ {company}")
        return await self.send_message(
            f"✅ *Applied successfully!*\n"
            f"💼 {title} @ {company}\n🕐 {now}\n📄 Resume: tailored\n📝 Cover letter: generated"
        )

    async def failure(self, title: str, company: str, error_message: str, apply_url: str) -> bool:
        return await self.send_message(
            f"❌ *Application failed*\n"
            f"💼 {title} @ {company}\nReason: {error_message}\nAction: Saved for manual review → {apply_url}"
        )

    async def cycle_summary(self, success_count: int, fail_count: int,
                            skip_count: int, next_run: str) -> bool:
        return await self.send_message(
            f"📊 *Cycle Complete*\n"
            f"✅ Applied: {success_count}\n❌ Failed: {fail_count}\n"
            f"⏭ Skipped (duplicate): {skip_count}\n⏰ Next run: {next_run}"
        )

    async def daily_summary(self, date_str: str, total_applications: int,
                            platforms: list[str], top_roles: list[str],
                            success_rate: float, all_time_total: int) -> bool:
        return await self.send_message(
            f"🗓 *Daily Report — {date_str}*\n"
            f"Total applications sent: {total_applications}\n"
            f"Platforms searched: {', '.join(platforms)}\n"
            f"Roles applied: {', '.join(top_roles[:3])}\n"
            f"Success rate: {success_rate:.0%}\n"
            f"Total in database: {all_time_total}\nKeep going 💪"
        )
