"""Async scheduler — runs the pipeline on a recurring schedule.

Uses APScheduler's AsyncIOScheduler for async job execution.
- Main cycle: every N hours (configurable)
- Daily summary: every day at 08:00 Asia/Kolkata
- Health check: every 10 minutes

On startup, runs one immediate cycle before the scheduler takes over.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config.settings import Settings
from app.pipeline.orchestrator import Pipeline
from app.resume.models import ResumeProfile
from app.telegram.notifier import TelegramNotifier

logger = logging.getLogger("job_automation_bot")


class Scheduler:
    """24/7 async scheduler using APScheduler AsyncIOScheduler.

    Attributes:
        pipeline: The async Pipeline orchestrator.
        resume: Parsed ResumeProfile (loaded once at startup).
        providers: List of job provider instances.
        notifier: TelegramNotifier for notifications.
        settings: Application settings.
    """

    def __init__(
        self,
        pipeline: Pipeline,
        resume: ResumeProfile,
        providers: list,
        notifier: TelegramNotifier,
        settings: Settings,
    ) -> None:
        self._pipeline = pipeline
        self._resume = resume
        self._providers = providers
        self._notifier = notifier
        self._settings = settings
        self._scheduler = AsyncIOScheduler()

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        """Start the scheduler with all recurring jobs.

        Runs one immediate cycle on startup, then schedules recurring jobs.
        """
        # Run one cycle immediately so the user gets instant feedback
        asyncio.create_task(self._run_immediate_cycle())

        # Main cycle: every N hours
        self._scheduler.add_job(
            self._run_cycle,
            trigger=IntervalTrigger(hours=self._settings.run_interval_hours),
            id="main_cycle",
            name="Job search and apply cycle",
            misfire_grace_time=600,
        )

        # Daily summary at 8:00 AM IST (convert to UTC: 2:30 AM UTC)
        self._scheduler.add_job(
            self._send_daily_summary,
            trigger=CronTrigger(hour=2, minute=30, timezone="Asia/Kolkata"),
            id="daily_summary",
            name="Daily application summary",
        )

        # Health check every 10 minutes
        self._scheduler.add_job(
            self._health_check,
            trigger=IntervalTrigger(minutes=10),
            id="health_check",
            name="Scheduler health check",
        )

        self._scheduler.start()
        logger.info(
            "Scheduler started — cycle every %d hours, daily summary at 8 AM IST",
            self._settings.run_interval_hours,
        )

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        try:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
        except Exception:
            logger.debug("Scheduler was not running — ignoring")

    # ── Jobs ──────────────────────────────────────────────────

    async def _run_immediate_cycle(self) -> None:
        """Run one cycle immediately on startup."""
        logger.info("Running initial cycle immediately...")
        stats = await self._pipeline.run_cycle(self._resume, self._providers)
        logger.info("Initial cycle complete: %s", stats)

    async def _run_cycle(self) -> None:
        """Run the main application cycle."""
        logger.info("=== Cycle started ===")
        try:
            stats = await self._pipeline.run_cycle(self._resume, self._providers)
            logger.info("=== Cycle complete: %s ===", stats)
        except Exception:
            logger.exception("Pipeline cycle failed")
            await self._notifier.send_message(
                f"🚨 *CRITICAL ERROR*: Pipeline cycle failed. Check logs."
            )

    async def _send_daily_summary(self) -> None:
        """Send daily summary at 8:00 AM IST."""
        logger.info("Sending daily summary...")
        try:
            stats = self._pipeline._repository.get_stats()  # noqa: SLF001
            today = datetime.now(timezone.utc).strftime("%B %d, %Y")
            platform_names = [p.__class__.__name__ for p in self._providers]
            await self._notifier.daily_summary(
                date_str=today,
                total_applications=stats.get("successful", 0),
                platforms=platform_names,
                top_roles=["Software Engineer", "Frontend Developer", "Full Stack"],
                success_rate=stats.get("successful", 0) / max(stats.get("total_applied", 1), 1),
                all_time_total=stats.get("total_applied", 0),
            )
        except Exception:
            logger.exception("Daily summary failed")

    async def _health_check(self) -> None:
        """Check that the main cycle job is still scheduled."""
        job = self._scheduler.get_job("main_cycle")
        if job is None:
            logger.error("Main cycle job missing — restarting scheduler")
            self._scheduler.add_job(
                self._run_cycle,
                trigger=IntervalTrigger(hours=self._settings.run_interval_hours),
                id="main_cycle",
                name="Job search and apply cycle",
                misfire_grace_time=600,
            )
