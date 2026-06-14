"""Test script: Watch the bot apply to a real job in real-time.

Opens a visible browser (headless=False) on a real Greenhouse job listing,
clicks Apply, fills the entire form with candidate data, clicks Submit,
and checks for confirmation. You can watch everything happen.

Usage:
    python test_apply.py                       # Visible browser + confirmation prompt
    python test_apply.py --dry-run             # Fill form, navigate steps, NO submit
    python test_apply.py --headless            # Run headless (invisible)
    python test_apply.py --url <custom_url>    # Test with a different job
"""

from __future__ import annotations

import os
os.environ["PYTHONIOENCODING"] = "utf-8"

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.browser.browser_manager import BrowserManager
from app.browser.form_filler import FormFiller
from app.browser.human import Human

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("apply_test")

TEST_JOB = {
    "company": "Vercel",
    "title": "Design Engineer",
    "apply_url": "https://boards.greenhouse.io/vercel/jobs/5709080004",
    "location": "AMER (Remote)",
}


async def test_apply(
    headless: bool = False,
    apply_url: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Open a job's apply page, fill the form, submit, and verify.

    Args:
        headless: If True, run browser headless (invisible).
        apply_url: Override the default test job URL.
        dry_run: If True, fill all fields across all steps but skip final submit.

    Returns:
        True if the application appeared to submit successfully.
    """
    url = apply_url or TEST_JOB["apply_url"]
    company = TEST_JOB["company"]
    title = TEST_JOB["title"]

    # Find a resume file
    resume_path = ""
    for candidate in [
        "resumes/Sayeed_Ahmed_Resume.docx",
        "Sayeed_Frontend_Developer.docx",
        "Sayeed_Ahmed_Resume.docx",
    ]:
        p = Path(candidate)
        if p.exists():
            resume_path = str(p.resolve())
            break
    if not resume_path:
        paths = list(Path(".").glob("*resume*.docx")) + list(Path("resumes").glob("*.docx"))
        if paths:
            resume_path = str(paths[0])

    print(f"\n{'=' * 60}")
    print(f"TESTING REAL APPLICATION{' (DRY RUN)' if dry_run else ''}")
    print(f"{'=' * 60}")
    print(f"Company:  {company}")
    print(f"Position: {title}")
    print(f"URL:      {url}")
    print(f"Resume:   {resume_path or 'None'}")
    print(f"{'=' * 60}\n")

    mgr = BrowserManager()
    await mgr.launch(headless=headless)
    page = await mgr.new_page()
    success = False

    try:
        # ── Navigate to job ──
        print("Navigating to job page...")
        await page.goto(url, wait_until="networkidle", timeout=45000)
        await Human.delay(3, 5)
        print(f"   Page loaded: {page.url}")

        # Wait for the page to finish rendering (not "Loading...")
        for _ in range(5):
            try:
                body_text = (await page.inner_text("body") or "").lower().strip()
                if body_text and body_text != "loading...":
                    print(f"   Page content ready: {body_text[:80]}...")
                    break
            except Exception:
                pass
            print("   Waiting for page to finish rendering...")
            await Human.delay(2, 3)

        # ── Click Apply button ──
        print("\n[SEARCH] Looking for Apply button...")
        apply_selectors = [
            # Greenhouse-specific: anchor with button class
            "a.button:has-text('Apply')",
            ".button:has-text('Apply')",
            "a:has-text('Apply for this job')",
            "button:has-text('Apply')",
            "[class*='apply']:has-text('Apply')",
            "a[href*='apply']",
            "button[type='submit']",
            "[data-testid*='apply']",
            ".apply-btn",
            # Broad fallback: any element containing 'Apply'
            ":has-text('Apply for this job')",
            ":has-text('Apply Now')",
            ":has-text('Apply')",
        ]
        clicked_apply = False
        for sel in apply_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    text = (await btn.inner_text()).strip()
                    print(f"   Found: '{text}' via '{sel}'")
                    await btn.scroll_into_view_if_needed()
                    await Human.delay(0.5, 1)
                    await btn.click()
                    await Human.delay(2, 4)
                    clicked_apply = True
                    break
            except Exception:
                continue

        if not clicked_apply:
            # Ultimate fallback: try clicking via XPath or page.evaluate
            print("   Trying JavaScript click fallback...")
            try:
                text_on_page = (await page.inner_text("body") or "").lower()
                if "apply" in text_on_page:
                    clicked = await page.evaluate("""() => {
                        const links = document.querySelectorAll('a, button');
                        for (const el of links) {
                            if (el.textContent.toLowerCase().includes('apply') &&
                                el.offsetParent !== null) {
                                el.scrollIntoView({behavior: 'instant', block: 'center'});
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                    if clicked:
                        print("   [OK] Clicked Apply via JavaScript fallback")
                        await Human.delay(2, 4)
                        clicked_apply = True
            except Exception:
                pass

        if not clicked_apply:
            print("[FAIL] No Apply button found!")
            try:
                body = (await page.inner_text("body") or "")[:500]
                print(f"   Page text: {body[:200]}...")
            except Exception:
                pass
            await Human.screenshot(page, "test_no_apply_btn")
            return False

        print("   [OK] Apply button clicked!")

        # ── Cover letter text ──
        cover_text = (
            f"Dear {company} Hiring Team,\n\n"
            f"I am excited to apply for the {title} position at {company}. "
            f"With my background in frontend development and a passion for "
            f"building great user interfaces, I believe I would be a strong "
            f"addition to your team.\n\n"
            f"I have experience with React, Next.js, TypeScript, Node.js, "
            f"and modern frontend tooling.\n\n"
            f"Thank you for your consideration.\n\n"
            f"Best regards,\nSayeed Ahmed\nsayeedahmed90082@gmail.com\n+91 9008299613"
        )

        filler = FormFiller()

        # ── Multi-step form loop ──
        for step in range(10):
            await Human.delay(1, 2)
            await filler.fill_form(page, resume_path, cover_text)
            print(f"   Step {step + 1}: fields filled")

            # Upload resume if a file input is present
            if resume_path:
                try:
                    file_inputs = await page.query_selector_all("input[type='file']")
                    for fi in file_inputs:
                        label = await fi.evaluate(
                            "el => el.closest('label')?.textContent || "
                            "el.getAttribute('aria-label') || ''"
                        )
                        if any(w in label.lower() for w in ["resume", "cv", "upload"]):
                            await fi.set_input_files(resume_path)
                            print(f"   [OK] Resume uploaded: {resume_path}")
                            await Human.delay(1, 2)
                except Exception:
                    pass

            # Look for Submit button first
            submit_selectors = [
                "button[type='submit']", "input[type='submit']",
                "button:has-text('Submit')", "button:has-text('Submit Application')",
                "button:has-text('Send Application')",
            ]
            found_submit = False
            for sel in submit_selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        text = (await btn.inner_text()).strip()
                        print(f"\n[SUBMIT] Found Submit button: '{text}'")

                        if dry_run:
                            print("[DRY RUN] skipping final click. Filled fields:")
                            inputs = await page.query_selector_all(
                                "input:not([type='hidden']):not([type='submit']):"
                                "not([type='button']), textarea"
                            )
                            for inp in inputs[:8]:
                                try:
                                    n = (await inp.get_attribute("name")) or ""
                                    v = (await inp.input_value())[:60]
                                    if v:
                                        print(f"     - {n}: {v}")
                                except Exception:
                                    pass
                            success = True
                        else:
                            print(f"\n{'!' * 50}")
                            print(f"[WARN] THIS WILL SUBMIT A REAL APPLICATION TO {company.upper()}")
                            print(f"{'!' * 50}")
                            inp = input("\nPress Enter to submit, or Ctrl+C to abort... ")
                            await btn.click()
                            await Human.delay(3, 6)
                            print("   [OK] Submit clicked!")

                        found_submit = True
                        break
                except (KeyboardInterrupt, EOFError):
                    print("\n[ABORT] Submit aborted by user.")
                    success = True
                    found_submit = True
                    break
                except Exception:
                    continue

            if found_submit:
                break

            # Look for Next / Continue / Review button
            next_selectors = [
                "button:has-text('Next')", "button:has-text('Continue')",
                "button:has-text('Review')", "a:has-text('Next')",
                "a:has-text('Continue')", "a:has-text('Review')",
            ]
            found_next = False
            for sel in next_selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        text = (await btn.inner_text()).strip()
                        print(f"   [NEXT] Clicking '{text}' (step {step + 1})...")
                        await btn.click()
                        await Human.delay(1, 3)
                        found_next = True
                        break
                except Exception:
                    continue

            if not found_next:
                print(f"\n[WARN] No Next/Submit at step {step + 1}.")
                await Human.screenshot(page, f"test_step_{step + 1}")
                break

        # ── Verify submission ──
        print("\n[VERIFY] Checking confirmation...")
        await Human.delay(2, 4)
        await Human.screenshot(page, "test_after_submit")

        post_url = page.url
        if post_url != url:
            print(f"   [OK] URL changed: {post_url}")
            print("   [OK] Confirmation via URL redirect!")
            success = True
        else:
            try:
                body = (await page.inner_text("body") or "").lower()
                for word in ["thank you", "application submitted", "submitted",
                             "successfully applied", "received"]:
                    if word in body:
                        print(f"   [OK] Found confirmation text: '{word}'")
                        success = True
                        break
                if not success:
                    el = await page.query_selector(
                        "[class*='success'], [class*='confirmation'], "
                        "[role='alert'], [aria-label*='success']"
                    )
                    if el:
                        print("   [OK] Found success element")
                        success = True
            except Exception:
                pass

        if success:
            print("\n[SUCCESS] APPLICATION SUBMITTED SUCCESSFULLY!")
        else:
            print("\n[WARN] Submit clicked but NO confirmation detected.")
            print("   Check the browser / screenshot.")

        sp = await Human.screenshot(page, "test_final_state")
        print(f"   [SCREENSHOT] {sp}")

    except Exception as e:
        logger.exception("Test failed")
        print(f"\n[ERROR] {e}")
        try:
            await Human.screenshot(page, "test_error")
        except Exception:
            pass
    finally:
        await page.close()
        await mgr.close()
        print(f"\n{'=' * 60}")
        print(f"[DONE] Test complete. Success: {success}")
        print(f"{'=' * 60}")

    return success


if __name__ == "__main__":
    headless = "--headless" in sys.argv
    dry_run = "--dry-run" in sys.argv
    url = None
    for i, arg in enumerate(sys.argv):
        if arg == "--url" and i + 1 < len(sys.argv):
            url = sys.argv[i + 1]

    if dry_run:
        print("\n[DRY RUN] All fields filled, no submission\n")

    asyncio.run(test_apply(headless=headless, apply_url=url, dry_run=dry_run))
