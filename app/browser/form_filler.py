"""Intelligent form filler — detects and fills ANY job application form.

Works with Greenhouse, Lever, Ashby, LinkedIn, Indeed, Naukri, and custom ATS forms.
Matches form fields by semantic meaning (aria-label, placeholder, name, id).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

from playwright.async_api import Page

from app.browser.human import Human

logger = logging.getLogger("job_automation_bot")

# ── Candidate profile (hardcoded from spec Section 1) ────────
CANDIDATE = {
    "first_name": "Sayeed",
    "last_name": "Ahmed",
    "full_name": "Sayeed Ahmed",
    "email": "sayeedahmed90082@gmail.com",
    "phone": "+919008299613",
    "location": "Bangalore, Karnataka, India",
    "city": "Bangalore",
    "state": "Karnataka",
    "country": "India",
    "linkedin": "",
    "github": "",
    "portfolio": "",
    "years_experience": "",
    "current_company": "",
    "current_title": "",
    "notice_period": "Immediate",
    "willing_to_relocate": "No",
    "work_authorization": "Yes, I am authorized to work in India",
    "salary_expectation": "Negotiable",
    "referral": "",
}

# ── Semantic field mapping ───────────────────────────────────
# Keys are tuples of substrings to match against
# (name, id, placeholder, aria-label concatenated).
_FIELD_MAP: list[tuple[tuple[str, ...], str]] = [
    (("first", "fname", "firstname", "given"), "first_name"),
    (("last", "lname", "lastname", "surname", "family"), "last_name"),
    (("full", "your name", "applicant name"), "full_name"),
    (("email", "mail"), "email"),
    (("phone", "mobile", "tel", "contact number"), "phone"),
    (("city", "current city"), "city"),
    (("state", "province"), "state"),
    (("country",), "country"),
    (("address", "location", "where are you"), "location"),
    (("linkedin",), "linkedin"),
    (("github", "git"), "github"),
    (("portfolio", "website", "personal site"), "portfolio"),
    (("experience", "years of exp", "yrs"), "years_experience"),
    (("current company", "current employer", "employer"), "current_company"),
    (("current title", "current role", "designation"), "current_title"),
    (("notice", "availability", "joining"), "notice_period"),
    (("salary", "ctc", "compensation", "expected"), "salary_expectation"),
    (("relocate", "relocation"), "willing_to_relocate"),
    (("authorization", "authorised", "eligible", "work permit"), "work_authorization"),
    (("referral", "how did you hear"), "referral"),
]


class FormFiller:
    """Detects form type and fills all fields with candidate data.

    Usage:
        filler = FormFiller()
        await filler.fill_form(page, resume_path, cover_letter_text)
    """

    async def fill_form(
        self,
        page: Page,
        resume_path: str,
        cover_letter_text: str,
    ) -> None:
        """Main entry point — automatically fills every field in the form.

        Args:
            page: Playwright page with the form loaded.
            resume_path: Path to the tailored resume file for upload.
            cover_letter_text: Cover letter text for textarea fields.
        """
        # Wait for the form to stabilize
        await Human.delay(1.5, 3.0)

        # Get all interactive elements
        selector = (
            "input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='image']), "
            "textarea, select"
        )
        inputs = await page.query_selector_all(selector)

        for elem in inputs:
            try:
                await self._fill_element(page, elem, resume_path, cover_letter_text)
                await Human.delay(0.3, 0.9)
            except Exception as e:
                logger.debug("Could not fill element: %s", e)
                continue

        # Handle file upload inputs separately
        file_inputs = await page.query_selector_all("input[type='file']")
        for fi in file_inputs:
            try:
                label = await fi.evaluate(
                    "el => el.closest('label')?.textContent || ''"
                )
                if any(w in label.lower() for w in ["resume", "cv", "upload", "document"]):
                    await fi.set_input_files(resume_path)
                    await Human.delay(1.0, 2.5)
            except Exception:
                continue

    async def _fill_element(
        self,
        page: Page,
        elem: Page,
        resume_path: str,
        cover_letter_text: str,
    ) -> None:
        """Fill a single form element based on its type and semantic hints."""
        tag = await elem.evaluate("el => el.tagName.toLowerCase()")
        input_type = (await elem.get_attribute("type") or "text").lower()
        name = (await elem.get_attribute("name") or "").lower()
        elem_id = (await elem.get_attribute("id") or "").lower()
        placeholder = (await elem.get_attribute("placeholder") or "").lower()
        aria = (await elem.get_attribute("aria-label") or "").lower()
        hint = f"{name} {elem_id} {placeholder} {aria}"

        # Skip if already filled
        try:
            val = await elem.input_value()
            if val.strip():
                return
        except Exception:
            pass

        # Build selector
        selector = f"#{elem_id}" if elem_id else f"[name='{name}']" if name else None
        if not selector:
            return

        # ── FILE UPLOAD ──
        if input_type == "file":
            if any(w in hint for w in ["resume", "cv", "document"]):
                await elem.set_input_files(resume_path)
            return

        # ── SELECT / DROPDOWN ──
        if tag == "select":
            options = await elem.evaluate(
                "el => Array.from(el.options).map(o => o.value)"
            )
            if len(options) > 1:
                await elem.select_option(options[1])
            return

        # ── CHECKBOX ──
        if input_type == "checkbox":
            is_checked = await elem.is_checked()
            if not is_checked and any(
                w in hint for w in ["agree", "terms", "consent", "authorize"]
            ):
                await elem.check()
            return

        # ── TEXTAREA / COVER LETTER ──
        if tag == "textarea" or any(
            w in hint
            for w in ["cover", "letter", "message", "additional", "why", "motivation",
                       "about yourself", "introduction"]
        ):
            await elem.click()
            await asyncio.sleep(random.uniform(0.3, 0.7))
            await elem.fill("")
            for ch in cover_letter_text:
                await page.keyboard.type(ch, delay=random.randint(30, 100))
            return

        # ── TEXT / EMAIL / TEL FIELDS ──
        for keys, field_key in _FIELD_MAP:
            value = CANDIDATE.get(field_key)
            if not value:
                continue
            if any(k in hint for k in keys):
                await Human.type_text(page, selector, value)
                return

        # ── FALLBACK: generic text input ──
        if input_type in ("text", "email", "tel", "url", "search", "number"):
            await Human.type_text(page, selector, "Interested in this opportunity")
