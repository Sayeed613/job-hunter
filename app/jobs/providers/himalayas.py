"""Himalayas job provider — fetches remote jobs from himalayas.app public API.

The Himalayas API is free, no auth required, and returns JSON.
API docs: https://himalayas.app/docs/remote-jobs-api
Key fields: title, companyName, applicationLink, location
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import aiohttp

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_HIMALAYAS_API = "https://himalayas.app/jobs/api"


class HimalayasProvider(BaseJobProvider):
    """Fetches remote jobs from Himalayas.app via their public JSON API.

    API is free and requires no authentication.
    Pagination: offset and limit (capped at 20 per page).
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "Himalayas"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs = await self._fetch_api()
        if jobs:
            logger.info("Himalayas: fetched %d jobs via API", len(jobs))
            return jobs

        if self._browser and self._browser.is_launched:
            jobs = await self._fetch_browser()
            logger.info("Himalayas: fetched %d jobs via browser", len(jobs))
        return jobs

    async def _fetch_api(self) -> list[Job]:
        """Fetch jobs from Himalayas public API."""
        jobs: list[Job] = []
        try:
            async with aiohttp.ClientSession() as session:
                # Fetch up to 40 jobs (2 pages of 20)
                for offset in (0, 20):
                    url = f"{_HIMALAYAS_API}?offset={offset}&limit=20"
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=30),
                        headers={"User-Agent": "Mozilla/5.0"},
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()

                    for item in data.get("jobs", []):
                        try:
                            title = item.get("title", "")
                            company = item.get("companyName", "") or item.get("company", "")
                            desc = item.get("description", "") or ""
                            url = item.get("applicationLink", "") or item.get("url", "") or item.get("applyUrl", "")
                            location = item.get("location", "Remote")
                            salary = item.get("salary", item.get("salaryRange", ""))
                            pub_date = item.get("publishedAt", "") or item.get("createdAt", "") or item.get("datePosted", "")

                            if not title or not company:
                                continue

                            job_id = hashlib.sha256(f"himalayas:{company}:{title}".encode()).hexdigest()[:16]
                            posted_at = None
                            if pub_date:
                                try:
                                    posted_at = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                                except Exception:
                                    pass

                            jobs.append(Job(
                                job_id=job_id, title=title, company=company,
                                description=str(desc)[:2000],
                                location=location or "Remote",
                                remote_type="Remote", job_type="Full-time",
                                source="Himalayas", apply_url=url,
                                salary=str(salary) if salary else None,
                                posted_at=posted_at,
                            ))
                        except Exception:
                            continue
        except Exception:
            pass
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        """Fallback: use Playwright browser to extract jobs from DOM."""
        if not self._browser:
            return []
        jobs: list[Job] = []
        page = await self._browser.new_page()
        try:
            await page.goto("https://himalayas.app/jobs", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            try:
                await page.wait_for_selector(
                    "a[href*='/jobs/'], [data-testid*='job'], [class*='job-item']",
                    timeout=10000,
                )
            except Exception:
                return []

            data = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/jobs/"]');
                const seen = new Set();
                return Array.from(links).slice(0, 30).map(a => {
                    const href = a.href;
                    if (!href || seen.has(href)) return null;
                    seen.add(href);
                    const parent = a.closest('[class*="job"]') || a.parentElement;
                    const title = a.textContent.trim();
                    const companyEl = parent ? parent.querySelector('[class*="company"]') : null;
                    const company = companyEl ? companyEl.textContent.trim() : 'Himalayas';
                    return { title, url: href, company };
                }).filter(j => j && j.title);
            }""")

            for item in data:
                if not item:
                    continue
                job_id = hashlib.sha256(f"himalayas:{item['url']}".encode()).hexdigest()[:16]
                jobs.append(Job(
                    job_id=job_id, title=item["title"][:100],
                    company=item.get("company", "Himalayas"),
                    description="", location="Remote",
                    remote_type="Remote", source="Himalayas",
                    apply_url=item["url"],
                    posted_at=datetime.now(timezone.utc),
                ))
        finally:
            await page.close()
        return jobs
