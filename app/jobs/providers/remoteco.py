"""Remote.co job provider — fetches remote jobs from remote.co."""

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

_REMOTECO_URL = "https://remote.co/remote-jobs/"


class RemoteCoProvider(BaseJobProvider):
    """Fetches remote jobs from Remote.co. Falls back to browser when HTTP is blocked."""

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "RemoteCo"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs = await self._fetch_http()
        if jobs:
            logger.info("RemoteCo: fetched %d jobs via HTTP", len(jobs))
            return jobs

        if self._browser and self._browser.is_launched:
            jobs = await self._fetch_browser()
            logger.info("RemoteCo: fetched %d jobs via browser", len(jobs))
        else:
            logger.warning("RemoteCo: HTTP blocked and no browser available")

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
                    _REMOTECO_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers=browser_headers(referer="https://remote.co/"),
                ) as resp:
                    if resp.status != 200:
                        logger.info("RemoteCo: HTTP returned %d — trying browser", resp.status)
                        return []
                    html = await resp.text()

            # Parse job listing rows
            job_rows = re.findall(
                r'<tr[^>]*>(.*?)</tr>',
                html, re.DOTALL,
            )
            for row in job_rows[:50]:
                try:
                    cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                    if len(cells) < 3:
                        continue

                    link_match = re.search(
                        r'href="(https?://remote\.co[^"]*)"[^>]*>(.*?)</a>',
                        cells[0], re.DOTALL,
                    )
                    if not link_match:
                        continue

                    url = link_match.group(1)
                    title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()
                    company = re.sub(r"<[^>]+>", "", cells[1]).strip() if len(cells) > 1 else ""
                    location = re.sub(r"<[^>]+>", "", cells[2]).strip() if len(cells) > 2 else "Remote"

                    if not title or not company:
                        continue

                    job_id = hashlib.sha256(f"remote.co:{company}:{title}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id, title=title, company=company,
                        description="", location=location or "Remote",
                        remote_type="Remote", source="RemoteCo",
                        apply_url=url, posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue
        except Exception:
            logger.debug("RemoteCo: HTTP fetch failed")
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        """Fallback: use Playwright browser to render remote.co job listings."""
        jobs: list[Job] = []
        if not self._browser:
            return jobs

        page = await self._browser.new_page()
        try:
            await page.goto(_REMOTECO_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            try:
                await page.wait_for_selector(
                    "article, .job-card, .listing, tr, div[class*='job'], "
                    "[data-testid*='job'], .card",
                    timeout=15000,
                )
            except Exception:
                logger.debug("RemoteCo: no job cards found in browser")
                return []

            # Scroll to load more
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 700)")
                await page.wait_for_timeout(1500)

            # Extract jobs from DOM
            data = await page.evaluate("""() => {
                const cards = document.querySelectorAll('article, .job-card, .listing, tr, div[class*="job-"], [data-testid*="job"]');
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
                    job_id = hashlib.sha256(f"rcb:{item['url']}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id, title=item["title"],
                        company=item.get("company", "Remote.co"),
                        description="", location="Remote",
                        remote_type="Remote", source="RemoteCo",
                        apply_url=item["url"],
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.warning("RemoteCo browser fetch failed: %s", e)
        finally:
            await page.close()

        return jobs
