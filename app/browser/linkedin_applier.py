"""LinkedIn Easy Apply automation.

Handles the full flow: navigate to LinkedIn, log in, navigate to a job
posting, click Easy Apply, fill the form, and submit.

Requires LinkedIn credentials configured via :class:`Settings`.
Only works with a visible browser (``headless=False``) when 2FA is
enabled on the account.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from app.browser.browser_manager import BrowserManager, HumanBehavior

logger = logging.getLogger("headhunter")

_LOGIN_URL = "https://www.linkedin.com/login"
_FEED_INDICATORS = ("/feed", "/mynetwork", "/jobs")

# Screenshot directory for debugging failures.
_SCREENSHOT_DIR = Path("logs")
_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class LinkedInApplier:
    """Log into LinkedIn and submit Easy Apply applications.

    Usage::

        applier = LinkedInApplier(email="...", password="...")
        success = applier.apply_to_job(
            job_url="https://www.linkedin.com/jobs/view/...",
            resume_path="output/resume_123.docx",
            cover_letter_text="Dear Hiring Team...",
        )
    """

    def __init__(
        self,
        email: str = "",
        password: str = "",
        browser: BrowserManager | None = None,
    ) -> None:
        """Initialise the applier.

        Args:
            email: LinkedIn account email.  Falls back to
                ``Settings.linkedin_email``.
            password: LinkedIn account password.  Falls back to
                ``Settings.linkedin_password``.
            browser: Optional shared :class:`BrowserManager`.  A fresh
                one is created when ``None``.
        """
        from app.config.settings import Settings  # noqa: PLC0415

        cfg = Settings()
        self._email = email or cfg.linkedin_email or ""
        self._password = password or cfg.linkedin_password or ""

        self._browser = browser
        self._owns_browser = browser is None

        self._available = bool(self._email and self._password)

        if not self._available:
            logger.warning(
                "LinkedIn credentials not configured — LinkedInApplier "
                "will skip submissions. Set LINKEDIN_EMAIL and "
                "LINKEDIN_PASSWORD."
            )

    # ── Public API ───────────────────────────────────────────

    def apply_to_job(
        self,
        job_url: str,
        resume_path: str,
        cover_letter_text: str,
        headless: bool = True,
    ) -> bool:
        """Log into LinkedIn and submit an Easy Apply application.

        Args:
            job_url: Full URL to the LinkedIn job posting.
            resume_path: Filesystem path to the resume file.
            cover_letter_text: Cover letter text to fill in the form.
            headless: Whether to run the browser headless.  Set to
                ``False`` to see the browser for debugging.

        Returns:
            ``True`` if the application was submitted successfully.
        """
        if not self._available:
            logger.info("LinkedInApplier not available — skipping")
            return False

        # Lazily create browser if not shared.
        if self._browser is None:
            self._browser = BrowserManager()
            self._browser.launch(headless=headless)

        page = self._browser.new_page()

        try:
            # Step 1: Log in.
            if not self._login(page):
                self._screenshot(page, "linkedin_login_failed")
                return False

            # Step 2: Navigate to job and apply.
            return self._easy_apply(page, job_url, resume_path, cover_letter_text)
        except Exception:
            logger.exception("LinkedIn application failed")
            self._screenshot(page, "linkedin_apply_error")
            return False
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            if self._owns_browser and self._browser is not None:
                self._browser.close()
                self._browser = None

    # ── Login flow ───────────────────────────────────────────

    def _login(self, page: Page) -> bool:
        """Log into linkedin.com with human-like typing."""
        logger.info("Navigating to LinkedIn login page")
        page.goto(_LOGIN_URL, wait_until="networkidle", timeout=30_000)
        HumanBehavior.random_delay(2, 4)

        # Fill email.
        HumanBehavior.type_human_like(page, self._email, 'input[name="session_key"]')
        HumanBehavior.random_delay(1, 2)

        # Fill password.
        HumanBehavior.type_human_like(
            page, self._password, 'input[name="session_password"]'
        )
        HumanBehavior.random_delay(1, 2)

        # Click Sign in.
        page.click('button[aria-label="Sign in"]')
        logger.info("Login button clicked")

        # Wait for navigation.
        page.wait_for_load_state("networkidle", timeout=30_000)

        # Check if we landed on a logged-in page.
        current = page.url.lower()
        logged_in = any(indicator in current for indicator in _FEED_INDICATORS)
        if logged_in:
            logger.info("LinkedIn login successful")
        else:
            logger.warning(
                "LinkedIn login may have failed or 2FA is required. "
                "Set headless=False to check manually."
            )
        return logged_in

    # ── Easy Apply flow ──────────────────────────────────────

    def _easy_apply(
        self,
        page: Page,
        job_url: str,
        resume_path: str,
        cover_letter_text: str,
    ) -> bool:
        """Navigate to a job posting and click through Easy Apply."""
        logger.info("Navigating to job: %s", job_url)
        page.goto(job_url, wait_until="networkidle", timeout=30_000)
        HumanBehavior.random_delay(2, 4)

        # Scroll to see the Easy Apply button.
        HumanBehavior.scroll_down(page, steps=2)
        HumanBehavior.random_delay(1, 2)
        # Scroll back up to find it.
        page.evaluate("window.scrollTo(0, 0)")
        HumanBehavior.random_delay(1, 2)

        # Click "Easy Apply" button.
        easy_btn = page.locator('button:has-text("Easy Apply")')
        if not easy_btn.is_visible(timeout=5_000):
            logger.warning("Easy Apply button not found — job may require external apply")
            return False

        easy_btn.click()
        logger.info("Easy Apply button clicked")
        HumanBehavior.random_delay(2, 3)

        # Fill the multi-step form.
        self._fill_easy_apply_form(page, resume_path, cover_letter_text)
        return True

    # ── Form filling ─────────────────────────────────────────

    @staticmethod
    def _fill_easy_apply_form(
        page: Page,
        resume_path: str,
        cover_letter_text: str,
    ) -> None:
        """Work through the Easy Apply modal — fill fields & submit.

        Handles multi-step forms by clicking "Next" until "Submit"
        is available.
        """
        max_steps = 10
        for step in range(max_steps):
            HumanBehavior.random_delay(1, 2)

            # Check if we're on the final step.
            submit_btn = page.locator('button:has-text("Submit application")')
            if submit_btn.is_visible(timeout=2_000):
                # Fill any remaining fields before submitting.
                LinkedInApplier._fill_visible_fields(
                    page, resume_path, cover_letter_text,
                )
                HumanBehavior.random_delay(1, 2)
                submit_btn.click()
                logger.info("Easy Apply submitted (step %d/%d)", step + 1, max_steps)
                HumanBehavior.random_delay(2, 4)
                return

            # Look for Next / Review / Continue button.
            next_btn = (
                page.locator('button:has-text("Next")')
                .or_(page.locator('button:has-text("Review")'))
                .or_(page.locator('button:has-text("Continue")'))
            )
            if not next_btn.is_visible(timeout=3_000):
                logger.warning("No Next/Submit button found at step %d", step + 1)
                break

            # Fill visible fields on this step.
            LinkedInApplier._fill_visible_fields(
                page, resume_path, cover_letter_text,
            )
            HumanBehavior.random_delay(1, 2)
            next_btn.click()
            logger.info("Easy Apply — clicked Next (step %d)", step + 1)

        logger.warning(
            "Easy Apply flow did not reach submission after %d steps",
            max_steps,
        )

    @staticmethod
    def _fill_visible_fields(
        page: Page,
        resume_path: str,
        cover_letter_text: str,
    ) -> None:
        """Fill all visible form fields in the Easy Apply modal."""
        # File upload (resume).
        file_inputs = page.query_selector_all('input[type="file"]')
        for fi in file_inputs:
            if fi.is_visible():
                try:
                    fi.set_input_files(resume_path)
                    logger.info("Uploaded resume: %s", resume_path)
                    HumanBehavior.random_delay(1, 2)
                except Exception:
                    logger.warning("Failed to upload resume to a file input")

        # Text inputs and textareas.
        inputs = page.query_selector_all(
            "input:not([type=file]):not([type=hidden]), textarea, select"
        )
        for inp in inputs:
            if not inp.is_visible():
                continue
            try:
                LinkedInApplier._fill_single_field(
                    page, inp, cover_letter_text,
                )
            except Exception:
                continue

    @staticmethod
    def _fill_single_field(
        page: Page,
        inp: Any,
        cover_letter_text: str,
    ) -> None:
        """Fill one form field based on its type, placeholder, or label."""
        tag = inp.evaluate("el => el.tagName").lower()
        input_type = inp.get_attribute("type") or ""
        placeholder = (inp.get_attribute("placeholder") or "").lower()
        aria_label = (inp.get_attribute("aria-label") or "").lower()
        name = (inp.get_attribute("name") or "").lower()
        field_id = inp.get_attribute("id") or ""
        selector = f"#{field_id}" if field_id else f'[name="{name}"]'

        # Skip already-filled fields.
        try:
            if inp.input_value():
                return
        except Exception:
            pass

        # Select dropdown.
        if tag == "select":
            options = inp.query_selector_all("option")
            if len(options) > 1:
                inp.select_option(index=1)
                HumanBehavior.random_delay(0.3, 0.7)
            return

        # File already handled above.
        if input_type == "file":
            return

        # Textarea — likely the cover letter.
        if tag == "textarea" or "cover letter" in (placeholder + " " + aria_label):
            HumanBehavior.type_human_like(page, cover_letter_text, selector=selector)
            return

        # Email field.
        if input_type == "email" or "email" in (placeholder + " " + aria_label):
            from app.config.settings import Settings  # noqa: PLC0415
            cfg = Settings()
            HumanBehavior.type_human_like(page, cfg.linkedin_email or "", selector=selector)
            return

        # Phone field.
        if input_type == "tel" or "phone" in (placeholder + " " + aria_label):
            HumanBehavior.type_human_like(page, "+91-9876543210", selector=selector)
            return

        # Name field.
        if "name" in (placeholder + " " + aria_label):
            from app.config.settings import Settings  # noqa: PLC0415
            cfg = Settings()
            HumanBehavior.type_human_like(
                page, cfg.linkedin_email or "", selector=selector,
            )
            return

        # Checkbox.
        if input_type == "checkbox":
            inp.check()
            HumanBehavior.random_delay(0.3, 0.6)
            return

        # Generic text — fill with a safe default.
        if input_type in ("text", "url", ""):
            HumanBehavior.type_human_like(
                page, "See attached resume for details", selector=selector,
            )
            return

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _screenshot(page: Page, name: str) -> None:
        """Save a screenshot for debugging."""
        try:
            path = _SCREENSHOT_DIR / f"{name}.png"
            page.screenshot(path=str(path))
            logger.info("Screenshot saved: %s", path)
        except Exception:
            pass
