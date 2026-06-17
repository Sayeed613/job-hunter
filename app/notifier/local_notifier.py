"""Local file-based notifier — writes all notifications to a log file.

Replaces TelegramNotifier when Telegram is banned in the user's region.
All notifications are written to storage/notifications.log and also
logged via Python's standard logging for terminal visibility.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("job_automation_bot")


class LocalNotifier:
    """Writes application notifications to a local log file.

    Same interface as TelegramNotifier so it's a drop-in replacement.
    Every method logs to Python's logger AND writes to storage/notifications.log.
    """

    NOTIFY_EVENTS = {"applied", "error", "question_needed", "cycle_summary", "daily_summary", "failure"}

    def __init__(self) -> None:
        self._available = True
        self._log_path = Path("storage/notifications.log")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Local notifier active — notifications written to %s", self._log_path)

    # ── Core write ───────────────────────────────────────────

    def _write(self, event_type: str, message: str) -> bool:
        """Write a notification entry to the log file and Python logger."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] [{event_type}] {message}"

        # Log to Python logger (visible in terminal)
        logger.info("[NOTIFY] %s", message)

        # Write to log file
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except OSError as e:
            logger.warning("Failed to write notification: %s", e)
            return False

        return True

    # ── Public API (mirrors TelegramNotifier) ─────────────────

    async def send_message(self, text: str) -> bool:
        """Log a generic notification message."""
        return self._write("info", text)

    async def send_document(self, file_path: str, caption: str = "") -> bool:
        """Log that a document was saved (no actual sending needed)."""
        path = Path(file_path)
        if not path.exists():
            return self._write("warning", f"⚠️ File not found: {file_path}")
        msg = f"📄 {caption} — saved at {file_path}" if caption else f"📄 File saved: {file_path}"
        return self._write("file", msg)

    # ── Event templates ─────────────────────────────────────

    async def cycle_started(self, platform_count: int) -> bool:
        return self._write("cycle", f"🔄 Cycle started — {platform_count} providers")

    async def jobs_found(self, new_count: int, total: int) -> bool:
        return self._write("jobs", f"📊 {new_count} new jobs (filtered from {total} total)")

    async def job_processing(self, i: int, total: int, title: str, company: str,
                             location: str, remote_type: str, salary: str,
                             apply_url: str) -> bool:
        parts = [f"💼 [{i}/{total}] {title} @ {company}"]
        if location: parts.append(f"📍 {location}")
        if remote_type: parts.append(f"🏠 {remote_type}")
        if salary: parts.append(f"💰 {salary}")
        return self._write("processing", " | ".join(parts))

    async def tailoring(self, matched_count: int, jd_keyword_count: int) -> bool:
        return self._write("tailor", f"🔧 AI tailoring — matched {matched_count}/{jd_keyword_count} skills")

    async def applying(self, method: str) -> bool:
        return self._write("apply", f"🚀 Applying via {method}...")

    async def success(self, title: str, company: str, resume_path: str = "", cover_letter_path: str = "", salary: str = "", location: str = "") -> bool:
        lines = [f"✅ Applied: {title} @ {company}"]
        if location:
            lines.append(f"   📍 {location}")
        if salary:
            lines.append(f"   💰 {salary}")
        if resume_path:
            lines.append(f"   📄 Resume: {Path(resume_path).name}")
        if cover_letter_path:
            lines.append(f"   📝 Cover letter: {cover_letter_path}")
        return self._write("success", "\n".join(lines))

    async def failure(self, title: str, company: str, error_message: str, apply_url: str) -> bool:
        return self._write("failure",
            f"❌ Failed: {title} @ {company}\n   Reason: {error_message}\n   URL: {apply_url}")

    async def cycle_summary(self, success_count: int, fail_count: int,
                            skip_count: int, next_run: str) -> bool:
        return self._write("summary",
            f"📊 Cycle Complete ✅ {success_count} | ❌ {fail_count} | ⏭ {skip_count} skipped | ⏰ Next: {next_run}")

    async def daily_summary(self, date_str: str, total_applications: int,
                            platforms: list[str], top_roles: list[str],
                            success_rate: float, all_time_total: int) -> bool:
        return self._write("daily",
            f"📅 Daily Report — {date_str}\n"
            f"   Total applications: {total_applications}\n"
            f"   Platforms: {', '.join(platforms)}\n"
            f"   Top roles: {', '.join(top_roles[:3])}\n"
            f"   Success rate: {success_rate:.0%}\n"
            f"   All-time total: {all_time_total}")
