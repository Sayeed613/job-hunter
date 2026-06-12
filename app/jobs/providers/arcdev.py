"""Arc.dev job provider — fetches remote developer jobs from arc.dev.

Uses HTTP + JSON-LD first, falls back to Playwright browser for JS rendering.
Browser selectors use `[data-testid="job-card"]` for stable element targeting.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import aiohttp

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_ARC_URL = "https://arc.dev/remote-jobs"


class ArcDevProvider(BaseJobProvider):
    """Fetches remote developer jobs from Arc.dev. Uses browser when available."""

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "ArcDev"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs = await self._fetch_http()
        if jobs:
            logger.info("ArcDev: fetched %d jobs via HTTP", len(jobs))
            return jobs

        if self._browser and self._browser.is_launched:
            jobs = await self._fetch_browser()
            logger.info("ArcDev: fetched %d jobs via browser", len(jobs))

        return jobs

    async def _fetch_http(self) -> list[Job]:
        """Try HTTP + JSON-LD parsing first."""
        jobs: list[Job] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _ARC_URL,
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

                            job_id = hashlib.sha256(f"arcdev:{company}:{title}".encode()).hexdigest()[:16]
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
                                source="ArcDev", apply_url=url, posted_at=posted_at,
                            ))
                except Exception:
                    continue
        except Exception:
            pass
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        """Fallback: use Playwright browser with data-testid selectors."""
        jobs: list[Job] = []
        if not self._browser:
            return []
        page = await self._browser.new_page()
        try:
            await page.goto(_ARC_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            try:
                await page.wait_for_selector(
                    "[data-testid='job-card'], div[class*='JobCard'], article",
                    timeout=10000,
                )
            except Exception:
                logger.debug("ArcDev: no job cards found in browser")
                return []

            # Scroll to load more
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 700)")
                await page.wait_for_timeout(1500)

            # Use evaluate to extract data from DOM
            data = await page.evaluate("""() => {
                const cards = document.querySelectorAll('[data-testid="job-card"], div[class*="JobCard"], article a[href*="/remote-jobs/details/"]');
                const processed = new Set();
                return Array.from(cards).slice(0, 30).map(card => {
                    const link = card.tagName === 'A' ? card : card.querySelector('a[href*="/remote-jobs/details/"]');
                    if (!link) return null;
                    const href = link.href || '';
                    if (processed.has(href)) return null;
                    processed.add(href);
                    const title = link.textContent.trim() || '';
                    // Company and location from nearby elements
                    const parent = card.closest('[data-testid="job-card"], div[class*="JobCard"]') || card;
                    const companyEl = parent.querySelector('[data-testid*="company"], [class*="company"]');
                    const company = companyEl ? companyEl.textContent.trim() : 'Arc.dev';
                    return { title, url: href, company };
                }).filter(j => j && j.title);
            }""")

            for item in data:
                if not item:
                    continue
                job_id = hashlib.sha256(f"arcdev:{item['url']}".encode()).hexdigest()[:16]
                jobs.append(Job(
                    job_id=job_id, title=item["title"][:100],
                    company=item.get("company", "Arc.dev"),
                    description="", location="Remote",
                    remote_type="Remote", source="ArcDev",
                    apply_url=item["url"],
                    posted_at=datetime.now(timezone.utc),
                ))
        finally:
            await page.close()
        return jobs
