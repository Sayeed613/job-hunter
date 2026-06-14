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
    "phone_alt": "+91 9008299613",
    "phone_noplus": "919008299613",
    "phone_local": "9008299613",
    "location": "Bangalore, Karnataka, India",
    "city": "Bangalore",
    "state": "Karnataka",
    "country": "India",
    "linkedin": "https://www.linkedin.com/in/sayeed-ahmed-613",
    "github": "https://github.com/Sayeed613",
    "portfolio": "https://sayeed613.github.io",
    "years_experience": "1",
    "current_company": "",
    "current_title": "Frontend Developer",
    "notice_period": "Immediate",
    "willing_to_relocate": "No, I am based in Bangalore",
    "work_authorization": "Yes, I am authorized to work in India. No visa sponsorship required.",
    "salary_expectation": "Negotiable",
    "referral": "LinkedIn",
    "highest_education": "Bachelor of Computer Applications (BCA)",
    "university": "Sabarmathi University",
    "graduation_year": "2024",
    "cover_letter_available": "Yes",
    "remote_ok": "Yes",
    "timezone": "IST (UTC+5:30)",
    "languages": "English, Hindi, Kannada",
    "pronouns": "He/Him",
    "start_date": "Immediate",
    "legally_authorized": "Yes",
    "sponsorship_required": "No",
    "gender": "",
    "race": "",
    "veteran": "",
    "disability": "",
}

# ── Semantic field mapping ───────────────────────────────────
# Maps (field hint keywords) -> CANDIDATE key
_FIELD_MAP: list[tuple[tuple[str, ...], str]] = [
    (("first", "fname", "firstname", "given"), "first_name"),
    (("last", "lname", "lastname", "surname", "family"), "last_name"),
    (("full", "your name", "applicant name", "candidate name"), "full_name"),
    (("email", "mail"), "email"),
    (("phone", "mobile", "tel", "contact number", "phone number"), "phone"),
    (("city", "current city"), "city"),
    (("state", "province", "region"), "state"),
    (("country",), "country"),
    (("address", "location", "where are you", "current location"), "location"),
    (("linkedin", "linkedin url", "linkedin profile"), "linkedin"),
    (("github", "git", "github url"), "github"),
    (("portfolio", "website", "personal site", "personal website", "url"), "portfolio"),
    (("experience", "years of exp", "yrs", "years of experience", "total experience"), "years_experience"),
    (("current company", "current employer", "employer"), "current_company"),
    (("current title", "current role", "designation", "job title"), "current_title"),
    (("notice", "availability", "joining", "start date", "when can you start"), "start_date"),
    (("salary", "ctc", "compensation", "expected", "salary expectation", "desired salary"), "salary_expectation"),
    (("relocate", "relocation", "willing to relocate"), "willing_to_relocate"),
    (("authorization", "authorised", "eligible", "work permit", "work authorization",
      "legally authorized", "legally eligible", "right to work"), "work_authorization"),
    (("sponsor", "visa", "sponsorship", "require visa", "need sponsorship"), "sponsorship_required"),
    (("referral", "how did you hear", "how did you find", "referral source"), "referral"),
    (("education", "degree", "highest education", "qualification"), "highest_education"),
    (("university", "college", "school", "institution"), "university"),
    (("graduation", "graduated", "grad year", "year of graduation"), "graduation_year"),
    (("pronoun", "gender pronoun", "preferred pronoun"), "pronouns"),
    (("language", "languages spoken"), "languages"),
    (("timezone", "time zone", "current timezone"), "timezone"),
    (("gender", "sex"), "gender"),
    (("race", "ethnicity"), "race"),
    (("veteran", "military", "armed forces"), "veteran"),
    (("disability", "disabled"), "disability"),
]

