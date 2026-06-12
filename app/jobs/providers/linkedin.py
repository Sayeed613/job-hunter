"""LinkedIn job provider — uses Playwright browser to fetch jobs from LinkedIn.

LinkedIn requires JavaScript rendering and authentication for job search pages.
This provider uses the shared BrowserManager to leverage the saved session
so it can see LinkedIn job listings that require login.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_KEYWORDS = ["React", "Python", "Frontend", "Full Stack", "Backend", "Node.js"]
_LOCATIONS = ["Bangalore", "India", "Remote"]


class LinkedInProvider(BaseJobProvider):
    """Fetches LinkedIn jobs using a real Playwright browser.

    Uses the BrowserManager's saved session for logged-in access.
    Filters: last 24 hours, remote/hybrid.
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "LinkedIn"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []

        if not self._browser or not self._browser.is_launched:
            logger.warning("LinkedIn: no browser available — returning 0 jobs")
            return []

        try:
            for kw in _KEYWORDS:
                for loc in _LOCATIONS:
                    try:
                        page_jobs = await self._scrape_keyword(kw, loc)
                        jobs.extend(page_jobs)
                    except Exception:
                        logger.debug("LinkedIn: scrape failed for %s / %s", kw, loc, exc_info=True)
                        continue

            logger.info("LinkedIn: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("LinkedIn fetch failed")
        return jobs

    async def _scrape_keyword(self, keyword: str, location: str) -> list[Job]:
        """Navigate to LinkedIn job search, wait for JS render, and extract jobs."""
        if not self._browser:
            return []

        url = (
            "https://www.linkedin.com/jobs/search/"
            f"?keywords={keyword.replace(' ', '%20')}"
            f"&location={location.replace(' ', '%20')}"
            "&f_TPR=r86400"  # Past 24 hours
            "&f_WT=2"        # Remote
            "&sort=DD"       # Most recent
        )

        page = await self._browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(random.randint(3000, 5000))

            # Handle login wall if LinkedIn redirects to login
            if "login" in page.url.lower():
                logger.info("LinkedIn login wall detected — waiting briefly then retrying")
                await page.wait_for_timeout(3000)
                if "login" in page.url.lower():
                    logger.warning("LinkedIn: still on login page — can't fetch jobs without auth")
                    return []

            # Wait for job listing container to appear
            try:
                await page.wait_for_selector(
                    ".jobs-search__results-list, .scaffold-layout__list, "
                    "[data-job-id], .job-card-container, "
                    ".jobs-search-results__list, ul.jobs-search__results",
                    timeout=10000,
                )
            except Exception:
                logger.debug("LinkedIn: no job results container found for %s / %s", keyword, location)
                return []

            # Scroll to load more results
            for _ in range(5):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(random.randint(800, 1500))

            jobs = await self._extract_jobs(page, keyword, location)
            return jobs
        except Exception as e:
            logger.debug("LinkedIn: error scraping %s / %s: %s", keyword, location, e)
            return []
        finally:
            await page.close()

    async def _extract_jobs(
        self, page, keyword: str, location: str
    ) -> list[Job]:
        """Extract job data from the rendered LinkedIn page."""
        jobs: list[Job] = []

        # Try multiple selectors for job cards
        cards = await page.query_selector_all(
            ".job-card-container, .job-search-card, "
            "[data-job-id], .jobs-search__results-list > li, "
            ".scaffold-layout__list-item, .result-card"
        )

        if not cards:
            # Fallback: try extracting from the list container children
            container = await page.query_selector(
                ".jobs-search__results-list, .jobs-search-results__list, "
                ".scaffold-layout__list"
            )
            if container:
                cards = await container.query_selector_all("li, div[data-job-id]")

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
        """Parse a single LinkedIn job card element into a Job object."""
        try:
            # Title
            title_el = await card.query_selector(
                "a[data-tracking-control-name='public_jobs_jserp_job'], "
                "a.base-card__full-link, a.job-card-list__title, "
                "span[title], h3, .job-card-search__title a, "
                "a.job-search-card__title, a[href*='/jobs/view']"
            )
            if not title_el:
                # Try any anchor with href containing /jobs/view
                all_links = await card.query_selector_all("a")
                for link in all_links:
                    href = await link.get_attribute("href") or ""
                    if "/jobs/view" in href:
                        title_el = link
                        break
            if not title_el:
                return None

            title = (await title_el.inner_text()).strip()
            apply_url = await title_el.get_attribute("href") or ""
            if apply_url and not apply_url.startswith("http"):
                apply_url = f"https://www.linkedin.com{apply_url}"

            # Company name
            company_el = await card.query_selector(
                ".job-card-container__company-name, "
                ".job-search-card__company-name, "
                ".base-search-card__subtitle a, "
                ".artdeco-entity-lockup__subtitle, "
                "a[data-tracking-control-name='public_jobs_jserp_job_company']"
            )
            company = ""
            if company_el:
                company = (await company_el.inner_text()).strip()
            if not company:
                company = "Unknown"

            # Location
            loc_el = await card.query_selector(
                ".job-card-container__metadata-item, "
                ".job-search-card__location, "
                ".base-search-card__location, "
                ".artdeco-entity-lockup__metadata-item"
            )
            job_loc = ""
            if loc_el:
                job_loc = (await loc_el.inner_text()).strip()

            # Salary / metadata
            salary_el = await card.query_selector(
                ".job-card-container__salary, "
                ".job-search-card__salary-info, "
                ".base-search-card__metadata-item"
            )
            salary = ""
            if salary_el:
                salary = (await salary_el.inner_text()).strip()

            # Date posted
            date_el = await card.query_selector(
                ".job-card-container__listed-state, "
                ".job-search-card__listed-state, "
                ".base-search-card__metadata-item:last-child, "
                "time"
            )
            posted_at = None
            if date_el:
                date_text = (await date_el.inner_text()).strip().lower()
                posted_at = self._parse_date(date_text)
                # Try datetime attribute
                dt_attr = await date_el.get_attribute("datetime")
                if dt_attr and not posted_at:
                    try:
                        posted_at = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
                    except Exception:
                        pass

            if not title:
                return None

            job_id = hashlib.sha256(
                f"linkedin:{company}:{title}".encode()
            ).hexdigest()[:16]

            return Job(
                job_id=job_id,
                title=title,
                company=company,
                description="",  # LinkedIn doesn't show full desc in search results
                location=job_loc or location or "Remote",
                remote_type="Remote" if "remote" in (job_loc + location).lower() else "Hybrid",
                salary=salary or None,
                source="LinkedIn",
                apply_url=apply_url,
                posted_at=posted_at,
            )
        except Exception:
            return None

    @staticmethod
    def _parse_date(date_text: str) -> datetime | None:
        """Parse LinkedIn date strings like '1 day ago', '2 weeks ago', 'Just now'."""
        now = datetime.now(timezone.utc)
        if not date_text:
            return None
        if "just now" in date_text or "moments" in date_text or "now" in date_text:
            return now
        if "minute" in date_text:
            match = re.search(r"(\d+)", date_text)
            minutes = int(match.group(1)) if match else 1
            return now - timedelta(minutes=minutes)
        if "hour" in date_text:
            match = re.search(r"(\d+)", date_text)
            hours = int(match.group(1)) if match else 1
            return now - timedelta(hours=hours)
        if "day" in date_text:
            match = re.search(r"(\d+)", date_text)
            days = int(match.group(1)) if match else 1
            return now - timedelta(days=days)
        if "week" in date_text:
            match = re.search(r"(\d+)", date_text)
            weeks = int(match.group(1)) if match else 1
            return now - timedelta(weeks=weeks)
        if "month" in date_text:
            match = re.search(r"(\d+)", date_text)
            months = int(match.group(1)) if match else 1
            return now - timedelta(days=months * 30)
        return now - timedelta(days=1)
