"""Intelligent form filler — detects and fills ANY job application form.

Works with Greenhouse, Lever, Ashby, LinkedIn, Indeed, Naukri, and custom ATS forms.
Matches form fields by semantic meaning (aria-label, placeholder, name, id).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

from playwright.async_api import ElementHandle, Page

from app.browser.human import Human

logger = logging.getLogger("job_automation_bot")

# ── Candidate profile ────────────────────────────────────────
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
    "github": "https://github.com/Sayeed613",
    "portfolio": "",
    "years_experience": "1",
    "current_company": "",
    "current_title": "Frontend Developer",
    "notice_period": "Immediate",
    "willing_to_relocate": "No",
    "work_authorization": "Yes, I am authorized to work in India",
    "salary_expectation": "Negotiable",
    "referral": "",
    "highest_education": "Bachelor of Computer Applications (BCA)",
    "university": "Sabarmathi University",
    "graduation_year": "2024",
    "cover_letter_available": "Yes",
    "remote_ok": "Yes",
    "timezone": "IST (UTC+5:30)",
    "languages": "English, Hindi, Kannada",
}

# ── Semantic field mapping ───────────────────────────────────
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

# ── Dropdown keyword matching ────────────────────────────────
# Maps candidate field keys to lists of option substrings to prefer.
_DROPDOWN_MATCH: dict[str, list[str]] = {
    "country": ["india", "in"],
    "years_experience": ["0-1", "1", "0 to 1", "fresher", "entry level", "junior", "<1"],
    "notice_period": ["immediate", "0 day", "0 day notice", "currently serving"],
    "state": ["karnataka", "bangalore"],
    "city": ["bangalore", "bengaluru"],
    "willing_to_relocate": ["no", "not willing"],
    "work_authorization": ["yes", "authorized", "authorised", "eligible", "work permit"],
    "highest_education": ["bachelor", "bca", "b.sc", "b.e", "b.tech", "graduate"],
    "graduation_year": ["2024", "2023", "2022"],
}


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
        await Human.delay(1.5, 3.0)

        # Get all interactive elements
        selector = (
            "input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='image']):not([type='file']), "
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
        elem: ElementHandle,
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

        # Build selector — try id, then name, then placeholder as fallback
        selector = (
            f"#{elem_id}" if elem_id
            else f"[name='{name}']" if name
            else f"[placeholder='{placeholder}']" if placeholder
            else None
        )
        if not selector:
            return  # Cannot target this element — skip

        # ── SELECT / DROPDOWN (smart) ──
        if tag == "select":
            await self._fill_select(elem, hint)
            return

        # ── RADIO BUTTONS ──
        if input_type == "radio":
            await self._fill_radio(page, elem, name, hint)
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
            if len(cover_letter_text) > 500:
                # Fast path: use fill() for long cover letters
                await elem.fill(cover_letter_text)
            else:
                # Slow path: type character by character for short fields
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

        # ── NO FALLBACK — silently skip unknown fields ────────
        # Do NOT type garbage into unknown fields — it causes form validation failures.

    # ── Smart select/dropdown filling ─────────────────────────

    async def _fill_select(self, elem: ElementHandle, hint: str) -> None:
        """Fill a select dropdown by matching hints against candidate values."""
        # Try to get the full option data (values + labels)
        options_data = await elem.evaluate("""\
            el => Array.from(el.options).map(o => ({
                value: o.value,
                text: o.textContent.trim().toLowerCase()
            }))
        """)

        if not options_data or len(options_data) <= 1:
            return

        # Determine which candidate field this dropdown maps to
        matched_field: Optional[str] = None
        for keys, field_key in _FIELD_MAP:
            if any(k in hint for k in keys):
                matched_field = field_key
                break

        # If we matched a field, try to find the best option
        if matched_field:
            preferred = _DROPDOWN_MATCH.get(matched_field, [])
            candidate_val = CANDIDATE.get(matched_field, "").lower()

            # Try candidate value first
            for opt in options_data:
                if candidate_val and candidate_val in opt["text"]:
                    await elem.select_option(opt["value"])
                    return

            # Try preferred substrings
            for kw in preferred:
                for opt in options_data:
                    if kw in opt["text"]:
                        await elem.select_option(opt["value"])
                        return

        # Fallback: pick index 1 (skip the default "Choose..." or "Select..." option)
        if len(options_data) > 1:
            await elem.select_option(options_data[1]["value"])

    # ── Radio button filling ─────────────────────────────────

    async def _fill_radio(self, page: Page, elem: ElementHandle, name: str, hint: str) -> None:
        """Fill a radio button group by choosing the appropriate option."""
        # Get all radio buttons in the same group
        radio_selector = f"input[type='radio'][name='{name}']"
        radios = await page.query_selector_all(radio_selector)
        if not radios:
            return

        # Read label text for each radio option
        for radio in radios:
            radio_id = await radio.get_attribute("id") or ""
            label = await page.evaluate(f"""\
                () => {{
                    const el = document.querySelector('label[for="{radio_id}"]');
                    return el ? el.textContent.trim().toLowerCase() : '';
                }}
            """)
            if not label:
                # Try to get the parent label
                parent_text = await radio.evaluate("""\
                    el => {{
                        const parent = el.closest('label');
                        return parent ? parent.textContent.trim().toLowerCase() : '';
                    }}
                """)
                label = parent_text or ""

            if not label:
                continue

            # For yes/no groups
            if any(w in hint for w in ["authorization", "authorised", "eligible",
                                        "work permit", "remote", "relocate"]):
                if "yes" in label:
                    await radio.check()
                    return
            # For experience/level groups
            elif any(w in hint for w in ["experience", "level", "years"]):
                if any(kw in label for kw in ["0-1", "1", "fresher", "entry", "junior"]):
                    await radio.check()
                    return
            # For education groups
            elif any(w in hint for w in ["education", "degree"]):
                if any(kw in label for kw in ["bachelor", "graduate", "bca", "b.sc"]):
                    await radio.check()
                    return
