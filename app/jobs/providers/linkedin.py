"""LinkedIn job provider — fetches jobs from LinkedIn via RSS feed and search URLs."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_LINKEDIN_RSS = "https://www.linkedin.com/jobs/search/?keywords={keywords}&location={location}&f_TPR=r86400&f_WT=2&f_AL=true"


class LinkedInProvider(BaseJobProvider):
    """Fetches LinkedIn jobs using the public job search page.

    Uses RSS/HTML parsing since LinkedIn has no public API.
    Filters: last 24 hours, remote, India.
    """

    @property
    def name(self) -> str:
        return "LinkedIn"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        reraise=False,
    )
    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        keywords = ["React", "Python", "Frontend", "Full Stack", "Backend"]
        locations = ["Bangalore", "India", "Remote"]

        try:
            async with aiohttp.ClientSession() as session:
                for kw in keywords:
                    for loc in locations:
                        url = _LINKEDIN_RSS.format(keywords=kw, location=loc.replace(" ", "+"))
                        try:
                            async with session.get(
                                url,
                                timeout=aiohttp.ClientTimeout(total=15),
                                headers={"User-Agent": "Mozilla/5.0"},
                            ) as resp:
                                if resp.status != 200:
                                    continue
                                html = await resp.text()
                                # Simple extraction: look for job listings in the HTML
                                jobs_from_html = self._parse_jobs_from_html(html, kw)
                                jobs.extend(jobs_from_html)
                        except Exception:
                            continue

            logger.info("LinkedIn: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("LinkedIn fetch failed")
        return jobs

    @staticmethod
    def _parse_jobs_from_html(html: str, keyword: str) -> list[Job]:
        """Basic HTML parsing for LinkedIn job search results."""
        jobs: list[Job] = []
        import re

        # Try to extract job cards using common patterns
        # LinkedIn uses JSON-LD structured data
        json_ld_pattern = re.compile(
            r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
        )
        import json

        for match in json_ld_pattern.finditer(html):
            try:
                data = json.loads(match.group(1))
                if isinstance(data, dict) and data.get("@type") == "ItemList":
                    for item in data.get("itemListElement", []):
                        job_data = item.get("item", {})
                        title = job_data.get("title", "")
                        company = job_data.get("hiringOrganization", {}).get("name", "")
                        description = job_data.get("description", "") or ""
                        location = job_data.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                        url = job_data.get("url", "")
                        date_posted = job_data.get("datePosted", "")

                        if not title or not company:
                            continue

                        job_id = hashlib.sha256(f"{company}:{title}".encode()).hexdigest()[:16]
                        posted_at = None
                        if date_posted:
                            try:
                                posted_at = datetime.fromisoformat(date_posted.replace("Z", "+00:00"))
                            except Exception:
                                pass

                        job = Job(
                            job_id=job_id,
                            title=title,
                            company=company,
                            description=description[:2000],
                            location=location or "Remote",
                            remote_type="Remote",
                            source="LinkedIn",
                            apply_url=url,
                            posted_at=posted_at,
                        )
                        jobs.append(job)
            except Exception:
                continue

        return jobs
