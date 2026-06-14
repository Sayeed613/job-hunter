"""Browser management — launches and manages a Chromium instance with anti-detection."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.config.settings import Settings
from app.browser.session import LoginSession

logger = logging.getLogger("job_automation_bot")

_STEALTH_SCRIPT = """\
// ── Essential automation flag removal ──
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// ── Language & locale ──
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'language', {get: () => 'en-US'});

// ── Plugins (real browser has 5) ──
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
    {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
    {name: 'Native Client', filename: 'internal-nacl-plugin'},
  ],
});
Object.defineProperty(navigator, 'mimeTypes', {get: () => [1,2,3,4]});

// ── Hardware fingerprinting ──
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
Object.defineProperty(navigator, 'pdfViewerEnabled', {get: () => false});

// ── Screen / viewport ──
Object.defineProperty(screen, 'width', {get: () => 1366});
Object.defineProperty(screen, 'height', {get: () => 768});
Object.defineProperty(screen, 'availWidth', {get: () => 1366});
Object.defineProperty(screen, 'availHeight', {get: () => 728});
Object.defineProperty(screen, 'colorDepth', {get: () => 24});
Object.defineProperty(screen, 'pixelDepth', {get: () => 24});

// ── Chrome runtime object (anti-bot check) ──
window.chrome = {
  runtime: {},
  loadTimes: function() { return {}; },
  csi: function() { return {}; },
  app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
};

// ── Permissions (avoid notification prompt detection) ──
Object.defineProperty(navigator, 'permissions', {
  get: () => ({
    query: async () => ({state: 'granted', onchange: null}),
  }),
});

// ── WebGL vendor/renderer spoof (matches Chrome on Windows) ──
const getParameterProxyHandler = {
  apply: function(target, thisArg, args) {
    const param = args[0];
    if (param === 37445) return 'Intel Inc.';   // UNMASKED_VENDOR_WEBGL
    if (param === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
    return Reflect.apply(target, thisArg, args);
  },
};
try {
  const canvas = document.createElement('canvas');
  const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
  if (gl) {
    const orig = gl.getParameter.bind(gl);
    gl.getParameter = new Proxy(orig, getParameterProxyHandler);
  }
} catch(e) {}

// ── Connection / network info ──
Object.defineProperty(navigator, 'connection', {
  get: () => ({
    rtt: 100,
    downlink: 10,
    effectiveType: '4g',
    saveData: false,
    onchange: null,
  }),
});
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
        self._session: LoginSession = LoginSession()
        self._session_path: Path = Path(
            Settings().session_state_path
        )
        # Track platform-specific contexts so they can be properly closed
        self._platform_contexts: dict[str, BrowserContext] = {}

    # ── Properties ───────────────────────────────────────────

    @property
    def is_launched(self) -> bool:
        """True if the browser context has been created."""
        return self._context is not None

    @property
    def context(self) -> BrowserContext | None:
        """The active browser context."""
        return self._context

    @property
    def session(self) -> LoginSession:
        """The login session manager."""
        return self._session

    # ── Lifecycle ────────────────────────────────────────────

    async def launch(self, headless: bool = True) -> None:
        """Launch Chromium with anti-detection args and stealth init script.

        Creates the shared browser instance. Individual platform contexts
        should be created via :meth:`new_platform_context` for logged-in access.

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

        # Create default context
        context_kwargs: dict[str, Any] = self._base_context_kwargs()
        self._context = await self._browser.new_context(**context_kwargs)
        await self._context.add_init_script(_STEALTH_SCRIPT)

        logger.info("Browser launched", extra={"headless": headless})

    def _base_context_kwargs(self) -> dict[str, Any]:
        """Return base context kwargs with anti-detection settings."""
        return {
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

    async def new_platform_context(self, platform: str) -> BrowserContext:
        """Create (or reuse) a browser context with the saved session for *platform*.

        If a saved storage_state exists for the platform, it is loaded into
        the new context so the user appears logged in. Contexts are tracked
        internally and closed when :meth:`close` is called.

        Args:
            platform: Platform name ("linkedin", "wellfound", etc.).

        Returns:
            A BrowserContext with the saved session loaded.

        Raises:
            RuntimeError: If the browser has not been launched.
        """
        if not self._browser:
            raise RuntimeError("Browser not launched. Call launch() first.")

        # Reuse existing context for this platform if one exists
        existing = self._platform_contexts.get(platform)
        if existing is not None:
            try:
                # Check if the context is still alive
                await existing.pages()
                logger.debug("Reusing existing context for %s", platform)
                return existing
            except Exception:
                # Context died — clean up and create new one
                del self._platform_contexts[platform]

        context_kwargs = self._base_context_kwargs()

        if self._session.has_session(platform):
            context_kwargs["storage_state"] = str(self._session.get_path(platform))
            logger.info("Loaded saved session for %s", platform)
        else:
            logger.info("No saved session for %s — starting fresh", platform)

        ctx = await self._browser.new_context(**context_kwargs)
        await ctx.add_init_script(_STEALTH_SCRIPT)
        self._platform_contexts[platform] = ctx
        return ctx

    async def save_platform_session(self, context: BrowserContext, platform: str) -> Path:
        """Save the current context's storage_state as the session for *platform*.

        Args:
            context: The browser context (after successful login).
            platform: Platform name.

        Returns:
            Path to the saved session file.
        """
        state = await context.storage_state()
        self._session.save_session(platform, state)
        return self._session.get_path(platform)

    async def new_page(self, platform: str | None = None) -> Page:
        """Create a new page (tab) in the browser context.

        If *platform* is specified, creates a new context with the platform's
        saved session and returns a page from that context.
        Otherwise, uses the default shared context.

        Args:
            platform: Optional platform for logged-in context.

        Returns:
            A Playwright Page object.

        Raises:
            RuntimeError: If the browser has not been launched.
        """
        if platform:
            ctx = await self.new_platform_context(platform)
            page = await ctx.new_page()
            await page.set_extra_http_headers({
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            return page

        if not self._context:
            raise RuntimeError("Browser not launched. Call launch() first.")
        page = await self._context.new_page()
        await page.set_extra_http_headers({
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        return page

    async def close(self) -> None:
        """Close the browser, save all session states, and stop Playwright driver.

        Closes all tracked platform contexts first (saving their storage_state
        if they have a session file), then closes the default context, browser,
        and Playwright driver.
        """
        # Save and close platform-specific contexts
        for platform, ctx in list(self._platform_contexts.items()):
            try:
                # Save session for platforms that have a session file
                if self._session.has_session(platform):
                    state = await ctx.storage_state()
                    self._session.save_session(platform, state)
                    logger.info("Saved session for %s", platform)
                await ctx.close()
            except Exception:
                logger.debug("Error closing %s context", platform, exc_info=True)
        self._platform_contexts.clear()

        # Save general session state before closing
        if self._context:
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
        cfg = Settings()
        await self.launch(headless=cfg.headless)
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
