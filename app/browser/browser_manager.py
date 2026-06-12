"""Browser management — launches and manages a Chromium instance with anti-detection."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger("job_automation_bot")

_STEALTH_SCRIPT = """\
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 4});
Object.defineProperty(screen, 'width', {get: () => 1366});
Object.defineProperty(screen, 'height', {get: () => 768});
Object.defineProperty(navigator, 'notification', {get: () => 'default'});
window.chrome = { runtime: {} };
"""


class BrowserManager:
    """Manages a Chromium instance with anti-detection and stealth settings.

    Usage:
        async with BrowserManager() as mgr:
            page = await mgr.new_page()
            await page.goto("https://example.com")
            ...

    Session cookies are persisted to secrets/browser_session.json and
    refreshed on every close().
    """

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._session_path: Path = Path(
            os.getenv("SESSION_STATE_PATH", "secrets/browser_session.json")
        )

    # ── Properties ───────────────────────────────────────────

    @property
    def is_launched(self) -> bool:
        """True if the browser context has been created."""
        return self._context is not None

    # ── Lifecycle ────────────────────────────────────────────

    async def launch(self, headless: bool = True) -> None:
        """Launch Chromium with anti-detection args and stealth init script.

        Loads a saved session from secrets/browser_session.json if it exists.

        Args:
            headless: Run headless (True) or visible (False for debugging).
        """
        self._playwright = await async_playwright().start()

        self._browser = await self._playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-extensions",
                "--disable-plugins",
                "--disable-web-resources",
            ],
        )

        context_kwargs: dict[str, Any] = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1366, "height": 768},
            "locale": "en-IN",
            "timezone_id": "Asia/Kolkata",
            "java_script_enabled": True,
            "accept_downloads": True,
        }

        # Load saved session if it exists
        if self._session_path.exists():
            context_kwargs["storage_state"] = str(self._session_path)
            logger.info("Loaded saved browser session from %s", self._session_path)
        else:
            logger.info("No saved session at %s — starting fresh", self._session_path)

        self._context = await self._browser.new_context(**context_kwargs)
        await self._context.add_init_script(_STEALTH_SCRIPT)

        logger.info("Browser launched", extra={"headless": headless})

    async def new_page(self) -> Page:
        """Create a new page (tab) in the browser context.

        Returns:
            A Playwright Page object.

        Raises:
            RuntimeError: If the browser has not been launched.
        """
        if not self._context:
            raise RuntimeError("Browser not launched. Call launch() first.")
        page = await self._context.new_page()
        await page.set_extra_http_headers({
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        return page

    async def close(self) -> None:
        """Close the browser, save session state, and stop Playwright driver.

        Refreshes saved cookies/session so they don't expire between runs.
        """
        # Save session state before closing
        if self._context and self._session_path.exists():
            try:
                state = await self._context.storage_state()
                self._session_path.parent.mkdir(parents=True, exist_ok=True)
                self._session_path.write_text(
                    __import__("json").dumps(state, indent=2),
                    encoding="utf-8",
                )
                logger.info("Browser session saved to %s", self._session_path)
            except Exception:
                logger.exception("Failed to save browser session")

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        logger.info("Browser closed")

    # ── Async context manager ─────────────────────────────────

    async def __aenter__(self) -> BrowserManager:
        await self.launch(
            headless=os.getenv("HEADLESS", "true").lower() == "true"
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
