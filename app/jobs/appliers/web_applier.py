"""Web-based job application submitter using browser automation.

Wraps the :mod:`app.browser` module into the :class:`BaseApplier`
interface so the :class:`JobApplier` orchestrator can use browser
automation alongside API-based appliers.

Handles two categories:

1. **LinkedIn Easy Apply** — full login + Easy Apply flow.
2. **Generic job boards** — Indeed, Naukri, Wellfound, etc. — via
   generic form detection and filling.

The applier launches a shared browser instance on first use and
reuses it across submissions within a cycle.  The browser is
closed via :meth:`close`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.browser.browser_manager import BrowserManager
from app.jobs.appliers.base import ApplierResult, ApplicationMethod, BaseApplier

logger = logging.getLogger("headhunter")

# Screenshot directory for debugging failures.
_SCREENSHOT_DIR = Path("logs")
_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class WebApplier(BaseApplier):
    """Submit applications via browser automation (LinkedIn + generic boards).

    The applier launches Chromium (headless by default) and interacts
    with job boards as a human would.

    Example usage::

        applier = WebApplier()
        result = applier.apply(
            candidate_name="Jane Doe",
            candidate_email="jane@example.com",
            candidate_phone="+91-9876543210",
            resume_path="output/resume_123.docx",
            cover_letter_path="output/cover_123.docx",
            job_title="Frontend Engineer",
            job_apply_url="https://www.linkedin.com/jobs/view/...",
            job_description="...",
        )
        applier.close()

    .. note::

        Requires Playwright and Chromium to be installed::

            pip install playwright
            python -m playwright install chromium
    """

    display_name = "Browser Automation"

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> None:
        """Initialise the applier.

        Args:
            headless: Whether to run the browser headless.  Set to
                ``False`` to watch the automation for debugging.
            timeout_ms: Default timeout for page operations (ms).
        """
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._browser: BrowserManager | None = None

    # ── Public API ───────────────────────────────────────────

    def apply(
        self,
        candidate_name: str,
        candidate_email: str,
        candidate_phone: str,
        resume_path: str,
        cover_letter_path: str,
        job_title: str,  # noqa: ARG002 — kept for interface
        job_apply_url: str,
        job_description: str,  # noqa: ARG002 — kept for interface
        extra: dict | None = None,
    ) -> ApplierResult:
        """Submit an application via browser automation.

        Args:
            candidate_name: Full name.
            candidate_email: Email address.
            candidate_phone: Phone number.
            resume_path: Path to the tailored resume file.
            cover_letter_path: Path to the cover letter file.
            job_title: Ignored by browser automation.
            job_apply_url: The job's application URL — used to
                detect LinkedIn vs generic boards.
            job_description: Ignored by browser automation.
            extra: Optional.  If ``headless`` is provided it overrides
                the instance default.

        Returns:
            An :class:`ApplierResult` summarising the outcome.
        """
        # Determine headless mode.
        headless = self._headless
        if extra and "headless" in extra:
            headless = bool(extra["headless"])

        # Read cover letter text.
        cover_text = self._read_file_text(cover_letter_path)

        # Launch browser if not already running.
        if self._browser is None:
            self._browser = BrowserManager()
            try:
                self._browser.launch(headless=headless)
            except Exception:
                logger.exception("Failed to launch browser")
                return ApplierResult(
                    success=False,
                    application_method=ApplicationMethod.WEB_FORM,
                    error_message="Browser launch failed — check Playwright/Chromium installation.",
                )

        # Detect LinkedIn.
        if "linkedin.com" in job_apply_url.lower():
            return self._apply_linkedin(
                job_url=job_apply_url,
                resume_path=resume_path,
                cover_text=cover_text,
                headless=headless,
            )

        # Generic job board.
        return self._apply_generic(
            job_url=job_apply_url,
            resume_path=resume_path,
            cover_text=cover_text,
            candidate_email=candidate_email,
            candidate_phone=candidate_phone,
            candidate_name=candidate_name,
            headless=headless,
        )

    def close(self) -> None:
        """Close the shared browser instance."""
        if self._browser is not None:
            self._browser.close()
            self._browser = None
            logger.info("WebApplier browser closed")

    def __del__(self) -> None:
        """Ensure the browser is closed on garbage collection."""
        try:
            self.close()
        except Exception:
            pass

    # ── URL detection ────────────────────────────────────────

    @staticmethod
    def can_handle(job_apply_url: str) -> bool:
        """Return ``True`` for URLs that can be handled by browser automation.

        Returns ``True`` for LinkedIn, Indeed, Naukri, and other
        non-API job boards.  Returns ``False`` for:
        - URLs handled by dedicated API appliers (Greenhouse, Lever, Ashby)
        - Non-HTTP URLs
        """
        url_lower = job_apply_url.lower()
        if not url_lower.startswith("http"):
            return False
        excluded = [
            "boards.greenhouse.io",
            "jobs.lever.co",
            "jobs.ashbyhq.com",
        ]
        if any(d in url_lower for d in excluded):
            return False
        return True

    # ── Internal: LinkedIn ───────────────────────────────────

    def _apply_linkedin(
        self,
        job_url: str,
        resume_path: str,
        cover_text: str,
        headless: bool,
    ) -> ApplierResult:
        """Apply via LinkedIn Easy Apply."""
        from app.browser.linkedin_applier import LinkedInApplier  # noqa: PLC0415

        applier = LinkedInApplier(browser=self._browser)
        try:
            success = applier.apply_to_job(
                job_url=job_url,
                resume_path=resume_path,
                cover_letter_text=cover_text,
                headless=headless,
            )
        except Exception as exc:
            logger.exception("LinkedIn application failed")
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.LINKEDIN,
                error_message=f"LinkedIn automation error: {exc}",
            )

        if success:
            logger.info("LinkedIn Easy Apply submitted successfully")
            return ApplierResult(
                success=True,
                application_method=ApplicationMethod.LINKEDIN,
                confirmation_url=job_url,
            )

        return ApplierResult(
            success=False,
            application_method=ApplicationMethod.LINKEDIN,
            error_message="LinkedIn Easy Apply failed. Check logs/screenshots.",
        )

    # ── Internal: Generic ────────────────────────────────────

    def _apply_generic(
        self,
        job_url: str,
        resume_path: str,
        cover_text: str,
        candidate_email: str,
        candidate_phone: str,
        candidate_name: str,
        headless: bool,  # noqa: ARG002
    ) -> ApplierResult:
        """Apply via generic form-filler."""
        if self._browser is None:
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.WEB_FORM,
                error_message="Browser not available",
            )

        page = self._browser.new_page()

        try:
            page.goto(job_url, wait_until="networkidle", timeout=self._timeout_ms)

            from app.browser.generic_applier import GenericFormFiller  # noqa: PLC0415

            filler = GenericFormFiller(page=page)
            success = filler.apply(
                resume_path=resume_path,
                cover_letter_text=cover_text,
                candidate_email=candidate_email,
                candidate_phone=candidate_phone,
                candidate_name=candidate_name,
            )
        except Exception as exc:
            logger.exception("Generic form fill failed")
            try:
                page.screenshot(path=str(_SCREENSHOT_DIR / "generic_apply_error.png"))
            except Exception:
                pass
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.WEB_FORM,
                error_message=f"Form fill error: {exc}",
            )
        finally:
            try:
                page.close()
            except Exception:
                pass

        if success:
            logger.info("Generic form submitted successfully")
            return ApplierResult(
                success=True,
                application_method=ApplicationMethod.WEB_FORM,
                confirmation_url=job_url,
            )

        return ApplierResult(
            success=False,
            application_method=ApplicationMethod.WEB_FORM,
            error_message="Form fill could not find or submit the apply button.",
        )

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _read_file_text(path: str) -> str:
        """Read text content from a file (DOCX or plain text)."""
        file_path = Path(path)
        if not file_path.exists():
            return ""

        if file_path.suffix == ".docx":
            try:
                from docx import Document  # noqa: PLC0415
                doc = Document(str(file_path))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception:
                logger.warning("Could not read DOCX for cover letter text")
                return ""

        return file_path.read_text(encoding="utf-8", errors="replace")