# ── Dropdown keyword matching ────────────────────────────────
# Maps candidate field keys to lists of option substrings to prefer.
# Ordered: more specific matches first to avoid false positives.
_DROPDOWN_MATCH: dict[str, list[str]] = {
    "country": ["india", "in"],
    "years_experience": ["1 year", "0-1", "<1", "entry", "junior", "fresher"],
    "notice_period": ["immediate", "0 day notice", "0 day", "now", "currently serving"],
    "state": ["karnataka", "bangalore"],
    "city": ["bangalore", "bengaluru"],
    "willing_to_relocate": ["no", "not willing", "i do not", "don't want", "cannot relocate"],
    "work_authorization": ["yes", "authorized", "authorised", "eligible", "work permit",
                           "i am authorized", "legally authorized"],
    "sponsorship_required": ["no", "not required", "don't require", "do not require",
                              "no sponsorship"],
    "highest_education": ["bachelor", "bca", "bachelor's", "graduate",
                           "b.sc", "b.tech", "b.e", "b.com", "b.a"],
    "graduation_year": ["2024", "2023", "2022"],
    "gender": ["prefer not", "decline", "male", "female"],
    "race": ["prefer not", "decline", "asian", "other"],
    "veteran": ["prefer not", "decline", "no", "not a veteran"],
    "disability": ["prefer not", "decline", "no", "none", "i don't have"],
    "pronouns": ["he/him", "him", "he", "male"],
    "languages": ["english", "hindi"],
    "remote_ok": ["yes", "remote", "fully remote"],
    "salary_expectation": ["negotiable", "open", "to be discussed", "not specified"],
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
        # Wrapped in try/except because the page/target can close if
        # the Apply button navigated away or the modal auto-closed.
        try:
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
        except Exception as e:
            logger.debug("Could not query file inputs (page may have navigated): %s", e)

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
        # Only fields explicitly marked as "cover letter" get the full CL.
        # Other textareas (like "Why", "Tell us about yourself") fall through
        # to _generate_safe_answer for a shorter contextual response.
        if tag == "textarea" or any(
            w in hint
            for w in ["cover", "letter", "message", "additional"]
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
                # For phone inputs: use local number (without +91 prefix) since
                # the country code is often handled by a separate select dropdown.
                if field_key == "phone":
                    local_val = CANDIDATE.get("phone_local")
                    if local_val:
                        value = local_val
                await Human.type_text(page, selector, value)
                return

        # ── TEXTAREA FALLBACK (not a cover letter, still needs filling) ──
        # Fields like "Why do you want to work here?", "Tell us about yourself"
        # are textareas that don't match the cover letter keywords.
        if tag == "textarea":
            answer = _generate_safe_answer(hint)
            if answer:
                await Human.type_text(page, selector, answer)
                return
            await Human.type_text(page, selector, "See attached resume for details")
            return

        # ── UNKNOWN TEXT FIELD — use safe generic response ──
        # Many forms have custom questions: "Why do you want to work here?",
        # "What is your experience with X?", "Tell us about yourself", etc.
        # We never leave them blank — always fill with something safe.
        if tag == "input" and input_type in ("text", ""):
            hint_lower = hint.lower()
            # Generate a safe contextual answer based on the field label
            answer = _generate_safe_answer(hint_lower)
            if answer:
                await Human.type_text(page, selector, answer)
                return
            # Absolute last resort: fill with a generic positive statement
            await Human.type_text(page, selector, "See attached resume for details")
            return

        # ── ABSOLUTE LAST RESORT (any unfilled input) ──
        # If we got here, something unusual. Fill with a brief safe value.
        if tag == "input":
            await Human.type_text(page, selector, "See resume for details")
            return

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

            # Try preferred keywords FIRST (most reliable for dropdowns
            # where candidate values are long sentences but options are short)
            for kw in preferred:
                for opt in options_data:
                    if kw in opt["text"]:
                        await elem.select_option(opt["value"])
                        return

            # Fallback: try matching candidate value against option text.
            # This naturally filters out long sentences since they won't
            # appear as substrings in short dropdown options.
            if candidate_val:
                for opt in options_data:
                    if candidate_val in opt["text"]:
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


# ── Generate safe answers for unknown form fields ────────────

def _generate_safe_answer(hint: str) -> str | None:
    """Return a safe, contextual answer for an unknown form field.

    Uses keyword matching against the field's hint text to generate
    a relevant response. This prevents blank fields on custom questions.

    Args:
        hint: Lowercased field hint (name + id + placeholder + aria-label).

    Returns:
        A safe answer string, or None if no match.
    """
    # Why / motivation questions
    if any(w in hint for w in ["why", "motivation", "interest", "passion", "drawn to"]):
        return "I am excited about this role because it aligns with my skills in frontend development and my passion for building great user experiences."

    # Experience / background questions
    if any(w in hint for w in ["tell us", "background", "about yourself", "introduce"]):
        return "I am a frontend developer with 1+ years of experience building web applications with React and Next.js. I enjoy creating responsive, user-friendly interfaces."

    # Skill questions
    if any(w in hint for w in ["skill", "proficient", "expertise", "technolog"]):
        return "React, Next.js, TypeScript, JavaScript, Tailwind CSS, Node.js, Python, FastAPI"

    # Project questions
    if any(w in hint for w in ["project", "portfolio", "work sample"]):
        return "Built an AI-powered job automation bot, an e-commerce dashboard, and a real-time chat application. Details in my resume."

    # Strength / weakness questions
    if any(w in hint for w in ["strength", "strong suit"]):
        return "Building responsive, user-friendly interfaces with modern React and attention to detail."
    if any(w in hint for w in ["weakness", "improve", "growth"]):
        return "I am actively deepening my backend knowledge with FastAPI and PostgreSQL to become a more well-rounded full-stack developer."

    # Availability / scheduling
    if any(w in hint for w in ["available", "interview", "time slot"]):
        return "Available immediately. Flexible with interview times."

    # Additional info
    if any(w in hint for w in ["additional", "anything else", "other", "comments"]):
        return "Please refer to my resume for full details. Thank you for your consideration!"

    # Diversity questions
    if any(w in hint for w in ["gender", "race", "ethnicity", "veteran", "disability"]):
        # Return empty string for EEO questions — user should fill these manually if desired
        return ""

    return None
