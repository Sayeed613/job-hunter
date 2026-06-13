"""EuropeRemotely job provider — fetches remote jobs from europeremotely.com."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job
from app.utils.http_headers import browser_headers

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_EUROPE_URL = "https://europeremotely.com"


class EuropeRemotelyProvider(BaseJobProvider):
    """Fetches remote jobs from EuropeRemotely. Falls back to browser when HTTP is blocked."""

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "EuropeRemotely"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs = await self._fetch_http()
        if jobs:
            logger.info("EuropeRemotely: fetched %d jobs via HTTP", len(jobs))
            return jobs

        if self._browser and self._browser.is_launched:
            jobs = await self._fetch_browser()
            logger.info("EuropeRemotely: fetched %d jobs via browser", len(jobs))
        else:
            logger.warning("EuropeRemotely: HTTP blocked and no browser available")

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
                    _EUROPE_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers=browser_headers(referer="https://europeremotely.com/"),
                ) as resp:
                    if resp.status != 200:
                        logger.info("EuropeRemotely: HTTP returned %d — trying browser", resp.status)
                        return []
                    html = await resp.text()

            # Parse job cards
            job_cards = re.findall(
                r'<div[^>]*class="[^"]*job[^"]*"[^>]*>(.*?)</div>',
                html, re.DOTALL,
            )
            for card in job_cards[:40]:
                try:
                    title_match = re.search(r'<h[23][^>]*>(.*?)</h[23]>', card, re.DOTALL)
                    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""

                    link_match = re.search(r'href="(https?://[^"]+)"', card)
                    url = link_match.group(1) if link_match else ""

                    company_match = re.search(
                        r'class="[^"]*company[^"]*"[^>]*>(.*?)<',
                        card, re.DOTALL,
                    )
                    company = re.sub(r"<[^>]+>", "", company_match.group(1)).strip() if company_match else ""

                    if not title:
                        continue
                    if not company:
                        company = "Europe Remotely"

                    job_id = hashlib.sha256(f"europe:{company}:{title}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id, title=title, company=company,
                        description="", location="Remote Europe",
                        remote_type="Remote", source="EuropeRemotely",
                        apply_url=url, posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue
        except Exception:
            logger.debug("EuropeRemotely: HTTP fetch failed")
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        """Fallback: use Playwright browser to render europeremotely.com."""
        jobs: list[Job] = []
        if not self._browser:
            return jobs

        page = await self._browser.new_page()
        try:
            await page.goto(_EUROPE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            try:
                await page.wait_for_selector(
                    "article, .job-card, .listing, div[class*='job'], "
                    "[data-testid*='job'], .card, main a[href]",
                    timeout=15000,
                )
            except Exception:
                logger.debug("EuropeRemotely: no job cards found in browser")
                return []

            # Scroll to load more
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 700)")
                await page.wait_for_timeout(1500)

            # Extract jobs from DOM
            data = await page.evaluate("""() => {
                const cards = document.querySelectorAll('article, .job-card, .listing, div[class*="job-"], [data-testid*="job"], main a[href*="/job"]');
                const seen = new Set();
                return Array.from(cards).slice(0, 30).map(card => {
                    const link = card.tagName === 'A' ? card : card.querySelector('a[href]');
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
                    job_id = hashlib.sha256(f"erb:{item['url']}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id, title=item["title"],
                        company=item.get("company", "Europe Remotely"),
                        description="", location="Remote Europe",
                        remote_type="Remote", source="EuropeRemotely",
                        apply_url=item["url"],
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.warning("EuropeRemotely browser fetch failed: %s", e)
        finally:
            await page.close()

        return jobs
