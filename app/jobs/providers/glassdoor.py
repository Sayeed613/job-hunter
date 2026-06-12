"""Glassdoor job provider — fetches jobs from glassdoor.com using Playwright."""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_GLASSDOOR_URL = "https://www.glassdoor.com/Job/jobs.htm?sc.keyword=react+python+frontend&remoteWorkType=1"


class GlassdoorProvider(BaseJobProvider):
    """Fetches jobs from Glassdoor using Playwright browser."""

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "Glassdoor"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        if not self._browser or not self._browser.is_launched:
            logger.warning("Glassdoor: no browser available")
            return []

        try:
            page = await self._browser.new_page()
            try:
                await page.goto(_GLASSDOOR_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(random.randint(3000, 5000))

                try:
                    await page.wait_for_selector(
                        ".jobListing, [data-testid='job-card'], "
                        "article[class*='job'], .react-job-listing",
                        timeout=8000,
                    )
                except Exception:
                    logger.debug("Glassdoor: no job listings found")
                    return []

                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, 600)")
                    await page.wait_for_timeout(random.randint(1000, 2000))

                cards = await page.query_selector_all(
                    ".jobListing, [data-testid='job-card'], article[class*='job']"
                )

                for card in cards[:30]:
                    try:
                        job = await self._parse_card(card)
                        if job:
                            jobs.append(job)
                    except Exception:
                        continue
            finally:
                await page.close()

            logger.info("Glassdoor: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Glassdoor fetch failed")
        return jobs

    async def _parse_card(self, card) -> Job | None:
        try:
            title_el = await card.query_selector(
                "a[class*='title'], a[data-testid='job-link'], h2 a, a.jobLink"
            )
            if not title_el:
                return None
            title = (await title_el.inner_text()).strip()
            url = await title_el.get_attribute("href") or ""
            if url and not url.startswith("http"):
                url = f"https://www.glassdoor.com{url}"

            company_el = await card.query_selector(
                "[data-testid='company-name'], span[class*='company'], "
                "a[class*='employer']"
            )
            company = (await company_el.inner_text()).strip() if company_el else ""

            loc_el = await card.query_selector(
                "[data-testid='location'], span[class*='location'], .job-location"
            )
            location = (await loc_el.inner_text()).strip() if loc_el else "Remote"

            if not title or not company:
                return None

            job_id = hashlib.sha256(f"glassdoor:{company}:{title}".encode()).hexdigest()[:16]
            return Job(
                job_id=job_id, title=title, company=company,
                description="", location=location or "Remote",
                remote_type="Remote", source="Glassdoor", apply_url=url,
                posted_at=datetime.now(timezone.utc),
            )
        except Exception:
            return None
