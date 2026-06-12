"""Telegram bot — interactive commands for the application tracker."""

from __future__ import annotations

import logging
from typing import Optional

from app.config.settings import Settings
from app.database.firestore_repository import FirestoreRepository
from app.models.application import Application, ApplicationStatus

logger = logging.getLogger("headhunter")

try:
    from telegram import Update
    from telegram.ext import Application as PTBApplication, CommandHandler, ContextTypes

    _HAS_TELEGRAM = True
except ImportError:
    _HAS_TELEGRAM = False
    # Placeholder types for static analysis.
    Update = object  # type: ignore[assignment]
    PTBApplication = object  # type: ignore[assignment]
    ContextTypes = object  # type: ignore[assignment]
    CommandHandler = object  # type: ignore[assignment]


class Bot:
    """Telegram bot that responds to user commands.

    Commands
    --------
    ``/stats`` — total applications, counts by status.
    ``/applications [limit]`` — list the most recent applications.
    ``/company <name>`` — show applications for a specific company.
    ``/interviews`` — list all applications with INTERVIEW status.

    The bot runs in **polling** mode via :meth:`run_polling`.
    """

    def __init__(
        self,
        repository: FirestoreRepository,
        settings: Settings | None = None,
        token: str | None = None,
    ) -> None:
        """Initialise the bot.

        Args:
            repository: A :class:`FirestoreRepository` instance for
                querying application data.
            settings: Optional :class:`Settings` instance.
            token: Override bot token.
        """
        self._repository = repository
        cfg = settings or Settings()
        self._token = token or cfg.telegram_bot_token or ""
        self._available = bool(self._token and _HAS_TELEGRAM)

        if not self._token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN not set — bot will not start."
            )
        elif not _HAS_TELEGRAM:
            logger.warning(
                "python-telegram-bot not installed — bot will not start."
            )

    # ── Public API ───────────────────────────────────────────

    def run_polling(self) -> None:
        """Start the bot in polling mode (blocking).

        Registers command handlers and begins polling for updates.
        The call does **not** return until the bot is stopped
        (e.g. via ``Ctrl+C``).
        """
        if not self._available:
            logger.error("Cannot start bot — check configuration.")
            return

        app = PTBApplication.builder().token(self._token).build()
        app.bot_data["repository"] = self._repository

        app.add_handler(CommandHandler("stats", self._cmd_stats))
        app.add_handler(CommandHandler("applications", self._cmd_applications))
        app.add_handler(CommandHandler("company", self._cmd_company))
        app.add_handler(CommandHandler("interviews", self._cmd_interviews))
        app.add_error_handler(self._on_error)

        logger.info("Telegram bot starting in polling mode")
        app.run_polling()

    # ── Command handlers ─────────────────────────────────────

    @staticmethod
    async def _cmd_stats(
        update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Show aggregate application statistics."""
        # context is used for type annotation consistency
        _ = context
        repo: FirestoreRepository = context.bot_data.get("repository")
        if not repo:
            await update.message.reply_text("❌ Repository not available.")
            return

        apps = repo.list_recent_applications(limit=1000)
        total = len(apps)
        counts: dict[str, int] = {}
        for a in apps:
            counts[a.status.name] = counts.get(a.status.name, 0) + 1

        lines = [f"📊 *Stats*\n  Total: {total}"]
        for status in ApplicationStatus:
            c = counts.get(status.name, 0)
            lines.append(f"  {status.name}: {c}")

        await update.message.reply_text("\n".join(lines))

    @staticmethod
    async def _cmd_applications(
        update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """List the most recent applications."""
        repo: FirestoreRepository = context.bot_data.get("repository")
        if not repo:
            await update.message.reply_text("❌ Repository not available.")
            return

        # Parse optional limit argument.
        limit = 5
        if context.args and context.args[0].isdigit():
            limit = min(int(context.args[0]), 20)

        apps = repo.list_recent_applications(limit=limit)
        if not apps:
            await update.message.reply_text("No applications found.")
            return

        lines = [f"📋 *Recent Applications (last {len(apps)})*"]
        for a in apps:
            score = f" ({a.match_score:.0%})" if a.match_score is not None else ""
            lines.append(
                f"  • *{a.company}* — {a.role}{score}\n"
                f"    Status: {a.status.name}  |  {a.applied_at.strftime('%b %d')}"
            )

        await update.message.reply_text("\n\n".join(lines))

    @staticmethod
    async def _cmd_company(
        update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Show applications for a specific company."""
        repo: FirestoreRepository = context.bot_data.get("repository")
        if not repo:
            await update.message.reply_text("❌ Repository not available.")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /company <name>\nExample: /company Google"
            )
            return

        company_query = " ".join(context.args).lower()
        all_apps = repo.list_recent_applications(limit=200)
        matches = [a for a in all_apps if company_query in a.company.lower()]

        if not matches:
            await update.message.reply_text(
                f"No applications found for \"{' '.join(context.args)}\"."
            )
            return

        lines = [f"🏢 *Applications matching \"{' '.join(context.args)}\"*"]
        for a in matches:
            score = f" ({a.match_score:.0%})" if a.match_score is not None else ""
            lines.append(
                f"  • *{a.role}*{score}\n"
                f"    Status: {a.status.name}  |  {a.applied_at.strftime('%b %d')}"
            )

        await update.message.reply_text("\n\n".join(lines))

    @staticmethod
    async def _cmd_interviews(
        update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Show applications with INTERVIEW status."""
        repo: FirestoreRepository = context.bot_data.get("repository")
        if not repo:
            await update.message.reply_text("❌ Repository not available.")
            return

        all_apps = repo.list_recent_applications(limit=200)
        interviews = [a for a in all_apps if a.status == ApplicationStatus.INTERVIEW]

        if not interviews:
            await update.message.reply_text(
                "🎉 No upcoming interviews scheduled."
            )
            return

        lines = [f"🎯 *Upcoming Interviews ({len(interviews)})*"]
        for a in interviews:
            lines.append(
                f"  • *{a.company}* — {a.role}\n"
                f"    {a.applied_at.strftime('%b %d')}"
            )

        await update.message.reply_text("\n\n".join(lines))

    # ── Error handler ────────────────────────────────────────

    @staticmethod
    async def _on_error(
        update: object, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Log errors without crashing the bot."""
        logger.error(
            "Telegram bot error: %s — update=%s",
            context.error,
            update,
        )
