"""Application router — routes each job to the correct application strategy.

Strategies:
- LinkedIn → login (if credentials provided) → Easy Apply multi-step modal
- Greenhouse / Lever / Ashby / Indeed / Naukri / Wellfound → standard form fill
- Everything else → generic form detection and fill
"""

from __future__ import annotations

import logging
from typing import Optional

from app.browser.browser_manager import BrowserManager
from app.browser.form_filler import FormFiller
from app.browser.human import Human
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")


class ApplicationRouter:
    """Routes each job to the best application strategy based on the apply URL."""

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None
        self._initialised = False
        self._linkedin_email: str = ""
        self._linkedin_password: str = ""

    async def ensure_browser(
        self,
        headless: bool = True,
        linkedin_email: str = "",
        linkedin_password: str = "",
    ) -> None:
        """Lazy-init the browser with optional LinkedIn credentials."""
        self._linkedin_email = linkedin_email
        self._linkedin_password = linkedin_password
        if not self._initialised:
            self._browser = BrowserManager()
            await self._browser.launch(headless=headless)
            self._initialised = True

    @property
    def is_browser_ready(self) -> bool:
        """True if the browser is launched and ready for use."""
        return self._initialised and self._browser is not None

    def get_browser(self) -> BrowserManager:
        """Get the shared BrowserManager instance.

        Returns:
            The BrowserManager.

        Raises:
            RuntimeError: If the browser has not been launched yet.
        """
        if not self._browser or not self._initialised:
            raise RuntimeError("Browser not launched. Call ensure_browser() first.")
        return self._browser

    async def close_browser(self) -> None:
        if self._browser:
            await self._browser.close()
        self._initialised = False

    async def apply(
        self,
        job: Job,
        resume_path: str,
        cover_letter_text: str,
        cover_letter_pdf: str,
    ) -> bool:
        url = job.apply_url.lower()

        if "greenhouse.io" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "greenhouse")
        elif "jobs.lever.co" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "lever")
        elif "jobs.ashbyhq.com" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "ashby")
        elif "linkedin.com" in url:
            return await self._apply_linkedin(job, resume_path, cover_letter_text)
        elif "indeed.com" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "indeed")
        elif "naukri.com" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "naukri")
        elif "wellfound.com" in url or "angel.co" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "wellfound")
        else:
            return await self._apply_generic(job, resume_path, cover_letter_text)

    async def _login_linkedin(self) -> bool:
        """Log into LinkedIn using stored credentials. Returns True if already logged in or login succeeded."""
        if not self._linkedin_email or not self._linkedin_password:
            logger.info("No LinkedIn credentials — trying Easy Apply without login")
            return False

        if not self._browser:
            return False

        page = await self._browser.new_page()
        try:
            await page.goto("https://www.linkedin.com/login", wait_until="networkidle")
            await Human.delay(2, 4)

            # Check if already logged in
            if "/feed" in page.url or "/mynetwork" in page.url:
                logger.info("Already logged in to LinkedIn")
                return True

            # Fill email
            email_el = await page.query_selector("#username")
            if not email_el:
                email_el = await page.query_selector("input[name='session_key']")
            if email_el:
                await Human.type_text(page, "#username, input[name='session_key']", self._linkedin_email)
                await Human.delay(1, 2)

            # Fill password
            pw_el = await page.query_selector("#password")
            if not pw_el:
                pw_el = await page.query_selector("input[name='session_password']")
            if pw_el:
                await Human.type_text(page, "#password, input[name='session_password']", self._linkedin_password)
                await Human.delay(1, 2)

            # Click sign in
            signin_btn = await page.query_selector("button[aria-label='Sign in'], button[type='submit']")
            if signin_btn:
                await signin_btn.click()
                await Human.delay(3, 6)

            # Check result
            if "/feed" in page.url or "/mynetwork" in page.url or "/checkpoint" in page.url:
                logger.info("LinkedIn login successful")
                return True
            else:
                logger.warning("LinkedIn login may have failed or 2FA required — continuing anyway")
                await Human.screenshot(page, "linkedin_login_status")
                return False
        except Exception as e:
            logger.error("LinkedIn login error: %s", e)
            return False
        finally:
            await page.close()

    async def _apply_linkedin(
        self,
        job: Job,
        resume_path: str,
        cover_letter_text: str,
    ) -> bool:
        """Apply via LinkedIn Easy Apply with optional login."""
        if not self._browser:
            logger.error("Browser not initialised")
            return False

        # Try logging in first (non-blocking if no credentials)
        await self._login_linkedin()

        page = await self._browser.new_page()
        try:
            await page.goto(job.apply_url, wait_until="networkidle")
            await Human.delay(2, 4)

            # Click Easy Apply button
            btn = await page.query_selector(
                "button.jobs-apply-button, .jobs-s-apply button, "
                "button:has-text('Easy Apply')"
            )
            if not btn:
                logger.warning("Easy Apply button not found on %s", job.apply_url)
                return False

            await Human.click(page, "button.jobs-apply-button, button:has-text('Easy Apply')")
            await Human.delay(2, 3)

            filler = FormFiller()

            # Multi-step form — loop until Submit
            for step in range(8):
                await Human.delay(1, 2)
                await filler.fill_form(page, resume_path, cover_letter_text)

                # Upload resume if prompted
                file_inputs = await page.query_selector_all("input[type='file']")
                for fi in file_inputs:
                    try:
                        await fi.set_input_files(resume_path)
                        await Human.delay(1, 2)
                    except Exception:
                        continue

                # Check for Submit button
                submit_btn = await page.query_selector(
                    "button[aria-label='Submit application'], button:has-text('Submit application')"
                )
                if submit_btn:
                    await Human.click(
                        page,
                        "button[aria-label='Submit application'], button:has-text('Submit application')",
                    )
                    await Human.delay(3, 5)
                    await Human.screenshot(page, f"linkedin_{job.job_id}_success")
                    return True

                # Try Next / Review button
                next_btn = await page.query_selector(
                    "button[aria-label='Continue to next step'], "
                    "button[aria-label='Review your application'], "
                    "button:has-text('Next')"
                )
                if next_btn:
                    await next_btn.click()
                    await Human.delay(1, 2)
                else:
                    break

            logger.warning("LinkedIn Easy Apply: reached end without submitting")
            return False

        except Exception as e:
            await Human.screenshot(page, f"linkedin_{job.job_id}_error")
            logger.error("LinkedIn apply failed: %s", e)
            return False
        finally:
            await page.close()

    async def _apply_generic(
        self, job: Job, resume_path: str, cover_letter_text: str,
    ) -> bool:
        if not self._browser:
            return False
        page = await self._browser.new_page()
        try:
            await page.goto(job.apply_url, wait_until="networkidle")
            await Human.delay(2, 4)
            await Human.scroll(page, "down", 2)

            apply_selectors = [
                "text=Apply Now", "text=Apply", "text=Quick Apply",
                "text=Easy Apply", "text=Apply for this job",
                "[data-testid*='apply']", ".apply-btn", "#apply-button",
                "a[href*='apply']", "button:has-text('Apply')",
            ]
            clicked = False
            for sel in apply_selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await Human.click(page, sel)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                logger.warning("No apply button on %s", job.apply_url)
                return False

            await Human.delay(2, 4)
            filler = FormFiller()
            await filler.fill_form(page, resume_path, cover_letter_text)
            await Human.delay(1, 2)

            submit_selectors = [
                "button[type='submit']", "input[type='submit']",
                "text=Submit", "text=Submit Application",
                "text=Send Application", "text=Complete Application",
            ]
            for sel in submit_selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await Human.click(page, sel)
                        break
                except Exception:
                    continue

            await Human.delay(3, 5)
            await Human.screenshot(page, f"generic_{job.job_id}_success")
            return True
        except Exception as e:
            await Human.screenshot(page, f"generic_{job.job_id}_error")
            logger.error("Generic apply failed: %s", e)
            return False
        finally:
            await page.close()

    async def _standard_apply(
        self, job: Job, resume_path: str, cover_letter_text: str, label: str,
    ) -> bool:
        if not self._browser:
            return False
        page = await self._browser.new_page()
        try:
            await page.goto(job.apply_url, wait_until="networkidle")
            await Human.delay(2, 4)
            filler = FormFiller()
            await filler.fill_form(page, resume_path, cover_letter_text)
            await Human.delay(1, 2)

            for sel in ["button[type='submit']", "input[type='submit']",
                        "text=Submit", "text=Submit Application", "text=Send Application"]:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await Human.click(page, sel)
                        break
                except Exception:
                    continue

            await Human.delay(3, 5)
            await Human.screenshot(page, f"{label}_{job.job_id}_success")
            return True
        except Exception as e:
            await Human.screenshot(page, f"{label}_{job.job_id}_error")
            logger.error("%s apply failed: %s", label, e)
            return False
        finally:
            await page.close()
