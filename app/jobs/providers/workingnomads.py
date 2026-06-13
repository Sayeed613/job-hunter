"""Working Nomads job provider — fetches remote jobs from workingnomads.com."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job
from app.utils.http_headers import api_headers

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_WN_URL = "https://www.workingnomads.com/api/jobs?remote=true"


class WorkingNomadsProvider(BaseJobProvider):
    """Fetches remote jobs from Working Nomads. Falls back to browser when API is blocked."""

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "WorkingNomads"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs = await self._fetch_http()
        if jobs:
            logger.info("WorkingNomads: fetched %d jobs via HTTP", len(jobs))
            return jobs

        if self._browser and self._browser.is_launched:
            jobs = await self._fetch_browser()
            logger.info("WorkingNomads: fetched %d jobs via browser", len(jobs))
        else:
            logger.warning("WorkingNomads: API blocked and no browser available")

        return jobs

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        reraise=False,
    )
    async def _fetch_http(self) -> list[Job]:
        jobs: list[Job] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _WN_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers=api_headers(referer="https://www.workingnomads.com/"),
                ) as resp:
                    if resp.status != 200:
                        logger.info("WorkingNomads: API returned %d — trying browser", resp.status)
                        return []
                    data = await resp.json()

            items = data if isinstance(data, list) else data.get("jobs", [])
            for item in items:
                try:
                    title = item.get("title", "")
                    company = item.get("company_name", "") or item.get("company", "")
                    desc = item.get("description", "") or ""
                    url = item.get("url", "") or item.get("apply_url", "")
                    location = item.get("location", "Remote")
                    pub_date = item.get("published_at", "") or item.get("created_at", "")

                    if not title or not company:
                        continue

                    job_id = hashlib.sha256(f"wn:{company}:{title}".encode()).hexdigest()[:16]
                    posted_at = None
                    if pub_date:
                        try:
                            posted_at = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                        except Exception:
                            pass

                    jobs.append(Job(
                        job_id=job_id,
                        title=title,
                        company=company,
                        description=desc[:2000],
                        location=location or "Remote",
                        remote_type="Remote",
                        source="WorkingNomads",
                        apply_url=url,
                        posted_at=posted_at,
                    ))
                except Exception:
                    continue
        except Exception:
            logger.debug("WorkingNomads: HTTP fetch failed")
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        """Fallback: use Playwright browser to render the WorkingNomads job listing page."""
        jobs: list[Job] = []
        if not self._browser:
            return jobs

        page = await self._browser.new_page()
        try:
            url = "https://www.workingnomads.com/jobs?remote=true"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            try:
                await page.wait_for_selector(
                    "article, .job-card, .job-listing, div[class*='job'], li[class*='job'], "
                    "[data-testid*='job'], .card",
                    timeout=15000,
                )
            except Exception:
                logger.debug("WorkingNomads: no job cards found in browser")
                return []

            # Scroll to load more
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 700)")
                await page.wait_for_timeout(1500)

            # Extract jobs from DOM
            data = await page.evaluate("""() => {
                const cards = document.querySelectorAll('article, .job-card, .job-listing, div[class*="job-"], li[class*="job-"], [data-testid*="job"]');
                const seen = new Set();
                return Array.from(cards).slice(0, 30).map(card => {
                    const link = card.querySelector('a[href]');
                    if (!link) return null;
                    const href = link.href || '';
                    if (seen.has(href) || !href) return null;
                    seen.add(href);
                    const text = card.textContent.trim();
                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 2);
                    let title = lines.find(l => l.length > 3 && l.length < 150) || '';
                    let company = lines.find(l => l !== title && l.length > 2 && l.length < 100) || '';
                    return { title: title.slice(0, 150), url: href, company: company.slice(0, 100) };
                }).filter(j => j && j.title);
            }""")

            for item in data:
                try:
                    if not item:
                        continue
                    job_id = hashlib.sha256(f"wnb:{item['url']}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id, title=item["title"],
                        company=item.get("company", "Working Nomads"),
                        description="", location="Remote",
                        remote_type="Remote", source="WorkingNomads",
                        apply_url=item["url"],
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.warning("WorkingNomads browser fetch failed: %s", e)
        finally:
            await page.close()

        return jobs
