"""Work at a Startup (YC) job provider — fetches jobs from workatastartup.com.

Requires a logged-in browser session (storage_state). The first run prompts
manual login via --relogin workatastartup. Subsequent runs load the saved session.

Job listings are rendered in a right-side panel. Apply flows may be:
  (a) In-site cover note form → fill and submit
  (b) External ATS link (Greenhouse/Lever/Ashby) → route through ApplicationRouter
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_YC_URL = "https://www.workatastartup.com/jobs"


class WorkAtAStartupProvider(BaseJobProvider):
    """Fetches startup jobs from Y Combinator's Work at a Startup platform.

    Session is managed at the browser level via storage_state. The provider
    checks for login walls and returns empty if not authenticated.
    Use --relogin workatastartup (coming in a future update) to re-authenticate.
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "WorkAtAStartup"

    @property
    def platform(self) -> str | None:
        return "workatastartup"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        if not self._browser or not self._browser.is_launched:
            logger.warning("WorkAtAStartup: no browser available")
            return []

        jobs = await self._fetch_browser()
        logger.info("WorkAtAStartup: fetched %d jobs", len(jobs))
        return jobs

    async def _fetch_browser(self) -> list[Job]:
        """Use Playwright browser to render YC job listings."""
        jobs: list[Job] = []
        if not self._browser:
            return jobs

        page = await self._browser.new_page()
        try:
            url = f"{_YC_URL}?remote=true"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            # Check for login wall
            if "login" in page.url.lower() or "signin" in page.url.lower():
                logger.warning("WorkAtAStartup: login wall — session may have expired")
                return []

            # Wait for job cards
            try:
                await page.wait_for_selector(
                    "a[href*='/companies/'], [data-testid*='job-card'], "
                    ".job-card, .job-listing, article, div[class*='job']",
                    timeout=15000,
                )
            except Exception:
                logger.debug("WorkAtAStartup: no job cards found")
                return []

            # Scroll to load more
            for _ in range(5):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(1500)

            # Extract jobs from DOM
            data = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/companies/"]');
                const seen = new Set();
                return Array.from(links).slice(0, 50).map(link => {
                    const href = link.href || '';
                    if (seen.has(href)) return null;
                    seen.add(href);
                    const card = link.closest('[data-testid*="card"], .job-card, article, div[class*="job"], li') || link.parentElement;
                    const text = card ? card.textContent : link.textContent;
                    const lines = text.split('\\\\n').map(l => l.trim()).filter(l => l.length > 2);
                    let title = lines.find(l => l.length > 3 && l.length < 150) || '';
                    let company = lines.find(l => l !== title && l.length > 2 && l.length < 100) || 'YC Startup';
                    // Try to find YC batch info
                    const batch = lines.find(l => /\\\\b[WS]\\\\d{2}\\\\b/.test(l)) || '';
                    return { title: title.slice(0, 150), url: href, company: company.slice(0, 100), batch };
                }).filter(j => j && j.title);
            }""")

            for item in data:
                try:
                    if not item:
                        continue
                    job_id = hashlib.sha256(f"ycb:{item['url']}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id,
                        title=item["title"],
                        company=item.get("company", "YC Startup"),
                        description="",
                        location="Remote",
                        remote_type="Remote",
                        source="WorkAtAStartup",
                        apply_url=item["url"],
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

        except Exception as e:
            logger.warning("WorkAtAStartup browser fetch failed: %s", e)
        finally:
            await page.close()

        return jobs
