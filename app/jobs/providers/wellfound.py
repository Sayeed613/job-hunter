"""Wellfound (AngelList Talent) job provider — fetches startup jobs via browser rendering.

Wellfound is fully JavaScript-rendered — HTTP requests return 403 or empty HTML.
This provider uses the shared Playwright browser to render the page and extract jobs.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import aiohttp

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job
from app.utils.http_headers import browser_headers

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")


class WellfoundProvider(BaseJobProvider):
    """Fetches startup jobs from Wellfound using the shared Playwright browser.

    Wellfound is fully JS-rendered — the initial HTTP response contains no job data.
    This provider uses the shared browser to render JavaScript and extract jobs from
    the live DOM.
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "Wellfound"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        # Try HTTP first (some pages may have server-rendered content)
        jobs = await self._fetch_http()
        if jobs:
            logger.info("Wellfound: fetched %d jobs via HTTP", len(jobs))
            return jobs

        # Fall back to browser for full JS rendering
        if self._browser and self._browser.is_launched:
            jobs = await self._fetch_browser()
            logger.info("Wellfound: fetched %d jobs via browser", len(jobs))
        else:
            logger.warning("Wellfound: no browser available — returning 0 jobs")

        return jobs

    async def _fetch_http(self) -> list[Job]:
        """Try HTTP first — may work for cached/server-rendered content."""
        jobs: list[Job] = []
        try:
            url = "https://wellfound.com/jobs?remote=true&sort_by=created_at"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers=browser_headers(),
                ) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        jobs.extend(self._parse_html(html))
                    else:
                        logger.info("Wellfound: HTTP returned %d — trying browser", resp.status)
        except Exception:
            logger.debug("Wellfound: HTTP fetch failed — trying browser")
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        """Use Playwright browser to render JS and extract jobs from the DOM."""
        jobs: list[Job] = []
        if not self._browser:
            return jobs

        page = await self._browser.new_page()
        try:
            url = "https://wellfound.com/jobs?remote=true&sort_by=created_at"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            # Wait for job cards to render
            try:
                await page.wait_for_selector(
                    "[data-testid*='job'], a[href*='/startups/'], .job-card, "
                    "article, div[class*='JobCard'], div[class*='job-card']",
                    timeout=15000,
                )
            except Exception:
                logger.warning("Wellfound: no job cards found after scroll — page may need login")
                return []

            # Scroll to trigger lazy loading
            for _ in range(5):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(1500)

            # Extract jobs from rendered DOM
            data = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/startups/"]');
                const seen = new Set();
                return Array.from(links).map(link => {
                    const href = link.href || '';
                    if (seen.has(href)) return null;
                    seen.add(href);

                    // Try to find title and company from parent elements
                    const card = link.closest('[data-testid*="job"], .job-card, article, li, div[class*="job"], div[class*="Job"]') || link.parentElement;
                    const text = card ? card.textContent : link.textContent;
                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);

                    // Heuristic: first non-empty line with >3 chars is likely the title
                    let title = lines.find(l => l.length > 3 && l.length < 150) || link.textContent.trim();
                    // Company is often right after the title
                    const company = lines.find(l => l !== title && l.length > 2 && !l.includes('$') && l !== href) || 'Wellfound Startup';

                    return { title: title.slice(0, 150), url: href, company: company.slice(0, 100) };
                }).filter(j => j && j.title);
            }""")

            for item in data:
                try:
                    if not item:
                        continue
                    job_id = hashlib.sha256(f"wellfound:{item['url']}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id,
                        title=item["title"],
                        company=item.get("company", "Wellfound Startup"),
                        description="",
                        location="Remote",
                        remote_type="Remote",
                        source="Wellfound",
                        apply_url=item["url"],
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.warning("Wellfound browser fetch failed: %s", e)
        finally:
            await page.close()

        return jobs

    @staticmethod
    def _parse_html(html: str) -> list[Job]:
        """Parse job listings from the Wellfound HTML page."""
        import re
        import json
        jobs: list[Job] = []

        # Try to find JSON-LD structured data
        json_ld_matches = re.finditer(
            r'<script type="application/ld\+json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        for match in json_ld_matches:
            try:
                data = json.loads(match.group(1))
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        title = item.get("title", "")
                        company_obj = item.get(
                            "hiringOrganization", item.get("directApplicant", {}),
                        )
                        company = (
                            company_obj.get("name", "")
                            if isinstance(company_obj, dict) else ""
                        )
                        if not title or not company:
                            continue
                        job_id = hashlib.sha256(f"wellfound:{company}:{title}".encode()).hexdigest()[:16]
                        jobs.append(Job(
                            job_id=job_id, title=title, company=company,
                            description=(item.get("description", "") or "")[:2000],
                            location=item.get("jobLocation", {}).get("address", {}).get("addressLocality", "") or "Remote",
                            remote_type="Remote", source="Wellfound",
                            apply_url=item.get("url", ""),
                        ))
            except Exception:
                continue

        return jobs
