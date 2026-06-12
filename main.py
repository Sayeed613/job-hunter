"""Application entry point for the Job Automation Bot.

Wires all async services together and starts the scheduler.
Usage:
    python main.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config.settings import Settings


async def main() -> None:
    """Initialise all services and start the scheduler."""
    settings = Settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("job_automation_bot")

    logger.info("=" * 50)
    logger.info("🚀 JOB AUTOMATION BOT STARTING")
    logger.info("=" * 50)

    # ── Parse resume ────────────────────────────────────────
    from app.resume.parser import ResumeParser

    resume_path = settings.base_resume_path
    parser = ResumeParser()
    resume = parser.parse_docx(resume_path)
    logger.info("Resume loaded", extra={"candidate": resume.name, "skills": len(resume.skills)})

    # ── Firebase (optional) ─────────────────────────────────
    from app.database import FirestoreRepository, initialize as init_firebase, is_initialized

    init_firebase(settings)
    repository = FirestoreRepository()
    if is_initialized():
        logger.info("Firestore initialised")
    else:
        logger.warning("Firebase not configured — persistence disabled")

    # ── AI Client ───────────────────────────────────────────
    from app.ai.client import AIClient

    ai_client = AIClient(settings=settings)
    if not ai_client.is_available:
        logger.warning("OPENAI_API_KEY not set — AI features disabled")

    # ── Telegram Notifier ────────────────────────────────────
    from app.telegram.notifier import TelegramNotifier

    notifier = TelegramNotifier(
        token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )

    # ── Pipeline ─────────────────────────────────────────────
    from app.pipeline.orchestrator import Pipeline

    pipeline = Pipeline(
        ai_client=ai_client,
        repository=repository,
        notifier=notifier,
        settings=settings,
    )

    # ── Job Providers ────────────────────────────────────────
    providers: list = []

    for mod_name, cls_name in [
        ("app.jobs.providers.remoteok", "RemoteOKProvider"),
        ("app.jobs.providers.weworkremotely", "WeWorkRemotelyProvider"),
        ("app.jobs.providers.linkedin", "LinkedInProvider"),
        ("app.jobs.providers.indeed", "IndeedProvider"),
        ("app.jobs.providers.naukri", "NaukriProvider"),
        ("app.jobs.providers.wellfound", "WellfoundProvider"),
    ]:
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name)
            providers.append(cls())
            logger.info("Loaded provider: %s", cls_name)
        except Exception as e:
            logger.warning("Provider %s not available: %s", cls_name, e)

    logger.info("Registered %d job providers", len(providers))

    # ── Schedule ─────────────────────────────────────────────
    from app.scheduler.scheduler import Scheduler

    scheduler = Scheduler(
        pipeline=pipeline,
        resume=resume,
        providers=providers,
        notifier=notifier,
        settings=settings,
    )

    # Use asyncio Event for graceful shutdown (works on all platforms)
    stop_event = asyncio.Event()

    def _shutdown() -> None:
        logger.info("Shutdown requested — stopping...")
        scheduler.stop()
        stop_event.set()

    # Register signal handlers where available
    try:
        loop = asyncio.get_running_loop()
        if sys.platform != "win32":
            loop.add_signal_handler(sys.SIGINT, _shutdown)
            loop.add_signal_handler(sys.SIGTERM, _shutdown)
        else:
            # Windows: use KeyboardInterrupt handler instead
            import signal
            signal.signal(signal.SIGINT, lambda *_: _shutdown())
            signal.signal(signal.SIGTERM, lambda *_: _shutdown())
    except (NotImplementedError, RuntimeError):
        logger.info("Signal handlers not available — using KeyboardInterrupt fallback")

    # Start the scheduler
    scheduler.start()
    await notifier.send_message("🤖 *Bot started!* Running 24/7...")

    # Keep running until shutdown
    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        scheduler.stop()
        logger.info("Goodbye.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
