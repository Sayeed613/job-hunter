"""Browser automation for human-like job application submission.

Provides async Playwright-based browser automation with anti-detection,
human-like typing, form filling, and application routing.

Modules
-------
manager:
    :class:`BrowserManager` — launches/configures Chromium with
    anti-detection measures (async).
human:
    :class:`Human` — realistic delays, typing, scrolling, screenshots.
form_filler:
    :class:`FormFiller` — detects and fills ANY job application form.
router:
    :class:`ApplicationRouter` — routes jobs to correct apply strategy.
"""

from app.browser.browser_manager import BrowserManager
from app.browser.human import Human
from app.browser.form_filler import FormFiller
from app.browser.router import ApplicationRouter

__all__ = [
    "ApplicationRouter",
    "BrowserManager",
    "FormFiller",
    "Human",
]
