"""Browser automation for human-like job application submission.

Provides human-like typing, mouse movement, and form-filling via
Playwright (sync API) for job boards that don't offer public APIs.

Modules
-------
browser_manager:
    :class:`HumanBehavior` — realistic delays, typing, scrolling.
    :class:`BrowserManager` — launches/configures Chromium with
    anti-detection measures.

linkedin_applier:
    :class:`LinkedInApplier` — LinkedIn login + Easy Apply flow.

generic_applier:
    :class:`GenericFormFiller` — fills application forms on any
    job board (Indeed, Naukri, etc.).
"""

from app.browser.browser_manager import BrowserManager, HumanBehavior
from app.browser.generic_applier import GenericFormFiller

__all__ = [
    "BrowserManager",
    "GenericFormFiller",
    "HumanBehavior",
]
