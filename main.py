"""Application entrypoint for Project Headhunter.

Wires all services together and starts the APScheduler-based
collection & processing cycle.

Usage::

    python main.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure the project root is on ``sys.path`` so that ``app`` can be
# imported regardless of the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config.settings import Settings
from app.utils.logger import setup_logger

# ── Late imports (may depend on logging being set up first) ──


def main() -> None:
    """Initialise every service and start the recurring scheduler."""
    settings = Settings()
    logger = setup_logger(settings.log_level)

    logger.info(
        "Project Headhunter starting",
        extra={
            "app_name": settings.app_name,
            "environment": settings.environment,
            "log_level": settings.log_level,
        },
    )

    # ── Firebase init (graceful) ────────────────────────────
    from app.database import (
        FirestoreRepository,
        initialize as init_firebase,
        is_initialized,
    )

    init_firebase(settings)
    if is_initialized():
        repository = FirestoreRepository()
        logger.info("Firestore repository initialised")
    else:
        logger.warning(
            "Firebase not available — application persistence and "
            "Telegram bot features will be disabled."
        )
        repository = FirestoreRepository()  # will fail at first write

    # ── Load resume ─────────────────────────────────────────
    from app.resume.service import ResumeService

    resume_path = Path("Sayeed_Frontend_Developer.docx")
    if not resume_path.exists():
        logger.error("Resume file not found at %s — aborting", resume_path.resolve())
        sys.exit(1)

    resume_service = ResumeService(resume_path)
    resume = resume_service.load_resume()
    logger.info(
        "Resume loaded",
            extra={
                "candidate": resume.name,
                "skills": len(resume.skills),
                "projects": len(resume.projects),
            },
        )

    # ── AI client ───────────────────────────────────────────
    from app.ai.opencode_client import OpenCodeClient

    client = OpenCodeClient(settings=settings)
    if not client._api_key:  # noqa: SLF001
        logger.warning(
            "OPENCODE_API_KEY not set — AI matching and cover letter "
            "generation will fail."
        )

    # ── Core services ───────────────────────────────────────
    from app.ai.job_matcher import JobMatcher
    from app.ai.recommendation_engine import RecommendationEngine
    from app.ats.ats_scorer import AtsScorer
    from app.cover_letter.generator import CoverLetterGenerator
    from app.tailor.resume_generator import ResumeGenerator
    from app.tailor.resume_tailor import ResumeTailor

    ats_scorer = AtsScorer()
    job_matcher = JobMatcher(client=client)
    recommendation_engine = RecommendationEngine()
    resume_tailor = ResumeTailor()
    resume_generator = ResumeGenerator()
    cover_gen = CoverLetterGenerator(client=client)

    # ── Telegram notifier (optional) ────────────────────────
    from app.telegram.notifier import Notifier

    notifier = Notifier(settings=settings)
    if not notifier._available:  # noqa: SLF001
        logger.info("Telegram notifier not available — notifications disabled")

    # ── GitHub service (optional) ───────────────────────────
    from app.github.github_service import GithubService

    github_service: GithubService | None = None
    if settings.github_token:
        github_service = GithubService()
        logger.info("GitHub service available (token configured)")
    else:
        logger.info("No GITHUB_TOKEN set — GitHub analysis disabled")

    # ── Portfolio service (optional) ────────────────────────
    from app.portfolio.portfolio_service import PortfolioService

    portfolio_service = PortfolioService()
    # Portfolio requires explicit load_portfolio() call — not loaded here

    # ── Job applier (optional) ───────────────────────────
    from app.jobs.applier import JobApplier

    job_applier: JobApplier | None = None
    if settings.auto_apply_enabled:
        job_applier = JobApplier()
        logger.info("Job applier initialised (auto-apply enabled)")
    else:
        logger.info("Auto-apply disabled by configuration")

    # ── Application pipeline ────────────────────────────────
    from app.pipeline.application_pipeline import ApplicationPipeline

    pipeline = ApplicationPipeline(
        ats_scorer=ats_scorer,
        job_matcher=job_matcher,
        recommendation_engine=recommendation_engine,
        resume_tailor=resume_tailor,
        resume_generator=resume_generator,
        cover_letter_generator=cover_gen,
        repository=repository,
        job_applier=job_applier,
        github_service=github_service,
        portfolio_service=portfolio_service,
        notifier=notifier,
        output_dir="output",
    )
    logger.info("Application pipeline initialised")

    # ── Job provider ────────────────────────────────────────
    from app.jobs.providers import RemoteOKProvider

    remoteok = RemoteOKProvider(timeout=30)

    # ── Scheduler ───────────────────────────────────────────
    from app.scheduler import Scheduler

    scheduler = Scheduler(
        pipeline=pipeline,
        resume=resume,
        providers=[remoteok],
        notifier=notifier,
    )
    scheduler.start()

    logger.info("=" * 50)
    logger.info("Scheduler is running — first cycle starting now")
    logger.info("Cycles run every 30 minutes")
    logger.info("Press Ctrl+C to stop gracefully")
    logger.info("=" * 50)

    # ── Keep main thread alive ──────────────────────────────
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received SIGINT — shutting down...")
        scheduler.shut_down(wait=True)
        logger.info("Goodbye.")


if __name__ == "__main__":
    main()
