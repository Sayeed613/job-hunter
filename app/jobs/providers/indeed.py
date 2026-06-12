"""Indeed job provider — fetches jobs from Indeed search pages."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_INDEED_SEARCH = "https://www.indeed.com/jobs?q={keywords}&l={location}&fromage=1&remotejob=1"


class IndeedProvider(BaseJobProvider):
    """Fetches jobs from Indeed using URL-based search.

    Filters: posted within 1 day, remote jobs.
    """

    @property
    def name(self) -> str:
        return "Indeed"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        reraise=False,
    )
    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        keywords = ["React", "Python", "Frontend", "Full Stack", "Node.js"]
        locations = ["Bangalore", "Remote"]

        try:
            async with aiohttp.ClientSession() as session:
                for kw in keywords:
                    for loc in locations:
                        url = _INDEED_SEARCH.format(
                            keywords=kw.replace(" ", "+"),
                            location=loc.replace(" ", "+"),
                        )
                        try:
                            async with session.get(
                                url,
                                timeout=aiohttp.ClientTimeout(total=15),
                                headers={"User-Agent": "Mozilla/5.0"},
                            ) as resp:
                                if resp.status != 200:
                                    continue
                                html = await resp.text()
                                jobs.extend(self._parse_jobs(html, kw))
                        except Exception:
                            continue

            logger.info("Indeed: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Indeed fetch failed")
        return jobs

    @staticmethod
    def _parse_jobs(html: str, keyword: str) -> list[Job]:
        jobs: list[Job] = []
        # Extract JSON-LD structured data
        json_ld_pattern = re.compile(
            r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
        )
        import json

        for match in json_ld_pattern.finditer(html):
            try:
                data = json.loads(match.group(1))
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        title = item.get("title", "")
                        company = item.get("hiringOrganization", {}).get("name", "")
                        desc = item.get("description", "") or ""
                        location = item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                        url = item.get("url", "")
                        date_posted = item.get("datePosted", "")

                        if not title or not company:
                            continue

                        job_id = hashlib.sha256(f"indeed:{company}:{title}".encode()).hexdigest()[:16]
                        posted_at = None
                        if date_posted:
                            try:
                                posted_at = datetime.fromisoformat(date_posted.replace("Z", "+00:00"))
                            except Exception:
                                pass

                        clean_desc = re.sub(r"<[^>]+>", "", desc)[:2000]
                        job = Job(
                            job_id=job_id,
                            title=title,
                            company=company,
                            description=clean_desc,
                            location=location or "Remote",
                            remote_type="Remote",
                            source="Indeed",
                            apply_url=url,
                            posted_at=posted_at,
                        )
                        jobs.append(job)
            except Exception:
                continue
        return jobs
