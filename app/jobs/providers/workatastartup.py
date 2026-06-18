"""Work at a Startup (YC) job provider — fetches jobs from workatastartup.com.

Requires a logged-in browser session (storage_state). The first run prompts
manual login via --relogin workatastartup. Subsequent runs load the saved session.

Job listings are rendered in a right-side panel. Apply flows may be:
  (a) In-site cover note form → fill and submit
  (b) External ATS link (Greenhouse/Lever/Ashby) → route through ApplicationRouter
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job
from app.utils.network import is_network_restricted_error, network_error_summary

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_YC_URL = "https://www.workatastartup.com/jobs"


class WorkAtAStartupProvider(BaseJobProvider):
    """Fetches startup jobs from Y Combinator's Work at a Startup platform.

    Session is managed at the browser level via storage_state. The provider
    checks for login walls and returns empty if not authenticated.
    Use --relogin workatastartup (coming in a future update) to re-authenticate.
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "WorkAtAStartup"

    @property
    def platform(self) -> str | None:
        return "workatastartup"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        if not self._browser or not self._browser.is_launched:
            logger.warning("WorkAtAStartup: no browser available")
            return []

        jobs = await self._fetch_browser()
        logger.info("WorkAtAStartup: fetched %d jobs", len(jobs))
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        """Use Playwright browser to render YC job listings."""
        jobs: list[Job] = []
        if not self._browser:
            return jobs

        page = await self._browser.new_page()
        try:
            url = f"{_YC_URL}?remote=true"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            # Check for login wall
            if "login" in page.url.lower() or "signin" in page.url.lower():
                logger.warning("WorkAtAStartup: login wall — session may have expired")
                return []

            # Wait for job cards
            try:
                await page.wait_for_selector(
                    "a[href*='/companies/'], [data-testid*='job-card'], "
                    ".job-card, .job-listing, article, div[class*='job']",
                    timeout=15000,
                )
            except Exception:
                logger.debug("WorkAtAStartup: no job cards found")
                return []

            # Scroll to load more
            for _ in range(5):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(1500)

            # Extract jobs from DOM with descriptions
            data = await page.evaluate("""() => {
                const cards = document.querySelectorAll('[class*="job"], [class*="Job"], [data-testid*="card"], article, li[class]');
                const seen = new Set();
                const results = [];
                
                // Also try to find cards by company links
                const links = document.querySelectorAll('a[href*="/companies/"]');
                
                // Use cards if found, otherwise use links
                let items = cards.length > 3 ? cards : links;
                
                items.forEach(el => {
                    // Get the actual card element
                    const card = el.closest('[class*="job"], [class*="Job"], article, li[class]') || el;
                    const allText = card.textContent || '';
                    
                    // Find the description element inside the card
                    const descEl = card.querySelector('p, [class*="desc"], [class*="Desc"], [class*="summary"], [class*="Summary"], [data-testid*="desc"]');
                    const description = descEl ? descEl.textContent.trim() : '';
                    
                    // Find the title link
                    const link = card.querySelector('a[href*="/companies/"]') || el;
                    const href = link.href || '';
                    if (!href || seen.has(href)) return;
                    seen.add(href);
                    
                    const textLines = allText.split('\\n').map(l => l.trim()).filter(l => l.length > 2);
                    let title = '';
                    let company = 'YC Startup';
                    
                    // Try to find title (first meaningful line in card)
                    for (const line of textLines) {
                        if (line.length > 3 && line.length < 150 && !line.includes('/companies/')) {
                            title = line;
                            break;
                        }
                    }
                    if (!title && link.textContent) title = link.textContent.trim();
                    if (!title) title = textLines[0] || '';
                    
                    // Try to find company name (different from title)
                    for (const line of textLines) {
                        if (line !== title && line.length > 2 && line.length < 100 && !line.includes('http')) {
                            company = line;
                            break;
                        }
                    }
                    
                    // Use description from dedicated element, or fallback to text not already used as title/company
                    const finalDesc = description || textLines.filter(l => 
                        l !== title && l !== company && l.length > 10 && l.length < 500
                    ).slice(0, 2).join(' ');
                    
                    results.push({
                        title: title.slice(0, 150),
                        url: href,
                        company: company.slice(0, 100),
                        description: finalDesc.slice(0, 2000),
                    });
                });
                
                return results;
            }""")

            for item in data:
                try:
                    if not item:
                        continue
                    job_id = hashlib.sha256(f"ycb:{item['url']}".encode()).hexdigest()[:16]
                    desc = item.get("description", "") or ""
                    jobs.append(Job(
                        job_id=job_id,
                        title=item["title"],
                        company=item.get("company", "YC Startup"),
                        description=desc[:2000],
                        location="Remote",
                        remote_type="Remote",
                        source="WorkAtAStartup",
                        apply_url=item["url"],
                        posted_at=datetime.now(timezone.utc),
                    ))
                    if desc:
                        logger.info("WorkAtAStartup: extracted description for %s (%d chars)", item["title"], len(desc))
                except Exception:
                    continue

        except Exception as e:
            if is_network_restricted_error(e):
                logger.warning(
                    "WorkAtAStartup browser fetch skipped due to blocked network access: %s",
                    network_error_summary(e),
                )
            else:
                logger.warning("WorkAtAStartup browser fetch failed: %s", e)
        finally:
            await page.close()

        return jobs
