"""Human-like browser automation — manager and behaviour helpers.

Uses Playwright's synchronous API (``sync_playwright``) to match the
rest of the Project Headhunter codebase (see :class:`YCProvider`).

The :class:`BrowserManager` owns the browser lifecycle; callers get
a :class:`Page` via :meth:`new_page` and should close it when done.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

logger = logging.getLogger("headhunter")

_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
"""


class HumanBehavior:
    """Collection of static methods that simulate realistic user interaction.

    Each method adds random delays and variation so automated activity
    is harder to distinguish from a real person.
    """

    @staticmethod
    def random_delay(min_sec: float = 0.5, max_sec: float = 3.0) -> None:
        """Sleep for a random duration between *min_sec* and *max_sec*."""
        time.sleep(random.uniform(min_sec, max_sec))

    @staticmethod
    def type_human_like(page: Page, text: str, selector: str | None = None) -> None:
        """Type *text* character-by-character with realistic delays.

        If *selector* is provided the element is focused first.
        Simulates occasional typos followed by correction.
        """
        if selector:
            page.click(selector)
            HumanBehavior.random_delay(0.2, 0.5)

        for char in text:
            # ~5 % chance of a typo + backspace.
            if random.random() < 0.05:
                wrong = random.choice("abcdefghijklmnopqrstuvwxyz")
                page.keyboard.type(wrong, delay=random.randint(50, 120))
                time.sleep(random.uniform(0.1, 0.25))
                page.keyboard.press("Backspace")
                time.sleep(random.uniform(0.05, 0.15))
            page.keyboard.type(char, delay=random.randint(50, 150))
            # Slightly longer pause between words.
            if char == " ":
                time.sleep(random.uniform(0.08, 0.25))

    @staticmethod
    def scroll_down(page: Page, steps: int = 3) -> None:
        """Scroll down in several small increments (human-like)."""
        for _ in range(steps):
            page.evaluate("window.scrollBy(0, window.innerHeight * 0.3)")
            HumanBehavior.random_delay(0.3, 0.8)

    @staticmethod
    def scroll_up(page: Page, steps: int = 2) -> None:
        """Scroll up in several small increments."""
        for _ in range(steps):
            page.evaluate("window.scrollBy(0, -window.innerHeight * 0.3)")
            HumanBehavior.random_delay(0.3, 0.8)


class BrowserManager:
    """Manages a headless (or headed) Chromium instance via Playwright.

    Usage::

        mgr = BrowserManager()
        mgr.launch(headless=False)          # visible browser for debugging
        page = mgr.new_page()
        page.goto("https://example.com")
        ...                                 # interact
        page.close()
        mgr.close()                         # shutdown
    """

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    # ── Lifecycle ─────────────────────────────────────────────

    def launch(
        self,
        headless: bool = True,
        viewport: dict[str, int] | None = None,
    ) -> None:
        """Launch a Chromium instance with anti-detection measures.

        Args:
            headless: Whether to run in headless mode.  Set to
                ``False`` to watch the browser for debugging.
            viewport: Window size dict (``{"width": …, "height": …}``).
                Defaults to 1920×1080.
        """
        self._playwright = sync_playwright().start()

        self._browser = self._playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-web-resources",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport=viewport or {"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Kolkata",
            storage_state=None,
        )

        # Inject stealth scripts into every page.
        self._context.add_init_script(_STEALTH_SCRIPT)

        logger.info(
            "Browser launched",
            extra={"headless": headless, "viewport": viewport or "1920x1080"},
        )

    def new_page(self) -> Page:
        """Open a new tab and return it."""
        if not self._context:
            raise RuntimeError("BrowserManager not launched. Call launch() first.")
        page = self._context.new_page()
        page.set_extra_http_headers({
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,*/*;q=0.8"
            ),
        })
        return page

    def close(self) -> None:
        """Close the browser and stop the Playwright driver."""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        logger.info("Browser closed")

    def __enter__(self) -> BrowserManager:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
