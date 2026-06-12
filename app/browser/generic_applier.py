"""Generic job-board form filler.

Attempts to fill and submit application forms on any job board by
detecting common UI patterns — apply buttons, form fields, submit
buttons.  Works with Indeed, Naukri, Wellfound, and most other
JavaScript-rendered boards.

Best-effort: not every site uses standard selectors, so the applier
logs warnings when it cannot find expected elements and saves
screenshots for debugging.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, expect

from app.browser.browser_manager import HumanBehavior

logger = logging.getLogger("headhunter")

_SCREENSHOT_DIR = Path("logs")
_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# ── Common button selector patterns ──────────────────────────

_APPLY_BUTTON_SELECTORS = [
    'button:has-text("Apply")',
    'button:has-text("Apply Now")',
    'button:has-text("Easy Apply")',
    "a:has-text(\"Apply\")",
    '[data-testid="apply-button"]',
    'button[aria-label*="Apply" i]',
    ".apply-button",
    "#apply-button",
    '[data-cy="apply-button"]',
    'button:has-text("I&apos;m interested")',
]

_SUBMIT_BUTTON_SELECTORS = [
    'button:has-text("Submit")',
    'button:has-text("Submit application")',
    'button:has-text("Send")',
    'button:has-text("Complete Application")',
    'button[type="submit"]',
    'button:has-text("Apply")',  # fallback — some boards use Apply for submit
    '[data-testid="submit-button"]',
    'button:has-text("Next")',   # multi-step
]

_NEXT_BUTTON_SELECTORS = [
    'button:has-text("Next")',
    'button:has-text("Continue")',
    'button:has-text("Review")',
]

_FORM_FIELD_SELECTORS = (
    "input:not([type=file]):not([type=hidden]):not([type=submit]):not([type=button]):not([type=checkbox]), "
    "textarea, select"
)


class GenericFormFiller:
    """Fills and submits application forms on arbitrary job boards.

    Usage::

        filler = GenericFormFiller(page=page)
        success = filler.apply(
            resume_path="output/resume_123.docx",
            cover_letter_text="Dear Hiring Team...",
            candidate_email="jane@example.com",
            candidate_phone="+91-9876543210",
            candidate_name="Jane Doe",
        )
    """

    def __init__(self, page: Page) -> None:
        self._page = page

    # ── Public API ───────────────────────────────────────────

    def apply(
        self,
        resume_path: str,
        cover_letter_text: str,
        candidate_email: str,
        candidate_phone: str,
        candidate_name: str,
    ) -> bool:
        """Navigate the apply flow: find button → fill → submit.

        Args:
            resume_path: Path to the resume file to upload.
            cover_letter_text: Cover letter text for textareas.
            candidate_email: Candidate email address.
            candidate_phone: Candidate phone number.
            candidate_name: Candidate full name.

        Returns:
            ``True`` if the form was submitted (or the last "Next"
            was clicked).  Returns ``False`` if no apply button was
            found.
        """
        # Step 1: Click the Apply button.
        if not self._click_apply_button():
            return False

        # Step 2: Fill the form (may be multi-step).
        self._fill_form(
            resume_path=resume_path,
            cover_letter_text=cover_letter_text,
            candidate_email=candidate_email,
            candidate_phone=candidate_phone,
            candidate_name=candidate_name,
        )

        # Step 3: Click Submit.
        self._click_submit_button()
        return True

    # ── Step 1: Apply button ─────────────────────────────────

    def _click_apply_button(self) -> bool:
        """Find and click an apply-type button.

        Returns ``True`` if a button was found and clicked.
        """
        for selector in _APPLY_BUTTON_SELECTORS:
            btn = self._page.locator(selector).first
            if btn.is_visible(timeout=2_000):
                btn.click()
                logger.info("Clicked apply button: %s", selector)
                HumanBehavior.random_delay(2, 4)
                return True

        # Fallback: try any visible button that has "apply" in the text.
        all_buttons = self._page.locator("button, a")
        count = all_buttons.count()
        for i in range(count):
            btn = all_buttons.nth(i)
            if btn.is_visible():
                text = (btn.text_content() or "").lower()
                if "apply" in text:
                    btn.click()
                    logger.info("Clicked apply button (fallback): %s", text[:60])
                    HumanBehavior.random_delay(2, 4)
                    return True

        logger.warning("No apply button found on the page")
        self._screenshot("no_apply_button")
        return False

    # ── Step 2: Fill form ────────────────────────────────────

    def _fill_form(
        self,
        resume_path: str,
        cover_letter_text: str,
        candidate_email: str,
        candidate_phone: str,
        candidate_name: str,
    ) -> None:
        """Iterate through multi-step form (Next → fill → Next → … → Submit)."""
        max_steps = 10
        for step in range(max_steps):
            HumanBehavior.random_delay(1, 2)

            # Fill visible fields.
            self._fill_visible_fields(
                resume_path=resume_path,
                cover_letter_text=cover_letter_text,
                candidate_email=candidate_email,
                candidate_phone=candidate_phone,
                candidate_name=candidate_name,
            )

            # Check for Submit button first.
            for sel in _SUBMIT_BUTTON_SELECTORS:
                btn = self._page.locator(sel).first
                if btn.is_visible(timeout=1_000):
                    logger.info("Submit button found at step %d", step + 1)
                    return

            # Check for Next / Continue / Review.
            found_next = False
            for sel in _NEXT_BUTTON_SELECTORS:
                btn = self._page.locator(sel).first
                if btn.is_visible(timeout=1_000):
                    HumanBehavior.random_delay(1, 2)
                    btn.click()
                    logger.info("Clicked Next (step %d): %s", step + 1, sel)
                    found_next = True
                    break

            if not found_next:
                logger.info("No Next/Submit button at step %d — form may be complete", step + 1)
                break

        logger.info("Form fill completed after %d steps", min(step + 1, max_steps))

    def _fill_visible_fields(
        self,
        resume_path: str,
        cover_letter_text: str,
        candidate_email: str,
        candidate_phone: str,
        candidate_name: str,
    ) -> None:
        """Fill every visible input / textarea / select on the current step."""
        inputs = self._page.query_selector_all(_FORM_FIELD_SELECTORS)
        for inp in inputs:
            if not inp.is_visible():
                continue
            try:
                self._fill_single_field(
                    inp, resume_path, cover_letter_text,
                    candidate_email, candidate_phone, candidate_name,
                )
            except Exception:
                continue

    def _fill_single_field(
        self,
        inp: Any,
        resume_path: str,
        cover_letter_text: str,
        candidate_email: str,
        candidate_phone: str,
        candidate_name: str,
    ) -> None:
        """Inspect and fill one form field."""
        tag = inp.evaluate("el => el.tagName").lower()
        input_type = (inp.get_attribute("type") or "").lower()
        placeholder = (inp.get_attribute("placeholder") or "").lower()
        aria = (inp.get_attribute("aria-label") or "").lower()
        name = (inp.get_attribute("name") or "").lower()
        field_id = inp.get_attribute("id") or ""
        selector = f"#{field_id}" if field_id else f'[name="{name}"]'
        if not selector:
            return

        # Skip already-filled.
        try:
            if inp.input_value():
                return
        except Exception:
            pass

        # Select dropdown.
        if tag == "select":
            opts = inp.query_selector_all("option")
            if len(opts) > 1:
                inp.select_option(index=1)
                HumanBehavior.random_delay(0.3, 0.7)
            return

        # File upload (resume).
        if input_type == "file":
            try:
                inp.set_input_files(resume_path)
                logger.info("Uploaded resume via generic form")
                HumanBehavior.random_delay(1, 2)
            except Exception:
                pass
            return

        # Checkbox.
        if input_type == "checkbox":
            try:
                inp.check()
            except Exception:
                pass
            HumanBehavior.random_delay(0.3, 0.6)
            return

        # Cover letter textarea.
        if tag == "textarea" or "cover letter" in (placeholder + " " + aria):
            HumanBehavior.type_human_like(self._page, cover_letter_text, selector=selector)
            return

        # Email.
        if input_type == "email" or "email" in (placeholder + " " + aria):
            HumanBehavior.type_human_like(self._page, candidate_email, selector=selector)
            return

        # Phone.
        if input_type == "tel" or "phone" in (placeholder + " " + aria):
            HumanBehavior.type_human_like(self._page, candidate_phone, selector=selector)
            return

        # Name.
        if "name" in (placeholder + " " + aria) and "file" not in name:
            HumanBehavior.type_human_like(self._page, candidate_name, selector=selector)
            return

        # URL / LinkedIn / portfolio.
        if input_type == "url" or "linkedin" in (placeholder + " " + aria):
            from app.config.settings import Settings  # noqa: PLC0415
            cfg = Settings()
            HumanBehavior.type_human_like(
                self._page, cfg.linkedin_url or "", selector=selector,
            )
            return

        # Generic text field.
        if input_type in ("text", "search", ""):
            HumanBehavior.type_human_like(
                self._page, "See attached resume", selector=selector,
            )
            return

    # ── Step 3: Submit button ────────────────────────────────

    def _click_submit_button(self) -> bool:
        """Find and click a submit-type button.

        Returns ``True`` if a button was clicked.
        """
        for selector in _SUBMIT_BUTTON_SELECTORS:
            btn = self._page.locator(selector).first
            if btn.is_visible(timeout=2_000):
                btn.click()
                logger.info("Clicked submit button: %s", selector)
                HumanBehavior.random_delay(2, 4)
                return True
        logger.warning("No submit button found — form may have been submitted inline")
        return False

    # ── Helpers ──────────────────────────────────────────────

    def _screenshot(self, name: str) -> None:
        """Save a debug screenshot."""
        try:
            path = _SCREENSHOT_DIR / f"generic_{name}.png"
            self._page.screenshot(path=str(path))
            logger.info("Screenshot saved: %s", path)
        except Exception:
            pass
