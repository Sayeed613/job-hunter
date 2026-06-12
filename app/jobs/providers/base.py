"""Base class for all job providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from app.models.job import Job

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager


class BaseJobProvider(ABC):
    """Abstract base class for job platform providers.

    All providers inherit from this and implement fetch_jobs().
    Providers that use browser-based scraping can receive a shared
    :class:`BrowserManager` instance via :meth:`set_browser_manager`.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'RemoteOK')."""

    @abstractmethod
    async def fetch_jobs(self) -> list[Job]:
        """Fetch latest jobs from this platform.

        Returns:
            A list of Job objects.

        Raises:
            Should never raise — catch all exceptions and return [].
        """

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        """Inject a shared Playwright browser for JS-rendered job pages.

        Called by the pipeline before the fetch cycle so that providers
        that need a real browser (Indeed, LinkedIn, Naukri) can use the
        same session that is used for form-filling later.

        Default implementation is a no-op. Providers override this to
        store the reference.
        """
        pass
