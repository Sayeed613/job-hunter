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

            # Extract jobs from DOM
            data = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/jobs/"]');
                const seen = new Set();
                return Array.from(links).slice(0, 50).map(link => {
                    const href = link.href || '';
                    if (seen.has(href) || !href) return null;
                    seen.add(href);
                    const card = link.closest('[data-testid*="card"], .job-card, article, div[class*="job"], li') || link.parentElement;
                    const text = card ? card.textContent : link.textContent;
                    if (text.length < 5) return null;
                    const lines = text.split('\\\\n').map(l => l.trim()).filter(l => l.length > 2);
                    let title = lines.find(l => l.length > 3 && l.length < 150) || '';
                    let company = lines.find(l => l !== title && l.length > 2 && l.length < 100) || 'YC Company';
                    return { title: title.slice(0, 150), url: href, company: company.slice(0, 100) };
                }).filter(j => j && j.title);
            }""")

            for item in data:
                try:
                    if not item:
                        continue
                    job_id = hashlib.sha256(f"ycpub:{item['url']}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id,
                        title=item["title"],
                        company=item.get("company", "YC Company"),
                        description="",
                        location="Remote",
                        remote_type="Remote",
                        source="YCJobs",
                        apply_url=item["url"],
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.warning("YCJobs browser fetch failed: %s", e)
        finally:
            await page.close()

        return jobs
