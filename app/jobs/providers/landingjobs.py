"""Landing Jobs provider — fetches tech jobs from landing.jobs.

Uses HTTP + JSON-LD first, falls back to Playwright browser.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_LANDING_URL = "https://landing.jobs/jobs?remote=true"


class LandingJobsProvider(BaseJobProvider):
    """Fetches tech jobs from Landing Jobs. Uses browser when needed."""

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "LandingJobs"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs = await self._fetch_http()
        if jobs:
            logger.info("LandingJobs: fetched %d jobs via HTTP", len(jobs))
            return jobs

        if self._browser and self._browser.is_launched:
            jobs = await self._fetch_browser()
            logger.info("LandingJobs: fetched %d jobs via browser", len(jobs))

        return jobs

    async def _fetch_http(self) -> list[Job]:
        jobs: list[Job] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _LANDING_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()

            json_ld = re.findall(
                r'<script type="application/ld\+json">(.*?)</script>',
                html, re.DOTALL,
            )
            import json
            for match in json_ld:
                try:
                    data = json.loads(match)
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if isinstance(item, dict) and item.get("@type") == "JobPosting":
                            title = item.get("title", "")
                            company = item.get("hiringOrganization", {}).get("name", "")
                            desc = item.get("description", "") or ""
                            url = item.get("url", "")
                            location = item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                            date_posted = item.get("datePosted", "")

                            if not title or not company:
                                continue

                            job_id = hashlib.sha256(f"landing:{company}:{title}".encode()).hexdigest()[:16]
                            posted_at = None
                            if date_posted:
                                try:
                                    posted_at = datetime.fromisoformat(date_posted.replace("Z", "+00:00"))
                                except Exception:
                                    pass

                            jobs.append(Job(
                                job_id=job_id, title=title, company=company,
                                description=re.sub(r"<[^>]+>", "", desc)[:2000],
                                location=location or "Remote", remote_type="Remote",
                                source="LandingJobs", apply_url=url, posted_at=posted_at,
                            ))
                except Exception:
                    continue
        except Exception:
            pass
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        jobs: list[Job] = []
        if not self._browser:
            return []
        page = await self._browser.new_page()
        try:
            await page.goto(_LANDING_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(4000)
            try:
                await page.wait_for_selector(
                    "a[href*='/opportunities/'], .job-card, article", timeout=8000,
                )
            except Exception:
                return []
            cards = await page.query_selector_all("a[href*='/opportunities/'], .job-card a, article a")
            seen = set()
            for card in cards[:30]:
                try:
                    href = await card.get_attribute("href") or ""
                    if not href or href in seen:
                        continue
                    seen.add(href)
                    if not href.startswith("http"):
                        href = f"https://landing.jobs{href}"
                    title = (await card.inner_text()).strip().split("\n")[0][:100]
                    job_id = hashlib.sha256(f"landing:{href}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id, title=title, company="Landing Jobs",
                        description="", location="Remote", remote_type="Remote",
                        source="LandingJobs", apply_url=href,
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue
        finally:
            await page.close()
        return jobs
