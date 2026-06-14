"""Ashby job provider — fetches jobs from company Ashby job boards via their public API.

Uses the Ashby Job Postings API (no auth required):
  GET https://api.ashbyhq.com/posting-api/job-board/{company}

For each matching job, the apply URL points to Ashby's hosted application form
which the ApplicationRouter handles via _standard_apply (ashby route).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job
from app.utils.http_headers import api_headers

logger = logging.getLogger("job_automation_bot")

_ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyProvider(BaseJobProvider):
    """Fetches jobs from company Ashby job boards via the public API.

    Reads company slugs from config/ashby_companies.txt.
    Each slug corresponds to a board at https://jobs.ashbyhq.com/{slug}.
    """

    def __init__(self) -> None:
        self._companies: list[str] = []

    @property
    def name(self) -> str:
        return "Ashby"

    async def fetch_jobs(self) -> list[Job]:
        companies = self._load_companies()
        if not companies:
            logger.warning("Ashby: no company slugs loaded — check config/ashby_companies.txt")
            return []

        jobs: list[Job] = []
        for company in companies:
            try:
                page_jobs = await self._fetch_company(company)
                jobs.extend(page_jobs)
            except Exception:
                logger.debug("Ashby: failed for %s", company, exc_info=True)
                continue

        logger.info("Ashby: fetched %d jobs from %d companies", len(jobs), len(companies))
        return jobs

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        reraise=False,
    )
    async def _fetch_company(self, company: str) -> list[Job]:
        """Fetch jobs for a single Ashby company board."""
        jobs: list[Job] = []
        url = f"{_ASHBY_API}/{company}"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
                headers=api_headers(referer=f"https://jobs.ashbyhq.com/{company}/"),
            ) as resp:
                if resp.status != 200:
                    logger.debug("Ashby: %s returned %d", company, resp.status)
                    return []
                data = await resp.json()

        # Ashby response may include the company name at the top level
        company_name = self._slug_to_name(company)

        for item in data.get("jobs", []):
            try:
                title = item.get("title", "")
                if not title:
                    continue

                # Location
                location = item.get("location", "") or ""

                # Secondary locations
                secondary = item.get("secondaryLocations", [])
                if secondary:
                    locs = [s.get("location", "") for s in secondary if isinstance(s, dict)]
                    if locs:
                        location = (location + "; " if location else "") + "; ".join(locs)

                # Description
                desc = item.get("descriptionPlain", "") or ""

                # Remote/workplace type
                is_remote = item.get("isRemote", False)
                workplace_type = item.get("workplaceType", "") or ""
                remote_type = "Remote" if (is_remote or "remote" in workplace_type.lower()) else "Hybrid" if "hybrid" in workplace_type.lower() else "On-site"

                # Apply URL
                apply_url = item.get("applyUrl", "") or item.get("jobUrl", "")
                if not apply_url:
                    continue

                # Employment type
                employment_type = item.get("employmentType", "") or "FullTime"
                job_type_map = {
                    "FullTime": "Full-time",
                    "PartTime": "Part-time",
                    "Intern": "Internship",
                    "Contract": "Contract",
                    "Temporary": "Contract",
                }
                job_type = job_type_map.get(employment_type, "Full-time")

                # Department / team
                department = item.get("department", "") or ""
                team = item.get("team", "") or ""
                # Use department as a tag for keyword matching
                tags = [t for t in [department, team] if t]

                # Published date
                published_at = item.get("publishedAt", "")
                posted_at = None
                if published_at:
                    try:
                        posted_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    except Exception:
                        pass

                job_id = hashlib.sha256(f"ashby:{company}:{title}".encode()).hexdigest()[:16]

                jobs.append(Job(
                    job_id=job_id,
                    title=title,
                    company=company_name,
                    description=desc[:2000],
                    location=location or "Remote",
                    remote_type=remote_type,
                    job_type=job_type,
                    source="Ashby",
                    apply_url=apply_url,
                    posted_at=posted_at,
                    skills_required=tags,
                ))
            except Exception:
                continue

        return jobs

    @staticmethod
    def _slug_to_name(slug: str) -> str:
        """Convert a company slug to a readable name."""
        name = slug.replace("-", " ").replace("_", " ").strip()
        return name.title() if " " in name else name.capitalize()

    @staticmethod
    def _load_companies() -> list[str]:
        """Load company slugs from config/ashby_companies.txt."""
        path = Path("config/ashby_companies.txt")
        if not path.exists():
            logger.warning("Ashby companies file not found at %s", path)
            return []
        slugs: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            slugs.append(line)
        return slugs
