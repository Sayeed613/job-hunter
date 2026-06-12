"""Naukri job provider — uses Playwright browser to fetch jobs from Naukri.com.

Naukri requires JavaScript rendering for job listings. This provider uses
the shared BrowserManager (Playwright) to navigate, wait for job cards,
and extract data from the rendered DOM.
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

_KEYWORDS = ["react", "python", "frontend", "full-stack", "nodejs", "backend"]
_LOCATIONS = ["bangalore", "remote"]


class NaukriProvider(BaseJobProvider):
    """Fetches jobs from Naukri.com using a real Playwright browser.

    Filters: remote/hybrid jobs posted within 1 day.
    Uses the shared BrowserManager to render JavaScript-heavy search pages.
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "Naukri"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []

        if not self._browser or not self._browser.is_launched:
            logger.warning("Naukri: no browser available — returning 0 jobs")
            return []

        try:
            for kw in _KEYWORDS:
                for loc in _LOCATIONS:
                    try:
                        page_jobs = await self._scrape_keyword(kw, loc)
                        jobs.extend(page_jobs)
                    except Exception:
                        logger.debug("Naukri: scrape failed for %s / %s", kw, loc, exc_info=True)
                        continue

            logger.info("Naukri: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Naukri fetch failed")
        return jobs

    async def _scrape_keyword(self, keyword: str, location: str) -> list[Job]:
        """Navigate to Naukri search, wait for JS render, and extract job cards."""
        if not self._browser:
            return []

        url = (
            "https://www.naukri.com/"
            f"{keyword}-jobs-in-{location}"
            f"?k={keyword}"
            f"&l={location}"
            "&remote=1"
            "&daysold=1"
            "&sort=date"
        )

        page = await self._browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(random.randint(3000, 5000))

            # Wait for job listing container
            try:
                await page.wait_for_selector(
                    ".jobTuple, .cust-job-tuple, "
                    ".srp-jobtuple-wrapper, [data-job-id], "
                    ".list, .job-list, .jobsearch-jobTuple",
                    timeout=10000,
                )
            except Exception:
                logger.debug("Naukri: no job results for %s / %s", keyword, location)
                return []

            # Scroll to load more
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 700)")
                await page.wait_for_timeout(random.randint(1000, 2000))

            jobs = await self._extract_jobs(page, keyword, location)
            return jobs
        except Exception as e:
            logger.debug("Naukri: error scraping %s / %s: %s", keyword, location, e)
            return []
        finally:
            await page.close()

    async def _extract_jobs(
        self, page, keyword: str, location: str
    ) -> list[Job]:
        """Extract job data from the rendered Naukri page."""
        jobs: list[Job] = []

        cards = await page.query_selector_all(
            ".jobTuple, .cust-job-tuple, "
            "[data-job-id], .srp-jobtuple-wrapper > div, "
            ".job-list > div, .list > .jobCard"
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
        """Parse a single Naukri job card element into a Job object."""
        try:
            # Title
            title_el = await card.query_selector(
                "a.title, a[class*='title'], "
                ".jobTitle h2 a, "
                "a[href*='/jobs-by-'], "
                "a[href*='job-detail']"
            )
            if not title_el:
                # Try any anchor with href containing job details
                links = await card.query_selector_all("a[href*='naukri.com']")
                for link in links:
                    href = await link.get_attribute("href") or ""
                    if "job-detail" in href or "jobs" in href:
                        title_el = link
                        break
            if not title_el:
                return None

            title = (await title_el.inner_text()).strip()
            apply_url = await title_el.get_attribute("href") or ""
            if apply_url and not apply_url.startswith("http"):
                apply_url = f"https://www.naukri.com{apply_url}"

            # Company name
            company_el = await card.query_selector(
                "a[class*='subTitle'], a[class*='company'], "
                ".companyName, .comp-name, "
                ".jobCardCompany, .jobCompany"
            )
            company = ""
            if company_el:
                company = (await company_el.inner_text()).strip()
            if not company:
                company = "Unknown"

            # Location
            loc_el = await card.query_selector(
                ".location, .loc, [class*='location'], "
                ".jobCardLocation, .job-location"
            )
            job_loc = ""
            if loc_el:
                job_loc = (await loc_el.inner_text()).strip()

            # Experience
            exp_el = await card.query_selector(
                ".experience, .exp, [class*='experience'], "
                ".jobCardExp"
            )
            experience_years = None
            if exp_el:
                exp_text = (await exp_el.inner_text()).strip()
                import re
                match = re.search(r"(\d+)", exp_text)
                if match:
                    experience_years = int(match.group(1))

            # Salary
            salary_el = await card.query_selector(
                ".salary, .sal, [class*='salary'], "
                ".jobCardSalary"
            )
            salary = ""
            if salary_el:
                salary = (await salary_el.inner_text()).strip()

            # Description
            desc_el = await card.query_selector(
                ".job-description, .job-desc, "
                "[class*='description'], .jobCardDesc"
            )
            desc = ""
            if desc_el:
                desc = (await desc_el.inner_text()).strip()

            # Date
            date_el = await card.query_selector(
                ".posted-by, .date, [class*='date'], "
                ".jobCardDate, time"
            )
            posted_at = None
            if date_el:
                date_text = (await date_el.inner_text()).strip().lower()
                posted_at = self._parse_date(date_text)

            if not title:
                return None

            job_id = hashlib.sha256(
                f"naukri:{company}:{title}".encode()
            ).hexdigest()[:16]

            return Job(
                job_id=job_id,
                title=title,
                company=company,
                description=desc[:2000],
                location=job_loc or "Bangalore/Remote",
                remote_type="Remote" if "remote" in (job_loc + location).lower() else "Hybrid",
                salary=salary or None,
                source="Naukri",
                apply_url=apply_url,
                posted_at=posted_at,
                experience_years=experience_years,
            )
        except Exception:
            return None

    @staticmethod
    def _parse_date(date_text: str) -> datetime | None:
        """Parse Naukri date strings like '1 day ago', 'Today', '3 days ago'."""
        now = datetime.now(timezone.utc)
        if not date_text:
            return None
        if "today" in date_text:
            return now
        if "hour" in date_text or "minute" in date_text:
            return now
        if "yesterday" in date_text:
            return now - timedelta(days=1)
        if "day" in date_text:
            import re
            match = re.search(r"(\d+)", date_text)
            days = int(match.group(1)) if match else 1
            return now - timedelta(days=days)
        if "week" in date_text:
            import re
            match = re.search(r"(\d+)", date_text)
            weeks = int(match.group(1)) if match else 1
            return now - timedelta(weeks=weeks)
        return now - timedelta(days=1)
