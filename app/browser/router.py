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

from app.ai.client import AIClient
from app.browser.browser_manager import BrowserManager
from app.browser.form_filler import FormFiller
from app.browser.human import Human
from app.browser.session import LoginSession
from app.models.job import Job
from app.notifier import LocalNotifier
from app.telegram.interaction import TelegramInteraction

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

    def __init__(
        self,
        ai_client: Optional[AIClient] = None,
        notifier: Optional[LocalNotifier] = None,
        interaction: Optional[TelegramInteraction] = None,
    ) -> None:
        self._browser: Optional[BrowserManager] = None
        self._initialised = False
        self._session = LoginSession()
        self._ai_client = ai_client
        self._notifier = notifier
        self._interaction = interaction
        # Track auto-login attempts so we don't retry failed logins endlessly
        self._login_attempted: set[str] = set()

    async def ensure_browser(
        self,
        headless: bool = True,
        linkedin_email: str = "",
        linkedin_password: str = "",
    ) -> None:
        """Lazy-init the browser with anti-detection.

        Now supports auto-login — if a session doesn't exist, the bot
        will attempt to log in automatically when it encounters a login
        wall.
        """
        if not self._initialised:
            self._browser = BrowserManager()
            await self._browser.launch(headless=headless)
            self._initialised = True
            logger.info(
                "Available login sessions: %s",
                self._session.list_available_sessions() or "none (will auto-login)",
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

    def reset_login_attempts(self) -> None:
        """Reset the auto-login attempt tracker between cycles.

        Call this at the start of each cycle so platforms that failed
        to auto-login in the previous cycle get another chance.
        """
        self._login_attempted.clear()

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

    # ── Auto-login ───────────────────────────────────────────

    async def _auto_login(self, platform: str, page, retry_url: str = "") -> bool:
        """Attempt to auto-login to a platform using stored credentials.

        Navigates to the platform login page, fills email/password,
        clicks login, waits for the redirect to a non-login URL,
        then saves the session.

        Args:
            platform: "linkedin", "naukri", "wellfound", etc.
            page: The Playwright page (currently on the login wall).
            retry_url: After successful login, navigate back to this URL.

        Returns:
            True if login succeeded and session was saved.
        """
        # Only attempt once per platform per cycle
        if platform in self._login_attempted:
            logger.info("Auto-login already attempted for %s — not retrying", platform)
            return False
        self._login_attempted.add(platform)

        email, password = self._session.get_credentials(platform)
        if not email or not password:
            logger.info("No stored credentials for %s — can't auto-login", platform)
            return False

        login_url = self._session.get_login_url(platform)
        if not login_url:
            logger.warning("No login URL known for %s", platform)
            return False

        logger.info("Attempting auto-login for %s...", platform)
        if self._notifier:
            await self._notifier.send_message(f"🔑 Auto-login to {platform}...")

        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            await Human.delay(2, 3)

            # ── Fill email ──
            email_sel = (
                "input[name='session_key'], #username, input[type='email'], "
                "input[name='email'], input[name='login'], "
                "input[name='loginfmt']"
            )
            email_el = await page.query_selector(email_sel)
            if email_el:
                await email_el.click()
                await Human.delay(0.3, 0.6)
                await email_el.fill(email)
                logger.info("Filled email for %s", platform)

            # ── Fill password ──
            pw_sel = (
                "input[name='session_password'], #password, input[type='password'], "
                "input[name='password'], input[name='pass']"
            )
            pw_el = await page.query_selector(pw_sel)
            if pw_el:
                await pw_el.click()
                await Human.delay(0.3, 0.6)
                await pw_el.fill(password)

            # ── Click login button ──
            btn_sel = (
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Sign in'), button:has-text('Log in'), "
                "button:has-text('Login'), button:has-text('Continue')"
            )
            btn = await page.query_selector(btn_sel)
            if btn:
                await btn.click()
                await Human.delay(1, 2)
            else:
                # Try pressing Enter as fallback
                await page.keyboard.press("Enter")
                await Human.delay(1, 2)

            # ── Wait for redirect away from login page ──
            success_patterns = self._session.get_success_patterns(platform)
            for _ in range(15):  # up to 15 seconds
                current = page.url.lower()
                if "login" not in current:
                    # Check success patterns
                    if success_patterns:
                        if any(p in current for p in success_patterns):
                            break
                    else:
                        break  # no patterns to check, assume success
                await Human.delay(1, 1)

            # Check if we're still on a login page
            current = page.url.lower()
            if "login" in current:
                logger.warning(
                    "Auto-login for %s failed — still on login page "
                    "(CAPTCHA/2FA may be blocking). "
                    "Run 'python main.py --relogin %s' manually.",
                    platform, platform,
                )
                if self._notifier:
                    await self._notifier.send_message(
                        f"⚠️ Auto-login to {platform} failed — CAPTCHA/2FA may be blocking. "
                        f"Run manually: python main.py --relogin {platform}"
                    )
                return False

            # ── Save session ──
            if self._browser:
                ctx = await page.context()
                await self._browser.save_platform_session(ctx, platform)
                logger.info("✅ Auto-login successful for %s — session saved", platform)
                if self._notifier:
                    await self._notifier.send_message(f"✅ Auto-login to {platform} successful")

                # Navigate back to the original URL if provided
                if retry_url:
                    logger.info("Navigating back to job URL after auto-login: %s", retry_url)
                    await page.goto(retry_url, wait_until="networkidle")
                    await Human.delay(2, 3)

                return True

            return False

        except Exception as e:
            logger.error("Auto-login for %s failed: %s", platform, e)
            if self._notifier:
                await self._notifier.send_message(f"⚠️ Auto-login to {platform} error: {e}")
            return False

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
            "[class*='apply-now']",
            "[class*='submit-app']",
            "button:has-text('Easy Apply')",
            "button:has-text('Quick Apply')",
            "button:has-text('Submit')",
            "input[type='submit']",
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
        # JavaScript fallback: click any visible element with Apply/Candidate text
        try:
            clicked = await page.evaluate("""() => {
                const keywords = ['apply', 'candidate', 'submit', 'register', 'sign up', 'join us', 'continue'];
                const candidates = document.querySelectorAll('a, button, span, div[role="button"]');
                for (const el of candidates) {
                    const text = (el.textContent || '').toLowerCase().trim();
                    if (keywords.some(k => text.includes(k)) && el.offsetParent !== null) {
                        el.scrollIntoView({behavior: 'instant', block: 'center'});
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                logger.info("Clicked Apply button via JS fallback")
                await Human.delay(1.0, 2.0)
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

            # Check if we're on a login page (session expired) — auto-login
            if "login" in page.url.lower():
                logged_in = await self._auto_login("linkedin", page, retry_url=job.apply_url)
                if not logged_in:
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

            filler = FormFiller(
                ai_client=self._ai_client,
                notifier=self._notifier,
                interaction=self._interaction,
            )

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
            filler = FormFiller(
                ai_client=self._ai_client,
                notifier=self._notifier,
                interaction=self._interaction,
            )
            await filler.fill_form(page, resume_path, cover_letter_text)
            await Human.delay(1, 2)

            submit_selectors = [
                "button[type='submit']", "input[type='submit']",
                "button:has-text('Submit')", "button:has-text('Submit Application')",
                "button:has-text('Send Application')", "button:has-text('Complete Application')",
                "button:has-text('Apply Now')", "button:has-text('Send')",
                "button:has-text('Finish')", "button:has-text('Done')",
                "[class*='submit']", "[class*='send-app']",
            ]
            submitted = False
            for sel in submit_selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await Human.click(page, sel)
                        submitted = True
                        break
                except Exception:
                    continue
            # JS fallback: find any visible submit-capable element
            if not submitted:
                try:
                    clicked = await page.evaluate("""() => {
                        const sel = 'button[type="submit"], input[type="submit"], [class*="submit"], [class*="send"]';
                        const btns = document.querySelectorAll(sel);
                        for (const btn of btns) {
                            if (btn.offsetParent !== null) {
                                btn.scrollIntoView({behavior: 'instant', block: 'center'});
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                    if clicked:
                        logger.info("Generic: clicked submit via JS fallback")
                except Exception:
                    pass

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

            # Check for login wall on platforms that need session — auto-login
            if platform and "login" in page.url.lower():
                logged_in = await self._auto_login(platform, page, retry_url=pre_url)
                if not logged_in:
                    return False

            # Click Apply button (if visible) to reveal the form
            clicked = await self._click_apply_button(page)
            if clicked:
                await Human.delay(2, 3)
            else:
                logger.info("No Apply button on %s — form may already be visible", label)

            filler = FormFiller(
                ai_client=self._ai_client,
                notifier=self._notifier,
                interaction=self._interaction,
            )

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
                    "button:has-text('Send Application')", "button:has-text('Complete Application')",
                    "button:has-text('Apply Now')", "button:has-text('Send')",
                    "button:has-text('Finish')", "button:has-text('Done')",
                    "[class*='submit']", "[class*='send-app']",
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
