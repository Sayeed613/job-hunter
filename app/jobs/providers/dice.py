"""Dice job provider — fetches tech jobs from dice.com using Playwright browser.

Dice.com is a Next.js React SPA. Job data is rendered dynamically.
The search results container uses `[data-testid="job-search-results"]`.
This provider uses `page.evaluate()` to extract job cards from the DOM.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

DICE_URLS = [
    "https://www.dice.com/jobs?q=react+python+frontend&location=Remote&remote=fixed&sort=date",
    "https://www.dice.com/jobs?q=javascript+typescript+node&location=Remote&remote=fixed&sort=date",
]


class DiceProvider(BaseJobProvider):
    """Fetches tech jobs from Dice.com using Playwright browser DOM extraction."""

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "Dice"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        if not self._browser or not self._browser.is_launched:
            logger.warning("Dice: no browser available")
            return []

        try:
            for url in DICE_URLS:
                page_jobs = await self._scrape_url(url)
                jobs.extend(page_jobs)

            logger.info("Dice: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Dice fetch failed")
        return jobs

    async def _scrape_url(self, url: str) -> list[Job]:
        """Navigate to a Dice search URL and extract job data from the rendered DOM."""
        if not self._browser:
            return []

        page = await self._browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # Wait for the search results container to appear
            try:
                await page.wait_for_selector(
                    "[data-testid='job-search-results'], [data-testid='job-card'], "
                    "section[class*='search-results']",
                    timeout=10000,
                )
            except Exception:
                return []

            # Scroll to trigger lazy loading
            for _ in range(5):
                await page.evaluate("window.scrollBy(0, 700)")
                await page.wait_for_timeout(1000)

            # Extract job data from the DOM using page.evaluate
            jobs = await page.evaluate("""() => {
                // Try to find all job card elements
                const cards = document.querySelectorAll(
                    '[data-testid="job-card"], ' +
                    'div[class*="card"]:not([class*="ad"]):not([class*="promoted"]), ' +
                    'li[class*="job-card"]'
                );

                const jobs = [];
                const seen = new Set();

                cards.forEach(card => {
                    // Find title link - usually an anchor inside h5 or directly
                    const titleEl = card.querySelector(
                        'a[data-testid="job-title"], ' +
                        'h5 a, h3 a, ' +
                        'a[href*="/job/"], ' +
                        'a[class*="title-link"]'
                    );

                    if (!titleEl) return;

                    const title = titleEl.textContent.trim();
                    const url = titleEl.href || '';

                    if (!title || seen.has(url)) return;
                    seen.add(url);

                    // Find company name
                    const companyEl = card.querySelector(
                        '[data-testid="company-name"], ' +
                        'a[class*="company"], ' +
                        'span[class*="company"], ' +
                        '[data-cy="company-name"]'
                    );
                    const company = companyEl
                        ? companyEl.textContent.trim()
                        : 'Unknown';

                    // Find location
                    const locEl = card.querySelector(
                        '[data-testid="job-location"], ' +
                        'span[class*="location"], ' +
                        '[data-cy="location"]'
                    );
                    const location = locEl
                        ? locEl.textContent.trim()
                        : 'Remote';

                    jobs.push({ title, url, company, location });
                });

                // Fallback: try to extract from the search results container directly
                if (jobs.length === 0) {
                    const container = document.querySelector(
                        '[data-testid="job-search-results"], ' +
                        'section[class*="search-results"]'
                    );
                    if (container) {
                        const links = container.querySelectorAll('a[href*="/job/"]');
                        links.forEach(link => {
                            const title = link.textContent.trim();
                            const url = link.href;
                            if (title && !seen.has(url)) {
                                seen.add(url);
                                // Try to find company from nearby elements
                                const parent = link.closest('div[class*="card"], li, [data-testid="job-card"]');
                                const companyEl = parent
                                    ? parent.querySelector('a[class*="company"], span[class*="company"]')
                                    : null;
                                const company = companyEl
                                    ? companyEl.textContent.trim()
                                    : 'Unknown';
                                jobs.push({ title, url, company, location: 'Remote' });
                            }
                        });
                    }
                }

                return jobs.slice(0, 40);
            }""")

            result = []
            for item in jobs:
                try:
                    title = item.get("title", "").strip()
                    company = item.get("company", "").strip()
                    url = item.get("url", "")
                    location = item.get("location", "Remote")

                    if not title or not company:
                        continue

                    job_id = hashlib.sha256(f"dice:{company}:{title}".encode()).hexdigest()[:16]
                    result.append(Job(
                        job_id=job_id, title=title, company=company,
                        description="", location=location,
                        remote_type="Remote", source="Dice", apply_url=url,
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

            return result
        except Exception as e:
            logger.debug("Dice: error scraping %s: %s", url, e)
            return []
        finally:
            await page.close()
