"""Application router — routes each job to the correct application strategy.

Strategies:
- LinkedIn → login session → Easy Apply multi-step modal
- Wellfound / Naukri → login session → standard form fill
- Greenhouse / Lever / Ashby / Indeed → standard form fill
- Everything else → generic form detection and fill

Platforms that require login use the per-platform storage_state saved
via ``--relogin <platform>``.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.browser.browser_manager import BrowserManager
from app.browser.form_filler import FormFiller
from app.browser.human import Human
from app.browser.session import LoginSession
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

# ── Platform → session name mapping ─────────────────────────
_PLATFORM_SESSION_MAP: dict[str, str] = {
    "linkedin": "linkedin",
    "naukri": "naukri",
    "wellfound": "wellfound",
    "workatastartup": "workatastartup",
}


class ApplicationRouter:
    """Routes each job to the best application strategy based on the apply URL.

    Uses the shared BrowserManager to create platform-specific contexts
    with saved storage_state for login-required platforms.
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None
        self._initialised = False
        self._session = LoginSession()

    async def ensure_browser(
        self,
        headless: bool = True,
        linkedin_email: str = "",
        linkedin_password: str = "",
    ) -> None:
        """Lazy-init the browser with anti-detection.

        Credentials are no longer needed here — login sessions are loaded
        from storage_state files saved via ``--relogin``.
        """
        if not self._initialised:
            self._browser = BrowserManager()
            await self._browser.launch(headless=headless)
            self._initialised = True
            logger.info(
                "Available login sessions: %s",
                self._session.list_available_sessions() or "none",
            )

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

    # ── Session helpers ──────────────────────────────────────

    def _platform_for_url(self, url: str) -> str | None:
        """Determine which platform session to use for a given URL."""
        for domain, platform in [
            ("linkedin.com", "linkedin"),
            ("naukri.com", "naukri"),
            ("wellfound.com", "wellfound"),
            ("angel.co", "wellfound"),
            ("workatastartup.com", "workatastartup"),
        ]:
            if domain in url:
                return platform
        return None

    def _has_platform_session(self, platform: str) -> bool:
        """Check if a saved session exists for *platform*."""
        return self._session.has_session(platform)

    # ── Shared helpers ───────────────────────────────────────

    @staticmethod
    async def _click_apply_button(page) -> bool:
        """Try to click the Apply/Candidate button on a job page.

        Uses CSS selectors first, then a JavaScript fallback that
        scans all visible elements for 'Apply' text.

        Returns:
            True if a button was found and clicked.
        """
        apply_selectors = [
            # Greenhouse-specific: anchor tag styled as button
            "a.button:has-text('Apply')",
            ".button:has-text('Apply')",
            "a:has-text('Apply for this job')",
            "button:has-text('Apply')",
            "a:has-text('Apply')",
            "[class*='apply']:has-text('Apply')",
            "a[href*='apply']",
            "button[type='submit']",
            "[data-testid*='apply']",
            ".apply-btn",
            "#apply-button",
            ":has-text('Apply for this job')",
            ":has-text('Apply Now')",
            ":has-text('Apply')",
        ]
        for sel in apply_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    logger.info("Clicked Apply button via CSS: %s", sel)
                    await btn.scroll_into_view_if_needed()
                    await Human.delay(0.3, 0.7)
                    await btn.click()
                    await Human.delay(0.5, 1.2)
                    return True
            except Exception:
                continue
        # JavaScript fallback: click any visible element with 'Apply' text
        try:
            clicked = await page.evaluate("""() => {
                const candidates = document.querySelectorAll('a, button, span');
                for (const el of candidates) {
                    const text = (el.textContent || '').toLowerCase().trim();
                    if (text.includes('apply') && el.offsetParent !== null) {
                        el.scrollIntoView({behavior: 'instant', block: 'center'});
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                logger.info("Clicked Apply button via JS fallback")
                return True
        except Exception:
            pass
        logger.debug("No Apply button found on page")
        return False

    # ── Main apply ───────────────────────────────────────────

    async def apply(
        self,
        job: Job,
        resume_path: str,
        cover_letter_text: str,
        cover_letter_pdf: str,
    ) -> bool:
        url = job.apply_url.lower()

        if "linkedin.com" in url:
            return await self._apply_linkedin(job, resume_path, cover_letter_text)
        elif "greenhouse.io" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "greenhouse")
        elif "jobs.lever.co" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "lever")
        elif "jobs.ashbyhq.com" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "ashby")
        elif "indeed.com" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "indeed")
        elif "naukri.com" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "naukri")
        elif "wellfound.com" in url or "angel.co" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "wellfound")
        elif "workatastartup.com" in url:
            return await self._standard_apply(job, resume_path, cover_letter_text, "workatastartup")
        else:
            return await self._apply_generic(job, resume_path, cover_letter_text)

    # ── LinkedIn Easy Apply (with saved session) ─────────────

    async def _apply_linkedin(
        self,
        job: Job,
        resume_path: str,
        cover_letter_text: str,
    ) -> bool:
        """Apply via LinkedIn Easy Apply using saved login session."""
        if not self._browser:
            logger.error("Browser not initialised")
            return False

        # Create a platform-specific page with LinkedIn session
        page = await self._browser.new_page(platform="linkedin")
        try:
            await page.goto(job.apply_url, wait_until="networkidle")
            await Human.delay(2, 4)

            # Check if we're on a login page (session expired)
            if "login" in page.url.lower():
                logger.warning("LinkedIn session expired — re-run: python main.py --relogin linkedin")
                return False

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

    # ── Standard apply (with optional platform session) ──────

    async def _apply_generic(
        self, job: Job, resume_path: str, cover_letter_text: str,
    ) -> bool:
        if not self._browser:
            return False

        # Check if this URL needs a platform session
        platform = self._platform_for_url(job.apply_url)
        page = await self._browser.new_page(platform=platform)
        try:
            pre_url = job.apply_url
            await page.goto(pre_url, wait_until="networkidle")
            await Human.delay(2, 4)
            await Human.scroll(page, "down", 2)

            clicked = await self._click_apply_button(page)
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

            # Verify success: URL change or success message
            success = await self._confirm_submission(page, pre_url)
            if success:
                await Human.screenshot(page, f"generic_{job.job_id}_success")
                logger.info("Generic: successfully submitted to %s", job.company)
            else:
                await Human.screenshot(page, f"generic_{job.job_id}_no_confirm")
                logger.warning("Generic: no confirmation detected for %s", job.company)
            return success
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

        # Determine if this platform has a saved login session
        platform = _PLATFORM_SESSION_MAP.get(label)
        page = await self._browser.new_page(platform=platform)
        try:
            pre_url = job.apply_url
            await page.goto(pre_url, wait_until="networkidle")
            await Human.delay(2, 4)

            # Check for login wall on platforms that need session
            if platform and "login" in page.url.lower():
                logger.warning(
                    "%s session expired — re-run: python main.py --relogin %s",
                    label, platform,
                )
                return False

            # Click Apply button (if visible) to reveal the form
            clicked = await self._click_apply_button(page)
            if clicked:
                await Human.delay(2, 3)
            else:
                logger.info("No Apply button on %s — form may already be visible", label)

            filler = FormFiller()

            # Multi-step form loop — handles Greenhouse/Lever/Ashby/Indeed multi-step forms
            submitted = False
            for step in range(8):
                await Human.delay(1, 2)
                await filler.fill_form(page, resume_path, cover_letter_text)

                # Upload resume if a file input is present
                try:
                    file_inputs = await page.query_selector_all("input[type='file']")
                    for fi in file_inputs:
                        try:
                            await fi.set_input_files(resume_path)
                            logger.info("Uploaded resume for %s", label)
                            await Human.delay(1, 2)
                        except Exception:
                            continue
                except Exception:
                    pass

                # Check for Submit button first
                submit_selectors = [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Submit')", "button:has-text('Submit Application')",
                    "button:has-text('Send Application')",
                    "button:has-text('Complete Application')",
                ]
                for sel in submit_selectors:
                    try:
                        btn = await page.query_selector(sel)
                        if btn and await btn.is_visible():
                            logger.info("%s: found Submit button on step %d", label, step + 1)
                            await Human.click(page, sel)
                            await Human.delay(3, 5)
                            submitted = True
                            break
                    except Exception:
                        continue
                if submitted:
                    break

                # Try Next / Continue / Review button
                next_selectors = [
                    "button:has-text('Next')", "button:has-text('Continue')",
                    "button:has-text('Review')",
                ]
                found_next = False
                for sel in next_selectors:
                    try:
                        btn = await page.query_selector(sel)
                        if btn and await btn.is_visible():
                            logger.info("%s: clicking Next (step %d)", label, step + 1)
                            await btn.click()
                            await Human.delay(1, 2)
                            found_next = True
                            break
                    except Exception:
                        continue

                if not found_next:
                    logger.info("%s: no Next/Submit at step %d — form may be complete", label, step + 1)
                    break

            if submitted:
                # Verify success: URL change or success message on page
                success = await self._confirm_submission(page, pre_url)
                if success:
                    await Human.screenshot(page, f"{label}_{job.job_id}_success")
                    logger.info("%s: successfully submitted application to %s", label, job.company)
                else:
                    await Human.screenshot(page, f"{label}_{job.job_id}_no_confirm")
                    logger.warning("%s: submit clicked but no confirmation detected for %s", label, job.company)
                return success
            else:
                logger.warning("%s: reached end of form without submitting for %s", label, job.company)
                await Human.screenshot(page, f"{label}_{job.job_id}_no_submit")
                return False
        except Exception as e:
            await Human.screenshot(page, f"{label}_{job.job_id}_error")
            logger.error("%s apply failed: %s", label, e)
            return False
        finally:
            await page.close()

    # ── Submission verification ──────────────────────────────

    @staticmethod
    async def _confirm_submission(page, pre_url: str) -> bool:
        """Check whether the application was submitted successfully.

        Returns True if:
        - The URL changed from the original apply URL (redirected to confirmation), OR
        - A success/thank-you message is visible on the page.
        """
        post_url = page.url
        if post_url != pre_url:
            return True
        try:
            for sel in [
                "text=Thank you", "text=Application submitted",
                "text=Your application has been submitted",
                "text=Successfully applied", "text=Application received",
                "[class*='success']", "[class*='confirmation']",
                "[aria-label*='success']", "[role='alert']",
            ]:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return True
        except Exception:
            pass
        return False
