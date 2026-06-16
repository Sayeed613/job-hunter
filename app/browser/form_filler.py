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

from app.ai.client import AIClient
from app.browser.human import Human
from app.telegram.interaction import TelegramInteraction
from app.telegram.learner import LearnedProfile
from app.telegram.notifier import TelegramNotifier

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
    "years_experience_total": "1.5",
    "current_company": "Actobiz",
    "current_title": "Frontend Developer",
    "internship_company": "Tekiarz",
    "internship_duration": "6 months",
    "internship_role": "Frontend Developer Intern",
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
    "gender": "Male",
    "race": "",
    "veteran": "No, I am not a veteran",
    "disability": "No, I do not have a disability",
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
    (("country code", "dial code", "phone code", "phone country"), "country"),
    (("address", "location", "where are you", "current location"), "location"),
    (("linkedin", "linkedin url", "linkedin profile"), "linkedin"),
    (("github", "git", "github url"), "github"),
    (("portfolio", "website", "personal site", "personal website", "url"), "portfolio"),
    (("experience", "years of exp", "yrs", "years of experience", "total experience"), "years_experience"),
    (("current company", "current employer", "employer"), "current_company"),
    (("internship", "intern", "internship company", "past internship"), "internship_company"),
    (("internship duration", "internship period"), "internship_duration"),
    (("internship role", "internship title"), "internship_role"),
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
    "internship_company": ["tek", "tekiartz"],
    "internship_role": ["frontend", "intern"],
    "pronouns": ["he/him", "him", "he", "male"],
    "languages": ["english", "hindi"],
    "remote_ok": ["yes", "remote", "fully remote"],
    "salary_expectation": ["negotiable", "open", "to be discussed", "not specified"],
}


