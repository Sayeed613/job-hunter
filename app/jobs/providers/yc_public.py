"""YC Public Job Board provider — fetches jobs from ycombinator.com/jobs.

This is the publicly accessible Y Combinator job board (no login required).
Uses Playwright browser since the page is JavaScript-rendered.

The old YCombinatorProvider (workatastartup.com) remains for the logged-in
version. This provider targets the public-facing board at ycombinator.com/jobs.
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

_YC_PUBLIC_URL = "https://www.ycombinator.com/jobs"


class YCJobBoardProvider(BaseJobProvider):
    """Fetches jobs from the public Y Combinator job board (ycombinator.com/jobs).

    This is the publicly accessible board. No login required.
    Uses the shared Playwright browser for JS rendering.
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "YCJobs"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        if not self._browser or not self._browser.is_launched:
            logger.warning("YCJobs: no browser available")
            return []

        jobs = await self._fetch_browser()
        logger.info("YCJobs: fetched %d jobs", len(jobs))
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        """Use Playwright browser to render YC public job listings."""
        jobs: list[Job] = []
        if not self._browser:
            return jobs

        page = await self._browser.new_page()
        try:
            await page.goto(_YC_PUBLIC_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            # Wait for job cards
            try:
                await page.wait_for_selector(
                    "a[href*='/jobs/'], [data-testid*='job'], "
                    ".job-card, article, div[class*='job'], .card",
                    timeout=15000,
                )
            except Exception:
                logger.debug("YCJobs: no job cards found")
                return []

            # Scroll to load more
            for _ in range(5):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(1500)

            # Extract jobs from DOM with descriptions
            data = await page.evaluate("""() => {
                const cards = document.querySelectorAll('[class*="job"], [class*="Job"], article, [data-testid*="card"], li[class]');
                const links = document.querySelectorAll('a[href*="/jobs/"]');
                const seen = new Set();
                const results = [];
                
                let items = cards.length > 3 ? cards : links;
                
                items.forEach(el => {
                    const card = el.closest('[class*="job"], [class*="Job"], article, li[class]') || el;
                    const allText = card.textContent || '';
                    if (allText.length < 5) return;
                    
                    // Find description element
                    const descEl = card.querySelector('p, [class*="desc"], [class*="Desc"], [class*="summary"], [class*="Summary"]');
                    const description = descEl ? descEl.textContent.trim() : '';
                    
                    // Find the job link
                    const link = card.querySelector('a[href*="/jobs/"]') || (el.tagName === 'A' ? el : null);
                    if (!link) return;
                    const href = link.href || '';
                    if (!href || seen.has(href)) return;
                    seen.add(href);
                    
                    const textLines = allText.split('\\n').map(l => l.trim()).filter(l => l.length > 2);
                    let title = '';
                    let company = 'YC Company';
                    
                    for (const line of textLines) {
                        if (line.length > 3 && line.length < 150) {
                            title = line;
                            break;
                        }
                    }
                    if (!title && link.textContent) title = link.textContent.trim();
                    if (!title) title = textLines[0] || '';
                    
                    for (const line of textLines) {
                        if (line !== title && line.length > 2 && line.length < 100 && !line.includes('http')) {
                            company = line;
                            break;
                        }
                    }
                    
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
                    job_id = hashlib.sha256(f"ycpub:{item['url']}".encode()).hexdigest()[:16]
                    desc = item.get("description", "") or ""
                    jobs.append(Job(
                        job_id=job_id,
                        title=item["title"],
                        company=item.get("company", "YC Company"),
                        description=desc[:2000],
                        location="Remote",
                        remote_type="Remote",
                        source="YCJobs",
                        apply_url=item["url"],
                        posted_at=datetime.now(timezone.utc),
                    ))
                    if desc:
                        logger.info("YCJobs: extracted description for %s (%d chars)", item["title"], len(desc))
                except Exception:
                    continue

        except Exception as e:
            if is_network_restricted_error(e):
                logger.warning(
                    "YCJobs browser fetch skipped due to blocked network access: %s",
                    network_error_summary(e),
                )
            else:
                logger.warning("YCJobs browser fetch failed: %s", e)
        finally:
            await page.close()

        return jobs
