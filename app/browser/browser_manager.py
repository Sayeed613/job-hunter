"""Browser management — launches and manages a Chromium instance with anti-detection."""

from __future__ import annotations

import logging
from typing import Any, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger("job_automation_bot")

_STEALTH_SCRIPT = """\
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = { runtime: {} };
"""


class BrowserManager:
    """Manages a Chromium instance with anti-detection and stealth settings.

    Usage:
        mgr = BrowserManager()
        await mgr.launch(headless=True)
        page = await mgr.new_page()
        # ... interact ...
        await page.close()
        await mgr.close()
    """

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def launch(self, headless: bool = True) -> None:
        """Launch Chromium with anti-detection args and stealth init script.

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

        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            java_script_enabled=True,
            accept_downloads=True,
        )

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
        """Close the browser and stop Playwright driver."""
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