class FormFiller:
    """Detects form type and fills all fields with candidate data.

    Uses keyword matching first, then falls back to AI-powered selection
    for dropdown options the bot can't resolve via rules alone.

    Usage:
        filler = FormFiller()
        await filler.fill_form(page, resume_path, cover_letter_text)

        # With AI-powered dropdown selection:
        ai = AIClient()
        tg = TelegramNotifier(token="...", chat_id="...")
        filler = FormFiller(ai_client=ai, notifier=tg)
        await filler.fill_form(page, resume_path, cover_letter_text)
    """

    def __init__(
        self,
        ai_client: Optional[AIClient] = None,
        notifier: Optional[TelegramNotifier] = None,
        interaction: Optional[TelegramInteraction] = None,
        learner: Optional[LearnedProfile] = None,
    ) -> None:
        """Initialise the form filler.

        Args:
            ai_client: Optional AI client for intelligent dropdown selection.
            notifier: Optional Telegram notifier for sending AI decisions.
            interaction: Optional Telegram interaction for asking the user
                         when AI doesn't know a dropdown option.
            learner: Optional learned profile for persisting user answers.
        """
        self._ai = ai_client
        self._notifier = notifier
        self._interaction = interaction
        self._learner = learner or LearnedProfile()

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
                    # Check label text, aria-label, id, AND name for keywords
                    fi_info = await fi.evaluate("""el => ({
                        label: el.closest('label')?.textContent?.toLowerCase() || '',
                        ariaLabel: el.getAttribute('aria-label')?.toLowerCase() || '',
                        id: el.id?.toLowerCase() || '',
                        name: el.getAttribute('name')?.toLowerCase() || ''
                    })""")
                    combined = f"{fi_info['label']} {fi_info['ariaLabel']} {fi_info['id']} {fi_info['name']}"
                    if any(w in combined for w in ["resume", "cv", "upload", "document"]):
                        print(f"   [UPLOAD] Found resume file input (id={fi_info['id']}) — uploading {resume_path}")
                        await fi.set_input_files(resume_path)
                        await Human.delay(1.0, 2.5)
                except Exception as e:
                    logger.debug("File upload failed: %s", e)
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

        # ── COMBOBOX (custom JS dropdown) — check BEFORE selector ──
        # Greenhouse/Lever/Ashby use <input role="combobox"> instead of native <select>.
        # React comboboxes often have no id/name/placeholder, so we must check
        # the role BEFORE the selector-based checks below.
        role = (await elem.get_attribute("role") or "").lower()
        aria_haspopup = (await elem.get_attribute("aria-haspopup") or "").lower()
        if "combobox" in role or "listbox" in aria_haspopup:
            await self._fill_combobox(page, elem, hint)
            return

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
            # Use fill() for speed — ATS systems don't detect keystroke timing
            await elem.fill(cover_letter_text)
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

    # ── Combobox (custom JS dropdown) filling ─────────────────

    async def _fill_combobox(
        self, page: Page, elem: ElementHandle, hint: str,
    ) -> None:
        """Fill a custom combobox by clicking, waiting for options, and selecting the best match.

        Greenhouse uses <input role="combobox"> elements for dropdowns.
        Clicking them opens a popup list of options.
        """
        # Determine which candidate field this dropdown maps to
        matched_field: Optional[str] = None
        for keys, field_key in _FIELD_MAP:
            if any(k in hint for k in keys):
                matched_field = field_key
                break

        candidate_val = ""
        preferred: list[str] = []
        candidate_lower = ""

        if matched_field:
            candidate_val = CANDIDATE.get(matched_field, "") or ""
            preferred = _DROPDOWN_MATCH.get(matched_field, []) or []
            candidate_lower = candidate_val.lower()
            print(f"   [COMBOBOX] Filling '{matched_field}', looking for '{candidate_val}'")
        else:
            print(f"   [COMBOBOX] No field match for hint: {hint[:60]} — will pick first option")

        # Click the combobox to open the dropdown
        await elem.click()
        print(f"   [COMBOBOX] Clicked, waiting for options...")
        # react-select renders options asynchronously, wait for the menu to appear
        await asyncio.sleep(1.5)

        # Find the open dropdown options
        # react-select uses: div.select__menu > div.select__option
        # Other implementations: ul[role='listbox'] li[role='option']
        option_selectors = [
            ".select__menu .select__option",       # react-select (Greenhouse)
            ".select__option",                     # react-select (shorter)
            "[role='listbox'] > [role='option']",  # direct child ARIA
            "[role='listbox'] [role='option']",    # nested ARIA
            "[role='option']",                     # broad ARIA fallback
            "li.select-option",
            ".dropdown-option",
            "[class*='option']:not(input)",
        ]
        options = None
        selected_sel = ""
        for sel in option_selectors:
            try:
                options = await page.query_selector_all(sel)
                if options and len(options) > 0:
                    selected_sel = sel
                    break
            except Exception:
                continue

        if not options:
            print(f"   [COMBOBOX] No options found via any selector for: {hint[:40]}")
            return

        print(f"   [COMBOBOX] Found {len(options)} options via '{selected_sel}'")

        # Try preferred keywords first (most specific first)
        # Use WHOLE WORD matching: "india" should match "India" but NOT "Indian Ocean"
        if matched_field:
            for kw in preferred:
                for opt in options:
                    try:
                        text = (await opt.inner_text() or "").lower().strip()
                    except Exception:
                        continue
                    if text and kw in text.split():
                        print(f"   [COMBOBOX] Selecting option matching '{kw}': '{text[:30]}'")
                        await opt.click()
                        await asyncio.sleep(0.5)
                        return

            # Try candidate value (also whole word for more accuracy)
            for opt in options:
                try:
                    text = (await opt.inner_text() or "").lower().strip()
                except Exception:
                    continue
                if text and candidate_lower in text.split():
                    print(f"   [COMBOBOX] Selecting option matching candidate: '{text[:30]}'")
                    await opt.click()
                    await asyncio.sleep(0.5)
                    return

            # Fallback: substring match (catches cases like "I am authorized" in "Yes, I am authorized")
            for kw in preferred:
                for opt in options:
                    try:
                        text = (await opt.inner_text() or "").lower().strip()
                    except Exception:
                        continue
                    if text and kw in text:
                        print(f"   [COMBOBOX] Substring fallback matching '{kw}': '{text[:30]}'")
                        await opt.click()
                        await asyncio.sleep(0.5)
                        return

        # ── AI-powered fallback: pick the BEST option instead of just the first ──
        # Try AI when all keyword matching fails (matched or unmatched field)
        option_texts: list[str] = []
        for opt in options:
            try:
                t = (await opt.inner_text() or "").strip()
                if t:
                    option_texts.append(t)
            except Exception:
                continue

        if option_texts:
            selected = await self._ai_select_option(hint, option_texts, matched_field, candidate_val)
            if selected:
                # Find the option element matching the selected text
                for opt in options:
                    try:
                        text = (await opt.inner_text() or "").strip()
                        if text == selected:
                            print(f"   [COMBOBOX] AI selected: '{selected[:40]}'")
                            await opt.click()
                            await asyncio.sleep(0.5)
                            return
                    except Exception:
                        continue

        # ── Telegram ask flow: ask user when AI doesn't know ──
        if self._interaction and self._interaction.available:
            field_key = matched_field or hint[:80]
            # Check learned profile first
            learned_answer = self._learner.get(field_key)
            if learned_answer:
                print(f"   [LEARNED] Using saved answer '{learned_answer[:40]}' for '{field_key[:40]}'")
                for opt in options:
                    try:
                        text = (await opt.inner_text() or "").strip()
                        if text == learned_answer:
                            await opt.click()
                            await asyncio.sleep(0.5)
                            return
                    except Exception:
                        continue

            # Ask user via Telegram
            print(f"   [TELEGRAM] Asking user: '{field_key[:40]}' ({len(options)} options)")
            reply = await self._interaction.ask(
                question=f"Choose option for: {field_key[:60]}",
                options=option_texts if option_texts else ["(no options)"],
                timeout=300,  # 5 minute timeout
            )
            if reply:
                print(f"   [TELEGRAM] User replied: '{reply[:40]}'")
                # Try matching by number first ("1", "2", etc.)
                if reply.isdigit():
                    idx = int(reply) - 1
                    if 0 <= idx < len(option_texts):
                        selected_text = option_texts[idx]
                        for opt in options:
                            try:
                                text = (await opt.inner_text() or "").strip()
                                if text == selected_text:
                                    print(f"   [TELEGRAM] Selected option {reply}: '{selected_text[:40]}'")
                                    self._learner.set(field_key, selected_text)
                                    await opt.click()
                                    await asyncio.sleep(0.5)
                                    return
                            except Exception:
                                continue
                # Try matching by text
                reply_lower = reply.lower()
                for opt in options:
                    try:
                        text = (await opt.inner_text() or "").strip()
                        if text.lower() == reply_lower or reply_lower in text.lower() or text.lower() in reply_lower:
                            print(f"   [TELEGRAM] Matched reply to option: '{text[:40]}'")
                            self._learner.set(field_key, text)
                            await opt.click()
                            await asyncio.sleep(0.5)
                            return
                    except Exception:
                        continue
                print(f"   [TELEGRAM] Could not match reply '{reply[:40]}' to any option")
            else:
                print(f"   [TELEGRAM] No reply received (timeout) — falling back")

        # Absolute last resort: click the first non-empty option
        for opt in options:
            try:
                text = (await opt.inner_text() or "").strip()
                if text:
                    print(f"   [COMBOBOX] First-option fallback: '{text[:30]}'")
                    await opt.click()
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue
        
        print(f"   [COMBOBOX] All options empty or unclickable")

    # ── AI-powered option selection ──────────────────────────

    async def _ai_select_option(
        self,
        field_hint: str,
        options: list[str],
        matched_field: Optional[str],
        candidate_val: str,
    ) -> Optional[str]:
        """Ask the AI to pick the best dropdown option for this candidate.

        Args:
            field_hint: The concatenated hint (name + id + placeholder + aria-label).
            options: List of option texts to choose from.
            matched_field: The matched CANDIDATE key (e.g. "country", "gender"), or None.
            candidate_val: The candidate's value for this field (if matched).

        Returns:
            The best-matching option text, or None if AI is unavailable.
        """
        if not self._ai or not self._ai.is_available:
            return None

        # Build a compact context for the AI
        field_label = matched_field or field_hint[:80]
        if matched_field and candidate_val:
            user_context = (
                f"You are filling in a job application form. "
                f"The form field is: **{field_label}**\n"
                f"The candidate's relevant info: **{candidate_val}**\n\n"
                f"Available options:\n"
                + "\n".join(f"{i+1}. {o}" for i, o in enumerate(options))
                + "\n\nRespond with ONLY the exact option text (no number, no punctuation)."
            )
        else:
            # Build candidate context dynamically from CANDIDATE dict
            cand_name = CANDIDATE.get("full_name", "the candidate")
            cand_title = CANDIDATE.get("current_title", "a professional")
            cand_city = CANDIDATE.get("city", "")
            cand_country = CANDIDATE.get("country", "")
            cand_location = f" based in {cand_city}, {cand_country}" if cand_city and cand_country else ""
            # Include demographic info for unknown fields (EEO/diversity questions)
            cand_gender = CANDIDATE.get("gender", "")
            cand_veteran = CANDIDATE.get("veteran", "")
            cand_disability = CANDIDATE.get("disability", "")
            cand_race = CANDIDATE.get("race", "")
            additional_context = ""
            if any(v for v in [cand_gender, cand_veteran, cand_disability, cand_race]):
                details = []
                if cand_gender: details.append(f"Gender: {cand_gender}")
                if cand_veteran: details.append(f"Veteran: {cand_veteran}")
                if cand_disability: details.append(f"Disability: {cand_disability}")
                if cand_race: details.append(f"Race/Ethnicity: {cand_race}")
                additional_context = f"\nCandidate demographics:\n" + "\n".join(details) + "\n"
            user_context = (
                f"You are filling in a job application form for {cand_name}, "
                f"a {cand_title}{cand_location}.{additional_context}"
                f"Field description: **{field_label}**\n\n"
                f"Available options:\n"
                + "\n".join(f"{i+1}. {o}" for i, o in enumerate(options))
                + f"\n\nBased on {cand_name.split()[0]}'s profile, which option is best? "
                "Respond with ONLY the exact option text (no number, no punctuation)."
            )

        try:
            selected = await self._ai.chat(
                system_prompt=(
                    "You are a precise job application assistant. Your job is to select "
                    "the most appropriate dropdown option for a candidate based on their "
                    "profile and the field description.\n"
                    "- Output ONLY the exact option text — no numbers, quotes, or formatting.\n"
                    "- If multiple options could fit, choose the most accurate one.\n"
                    "- Use the candidate's provided info as the source of truth — do not override "
                    "it with 'Prefer not to say' or 'Decline to answer' unless the candidate's "
                    "info is empty or explicitly says so."
                ),
                user_prompt=user_context,
                temperature=0.1,
                max_tokens=100,
            )

            selected_text = selected.strip().strip('"').strip("'")

            # Verify the selected option actually exists in the list
            for opt in options:
                if selected_text.lower() == opt.lower() or selected_text in opt or opt in selected_text:
                    exact_match = opt
                    # Send Telegram notification (best-effort, won't crash form fill)
                    if self._notifier:
                        try:
                            await self._notifier.send_message(
                                f"🤖 *AI Dropdown Selection*\n"
                                f"📋 *Field:* {field_label}\n"
                                f"✅ *Selected:* {exact_match[:50]}"
                            )
                        except Exception:
                            pass
                    return exact_match

            # If AI returned something not in the list, fall through
            print(f"   [AI] Response '{selected_text}' not in options, falling back")
            return None

        except Exception as e:
            print(f"   [AI] Selection failed: {e}")
            return None

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

        # Define candidate_val in scope for all code paths
        candidate_val = ""

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

        # ── AI-powered fallback ──
        option_texts = [o["text"] for o in options_data if o["text"].strip()]
        if option_texts:
            selected = await self._ai_select_option(hint, option_texts, matched_field, candidate_val)
            if selected:
                for opt in options_data:
                    if opt["text"] == selected.lower() or selected.lower() in opt["text"] or opt["text"] in selected.lower():
                        await elem.select_option(opt["value"])
                        print(f"   [NATIVE SELECT] AI selected: '{selected[:40]}'")
                        return

        # ── Telegram ask flow: ask user when AI doesn't know ──
        if self._interaction and self._interaction.available:
            field_key = matched_field or hint[:80]
            # Check learned profile first
            learned_answer = self._learner.get(field_key)
            if learned_answer:
                print(f"   [LEARNED] Using saved answer '{learned_answer[:40]}' for '{field_key[:40]}'")
                for opt in options_data:
                    if opt["text"] == learned_answer.lower() or learned_answer.lower() in opt["text"] or opt["text"] in learned_answer.lower():
                        await elem.select_option(opt["value"])
                        return

            # Ask user via Telegram
            print(f"   [TELEGRAM] Asking user about '{field_key[:40]}'...")
            reply = await self._interaction.ask(
                question=f"Choose option for: {field_key[:60]}",
                options=option_texts,
                timeout=300,
            )
            if reply:
                print(f"   [TELEGRAM] User replied: '{reply[:40]}'")
                # Try matching by number
                if reply.isdigit():
                    idx = int(reply) - 1
                    if 0 <= idx < len(option_texts):
                        selected_text = option_texts[idx]
                        for opt in options_data:
                            if opt["text"] == selected_text.lower() or selected_text.lower() in opt["text"] or opt["text"] in selected_text.lower():
                                print(f"   [TELEGRAM] Selected #{reply}: '{selected_text[:40]}'")
                                self._learner.set(field_key, selected_text)
                                await elem.select_option(opt["value"])
                                return
                # Try matching by text
                reply_lower = reply.lower()
                for opt in options_data:
                    if opt["text"] == reply_lower or reply_lower in opt["text"] or opt["text"] in reply_lower:
                        print(f"   [TELEGRAM] Matched reply to option: '{opt['text'][:40]}'")
                        self._learner.set(field_key, opt["text"])
                        await elem.select_option(opt["value"])
                        return
                print(f"   [TELEGRAM] Could not match reply '{reply[:40]}' to any option")
            else:
                print(f"   [TELEGRAM] No reply received (timeout) — falling back")

        # Last resort: pick index 1 (skip the default "Choose..." or "Select..." option)
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
