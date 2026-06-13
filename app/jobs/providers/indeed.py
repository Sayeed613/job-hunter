"""Indeed job provider — uses Playwright browser to fetch jobs from Indeed search pages.

Indeed requires JavaScript rendering, so this provider uses a shared
BrowserManager (Playwright) to navigate, wait for job cards, and
extract data from the rendered DOM.
"""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_LOCATIONS = ["Bangalore", "Remote"]


class IndeedProvider(BaseJobProvider):
    """Fetches jobs from Indeed using a real Playwright browser.

    Filters: posted within 1 day, remote jobs.
    Uses the shared BrowserManager to render JavaScript-heavy search pages.
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None
        self._keywords = self._load_keywords_from_settings()

    @staticmethod
    def _load_keywords_from_settings() -> list[str]:
        from app.config.settings import Settings
        cfg = Settings()
        return [k.strip() for k in cfg.job_keywords.split(",") if k.strip()]

    @property
    def name(self) -> str:
        return "Indeed"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []

        if not self._browser or not self._browser.is_launched:
            logger.warning("Indeed: no browser available — returning 0 jobs")
            return []

        try:
            for kw in self._keywords:
                for loc in _LOCATIONS:
                    try:
                        page_jobs = await self._scrape_keyword(kw, loc)
                        jobs.extend(page_jobs)
                    except Exception:
                        logger.debug("Indeed: scrape failed for %s / %s", kw, loc, exc_info=True)
                        continue

            logger.info("Indeed: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Indeed fetch failed")
        return jobs

    async def _scrape_keyword(self, keyword: str, location: str) -> list[Job]:
        """Navigate to Indeed search, wait for JS render, and extract job cards."""
        if not self._browser:
            return []

        url = (
            "https://www.indeed.com/jobs?"
            f"q={keyword.replace(' ', '+')}"
            f"&l={location.replace(' ', '+')}"
            "&fromage=1"
            "&remotejob=1"
            "&sort=date"
        )

        page = await self._browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(random.randint(3000, 5000))  # wait for JS render

            # Scroll down a few times to trigger lazy loading
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 600)")
                await page.wait_for_timeout(random.randint(1000, 2000))

            jobs = await self._extract_jobs(page, keyword, location)
            return jobs
        except Exception as e:
            logger.debug("Indeed: error scraping %s / %s: %s", keyword, location, e)
            return []
        finally:
            await page.close()

    async def _extract_jobs(
        self, page, keyword: str, location: str
    ) -> list[Job]:
        """Extract job data from the rendered Indeed page."""
        jobs: list[Job] = []

        # Wait for job cards to appear
        try:
            await page.wait_for_selector(
                ".job_seen_beacon, .cardOutline, [data-testid='job-card'], "
                ".jobsearch-SerpJobCard, .slider_container",
                timeout=8000,
            )
        except Exception:
            return []

        cards = await page.query_selector_all(
            ".job_seen_beacon, .cardOutline, [data-testid='job-card'], "
            ".jobsearch-SerpJobCard, .slider_container"
        )

        for card in cards:
            try:
                job = await self._parse_card(card, keyword, location)
                if job:
                    jobs.append(job)
            except Exception:
                continue

        return jobs

    async def _parse_card(
        self, card, keyword: str, location: str
    ) -> Job | None:
        """Parse a single Indeed job card element into a Job object."""
        try:
            # Title
            title_el = await card.query_selector(
                "h2.jobTitle a, a[id^='job_'], a.jobtitle, "
                "[data-testid='job-title'], .jobTitle a, span[title]"
            )
            if not title_el:
                return None
            title = (await title_el.inner_text()).strip()
            apply_url = await title_el.get_attribute("href") or ""
            if apply_url and not apply_url.startswith("http"):
                apply_url = f"https://www.indeed.com{apply_url}"

            # Company name
            company_el = await card.query_selector(
                "[data-testid='company-name'], .companyName, "
                ".company, span.companyName, .jobCardCompany"
            )
            company = ""
            if company_el:
                company = (await company_el.inner_text()).strip()
            if not company:
                company = "Unknown"

            # Location
            loc_el = await card.query_selector(
                "[data-testid='job-location'], .companyLocation, "
                ".location, .jobCardLocation"
            )
            job_loc = ""
            if loc_el:
                job_loc = (await loc_el.inner_text()).strip()

            # Salary
            salary_el = await card.query_selector(
                ".salary-snippet, .salaryText, .estimated-salary, "
                "[data-testid='job-salary'], .jobCardSalary"
            )
            salary = ""
            if salary_el:
                salary = (await salary_el.inner_text()).strip()

            # Description snippet
            desc_el = await card.query_selector(
                ".job-snippet, .summary, .jobCardDescription, "
                "[data-testid='job-snippet'], .jobDescription"
            )
            desc = ""
            if desc_el:
                desc = (await desc_el.inner_text()).strip()

            # Date posted
            date_el = await card.query_selector(
                ".date, .job-age, [data-testid='job-date'], .jobCardDate"
            )
            posted_at = None
            if date_el:
                date_text = (await date_el.inner_text()).strip().lower()
                posted_at = self._parse_date(date_text)

            if not title or not company:
                return None

            # Remove "new" suffix from title
            title = title.replace("new", "").strip()

            job_id = hashlib.sha256(
                f"indeed:{company}:{title}".encode()
            ).hexdigest()[:16]

            return Job(
                job_id=job_id,
                title=title,
                company=company,
                description=desc[:2000],
                location=job_loc or location or "Remote",
                remote_type="Remote" if "remote" in (job_loc + location).lower() else "Hybrid",
                salary=salary or None,
                source="Indeed",
                apply_url=apply_url,
                posted_at=posted_at,
            )
        except Exception:
            return None

    @staticmethod
    def _parse_date(date_text: str) -> datetime | None:
        """Parse Indeed date strings like 'Just posted', '3 days ago', '30+ days ago'."""
        now = datetime.now(timezone.utc)
        if not date_text or date_text in ("just posted", "today"):
            return now
        if "hour" in date_text or "minute" in date_text:
            return now
        if "day" in date_text:
            import re
            match = re.search(r"(\d+)", date_text)
            if match:
                days = int(match.group(1))
                return now - timedelta(days=days)
            return now - timedelta(days=1)
        return now - timedelta(days=1)
