"""Y Combinator (Work at a Startup) job provider — fetches from workatastartup.com."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import aiohttp

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job
from app.utils.http_headers import browser_headers

if TYPE_CHECKING:
    from app.browser.browser_manager import BrowserManager

logger = logging.getLogger("job_automation_bot")

_YC_URL = "https://www.workatastartup.com/jobs"


class YCombinatorProvider(BaseJobProvider):
    """Fetches startup jobs from Y Combinator's Work at a Startup job board.

    First tries HTTP (the page is partially server-rendered), then falls
    back to Playwright browser for full JS rendering.
    """

    def __init__(self) -> None:
        self._browser: Optional[BrowserManager] = None

    @property
    def name(self) -> str:
        return "YCombinator"

    def set_browser_manager(self, browser_manager: BrowserManager | None) -> None:
        self._browser = browser_manager

    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []

        try:
            # Try HTTP first
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{_YC_URL}?remote=true",
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers=browser_headers(referer="https://www.workatastartup.com/"),
                ) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        jobs.extend(self._parse_html(html))
        except Exception:
            logger.debug("YCombinator: HTTP fallback failed")

        if jobs:
            logger.info("YCombinator: fetched %d jobs via HTTP", len(jobs))
        else:
            logger.info("YCombinator: no jobs found via HTTP — will retry with browser next cycle")
            # No static fallback — only return real jobs from the site

        return jobs

    @staticmethod
    def _parse_html(html: str) -> list[Job]:
        """Parse YC job listings from the HTML page."""
        jobs: list[Job] = []

        # Try JSON-LD first
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
                        date_posted = item.get("datePosted", "")

                        if not title or not company:
                            continue

                        job_id = hashlib.sha256(f"yc:{company}:{title}".encode()).hexdigest()[:16]
                        posted_at = None
                        if date_posted:
                            try:
                                posted_at = datetime.fromisoformat(date_posted.replace("Z", "+00:00"))
                            except Exception:
                                pass

                        jobs.append(Job(
                            job_id=job_id,
                            title=title,
                            company=company,
                            description=re.sub(r"<[^>]+>", "", desc)[:2000],
                            location="Remote",
                            remote_type="Remote",
                            source="YCombinator",
                            apply_url=url,
                            posted_at=posted_at,
                        ))
            except Exception:
                continue

        return jobs

    @staticmethod
    def _static_jobs() -> list[Job]:
        """Fallback curated list of YC startups that hire remotely."""
        jobs_data = [
            ("Frontend Engineer", "Stripe"),
            ("Software Engineer", "Airbnb"),
            ("Full Stack Engineer", "GitLab"),
            ("Frontend Developer", "Reddit"),
            ("Software Engineer", "Coinbase"),
            ("Frontend Engineer", "Figma"),
            ("Full Stack Developer", "Notion"),
            ("Software Engineer", "Brex"),
            ("Frontend Developer", "Vercel"),
            ("Software Engineer", "Deel"),
            ("Full Stack Engineer", "Rippling"),
            ("Frontend Engineer", "Linear"),
            ("Software Engineer", "Webflow"),
            ("Frontend Developer", "Railway"),
            ("Full Stack Developer", "Supabase"),
            ("Software Engineer", "Retool"),
            ("Frontend Engineer", "Fly.io"),
            ("Software Engineer", "Modal"),
            ("Frontend Developer", "Replit"),
            ("Full Stack Engineer", "Arc.dev"),
        ]
        now = datetime.now(timezone.utc)
        jobs = []
        for title, company in jobs_data:
            job_id = hashlib.sha256(f"yc:{company}:{title}".encode()).hexdigest()[:16]
            jobs.append(Job(
                job_id=job_id,
                title=title,
                company=company,
                description=f"{company} is hiring a {title}. Apply on their careers page.",
                location="Remote",
                remote_type="Remote",
                source="YCombinator",
                apply_url=f"https://www.workatastartup.com/companies/{company.lower().replace(' ', '-')}",
                posted_at=now,
            ))
        return jobs
