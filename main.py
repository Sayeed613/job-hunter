"""Application entry point for the Job Automation Bot.

Wires all async services together and starts the scheduler.

Usage:
    python main.py                    # Normal operation (24/7 scheduler)
    python main.py --relogin linkedin # Re-authenticate a platform manually
    python main.py --relogin workatastartup
    python main.py --relogin wellfound
    python main.py --relogin naukri
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config.settings import Settings

logger = logging.getLogger("job_automation_bot")


async def relogin_platform(platform: str) -> None:
    """Run an interactive manual login flow for *platform*.

    Opens a non-headless browser, navigates to the platform's login page,
    and waits for the user to log in manually. Once the user confirms
    (presses Enter), saves the browser context's storage_state to disk.

    Args:
        platform: One of "linkedin", "wellfound", "workatastartup", "naukri".
    """
    from app.browser.session import LoginSession
    from app.browser.browser_manager import BrowserManager

    session = LoginSession()
    valid_platforms = session.list_platforms()

    if platform not in valid_platforms:
        print(f"❌ Unknown platform: {platform}")
        print(f"   Valid platforms: {', '.join(valid_platforms)}")
        sys.exit(1)

    # Check if we have stored credentials (shared from LinkedIn)
    email, password = session.get_credentials(platform)
    login_url = session.get_login_url(platform)

    print(f"\n{'=' * 60}")
    print(f"🔐 Manual login for: {platform.upper()}")
    print(f"{'=' * 60}")
    print(f"→ Opening {login_url}")
    print(f"→ A browser window will open.")
    if email:
        print(f"→ Email will be pre-filled: {email}")
        print(f"→ Password will be typed automatically from .env")
    else:
        print(f"→ No saved credentials — please enter manually.")
    print(f"→ Please log in (solve any CAPTCHA / 2FA if prompted).")
    print(f"→ Once you see your feed/dashboard, press Enter here to save the session.")
    print(f"{'=' * 60}\n")

    # Launch non-headless browser
    mgr = BrowserManager()
    await mgr.launch(headless=False)

    # Create a dedicated context for this platform
    ctx = await mgr.new_platform_context(platform)
    page = await ctx.new_page()

    try:
        await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Auto-fill email and password if we have them
        if email and password:
            try:
                # Try common selectors for email/username
                email_sel = "input[name='session_key'], #username, input[type='email'], input[name='email'], input[name='login']"
                email_el = await page.query_selector(email_sel)
                if email_el:
                    await email_el.click()
                    await asyncio.sleep(0.3)
                    await email_el.fill(email)
                    print(f"✅ Auto-filled email: {email}")
                else:
                    print("⚠️ Email field not found — enter credentials manually")

                # Try common selectors for password
                pw_sel = "input[name='session_password'], #password, input[type='password'], input[name='password'], input[name='pass']"
                pw_el = await page.query_selector(pw_sel)
                if pw_el:
                    await pw_el.click()
                    await asyncio.sleep(0.3)
                    await pw_el.fill(password)
                    print(f"✅ Auto-filled password")

                # Try to click the submit/login button
                try:
                    btn_sel = "button[type='submit'], input[type='submit'], button:has-text('Sign in'), button:has-text('Log in'), button:has-text('Login')"
                    btn = await page.query_selector(btn_sel)
                    if btn:
                        await btn.click()
                        print("✅ Clicked login button")
                        await asyncio.sleep(2)
                except Exception:
                    pass  # User may need to handle CAPTCHA manually

            except Exception as e:
                print(f"⚠️ Auto-fill issue: {e} — enter credentials manually")
        elif email:
            try:
                email_sel = "input[name='session_key'], #username, input[type='email'], input[name='email']"
                email_el = await page.query_selector(email_sel)
                if email_el:
                    await email_el.click()
                    await asyncio.sleep(0.5)
                    await email_el.fill(email)
                    print(f"✅ Pre-filled email: {email}")
            except Exception:
                print("⚠️ Could not pre-fill email — enter it manually")

        # Wait for user to complete login (solve CAPTCHA/2FA if needed)
        input("⏳ Press Enter AFTER you have logged in successfully...\n")

        # Wait a moment for the page to settle
        await asyncio.sleep(2)

        # Save the session
        state = await ctx.storage_state()
        session.save_session(platform, state)
        print(f"✅ Session saved for {platform}!")
        print(f"   File: {session.get_path(platform)}")
        print(f"   Cookies saved: {len(state.get('cookies', []))}")
        print(f"   Origins saved: {len(state.get('origins', []))}")

        # Also save to the general browser session
        general_path = Path("storage/browser_session.json")
        general_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        general_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"   (also saved to general session: {general_path})")

    except Exception as e:
        print(f"❌ Error during login: {e}")
    finally:
        await page.close()
        await ctx.close()
        await mgr.close()

    print(f"\n✅ Done! You can now run 'python main.py' and the bot will use the saved {platform} session.")
    print(f"   To re-login later (e.g. if session expires): python main.py --relogin {platform}\n")


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

    # ── Local storage (applications.json) ────────────────────
    from app.database import FirestoreRepository

    repository = FirestoreRepository()

    # ── AI Client ───────────────────────────────────────────
    from app.ai.client import AIClient

    ai_client = AIClient(settings=settings)

    # Validate API key immediately so we don't waste time retrying on every AI call
    if ai_client.is_available:
        key_valid = await ai_client.validate()
        if not key_valid:
            logger.warning(
                "AI API key is invalid or unreachable — AI features disabled. "
                "Get a free key at https://console.groq.com"
            )
    else:
        logger.warning("OPENAI_API_KEY not set — AI features disabled")

    # ── WhatsApp Notifier (with local file fallback) ────────
    from app.notifier import WhatsAppNotifier

    notifier = WhatsAppNotifier()

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
        # ── TIER 1: Y Combinator & Startups ──
        ("app.jobs.providers.workatastartup", "WorkAtAStartupProvider"),
        ("app.jobs.providers.wellfound", "WellfoundProvider"),
        ("app.jobs.providers.yc_public", "YCJobBoardProvider"),
        ("app.jobs.providers.greenhouse", "GreenhouseProvider"),
        ("app.jobs.providers.lever", "LeverProvider"),
        ("app.jobs.providers.ashby", "AshbyProvider"),
        # ── TIER 1b: HTTP API providers (reliable) ──
        ("app.jobs.providers.remoteok", "RemoteOKProvider"),
        ("app.jobs.providers.weworkremotely", "WeWorkRemotelyProvider"),
        ("app.jobs.providers.remotive", "RemotiveProvider"),
        ("app.jobs.providers.himalayas", "HimalayasProvider"),
        ("app.jobs.providers.workingnomads", "WorkingNomadsProvider"),
        # ── TIER 2: HTML-scrape providers (medium reliability) ──
        ("app.jobs.providers.ycombinator", "YCombinatorProvider"),
        ("app.jobs.providers.jobspresso", "JobspressoProvider"),
        ("app.jobs.providers.remoteco", "RemoteCoProvider"),
        ("app.jobs.providers.web3career", "Web3CareerProvider"),
        ("app.jobs.providers.arcdev", "ArcDevProvider"),
        ("app.jobs.providers.landingjobs", "LandingJobsProvider"),
        ("app.jobs.providers.europeremotely", "EuropeRemotelyProvider"),
        # ── TIER 2b: Browser-based providers (need Playwright session) ──
        ("app.jobs.providers.linkedin", "LinkedInProvider"),
        ("app.jobs.providers.indeed", "IndeedProvider"),
        ("app.jobs.providers.naukri", "NaukriProvider"),
        ("app.jobs.providers.dice", "DiceProvider"),
        ("app.jobs.providers.ziprecruiter", "ZipRecruiterProvider"),
        ("app.jobs.providers.glassdoor", "GlassdoorProvider"),
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

    # #region agent log
    import json as _json, time as _time
    with open("debug-eeb1f2.log", "a", encoding="utf-8") as _f:
        _f.write(_json.dumps({"sessionId": "eeb1f2", "hypothesisId": "D", "location": "main.py:main", "message": "startup_providers", "data": {"loaded": len(providers), "expected": 25, "resume": resume.name, "ai_available": ai_client.is_available}, "timestamp": int(_time.time() * 1000)}) + "\n")
    # #endregion

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
    pass  # suppressed startup noise

    # Keep running until shutdown
    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        scheduler.stop()
        logger.info("Goodbye.")


if __name__ == "__main__":
    # ── Handle --relogin flag ────────────────────────────────
    if "--relogin" in sys.argv:
        idx = sys.argv.index("--relogin")
        if idx + 1 < len(sys.argv):
            platform = sys.argv[idx + 1].lower().strip()
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
            asyncio.run(relogin_platform(platform))
        else:
            print("Usage: python main.py --relogin <platform>")
            print("  Valid platforms: linkedin, wellfound, workatastartup, naukri")
            print("\nExample:")
            print("  python main.py --relogin linkedin")
            print("  python main.py --relogin wellfound")
            sys.exit(1)
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass
