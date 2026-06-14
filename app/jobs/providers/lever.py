"""Lever job provider — fetches jobs from company Lever job boards via their public API.

Uses the Lever Postings API (no auth required):
  GET https://api.lever.co/v0/postings/{company}?mode=json

For each matching job, the apply URL points to Lever's hosted application form
which the ApplicationRouter handles via _standard_apply (lever route).
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

_LEVER_API = "https://api.lever.co/v0/postings"


class LeverProvider(BaseJobProvider):
    """Fetches jobs from company Lever job boards via the public API.

    Reads company slugs from config/lever_companies.txt.
    Each slug corresponds to a board at https://jobs.lever.co/{slug}.
    """

    def __init__(self) -> None:
        self._companies: list[str] = []

    @property
    def name(self) -> str:
        return "Lever"

    async def fetch_jobs(self) -> list[Job]:
        companies = self._load_companies()
        if not companies:
            logger.warning("Lever: no company slugs loaded — check config/lever_companies.txt")
            return []

        jobs: list[Job] = []
        for company in companies:
            try:
                page_jobs = await self._fetch_company(company)
                jobs.extend(page_jobs)
            except Exception:
                logger.debug("Lever: failed for %s", company, exc_info=True)
                continue

        logger.info("Lever: fetched %d jobs from %d companies", len(jobs), len(companies))
        return jobs

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        reraise=False,
    )
    async def _fetch_company(self, company: str) -> list[Job]:
        """Fetch jobs for a single Lever company board."""
        jobs: list[Job] = []
        url = f"{_LEVER_API}/{company}?mode=json"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
                headers=api_headers(referer=f"https://jobs.lever.co/{company}/"),
            ) as resp:
                if resp.status != 200:
                    logger.debug("Lever: %s returned %d", company, resp.status)
                    return []
                data = await resp.json()

        # Lever returns a JSON array of postings
        items = data if isinstance(data, list) else []

        for item in items:
            try:
                title = item.get("text", "")
                if not title:
                    continue

                # Categories
                categories = item.get("categories", {}) or {}
                location = categories.get("location", "") or ""
                commitment = categories.get("commitment", "") or ""
                team = categories.get("team", "") or ""

                # Company name from categories or slug
                company_name = self._slug_to_name(company)
                dept = categories.get("department", "")
                # Lever data doesn't include company name in posting — use slug conversion

                # Description — use plaintext version
                desc = item.get("descriptionPlain", "") or ""

                # Apply URL — hosted Lever application form
                apply_url = item.get("applyUrl", "") or item.get("hostedUrl", "")
                if not apply_url:
                    continue

                # Workplace type
                workplace_type = item.get("workplaceType", "") or ""

                # Salary range
                salary_range = item.get("salaryRange", {}) or {}
                salary_str = ""
                if salary_range:
                    currency = salary_range.get("currency", "") or ""
                    interval = salary_range.get("interval", "") or ""
                    min_val = salary_range.get("min")
                    max_val = salary_range.get("max")
                    if min_val or max_val:
                        parts = [currency] if currency else []
                        if min_val:
                            parts.append(f"${min_val:,.0f}")
                        if max_val:
                            parts.append(f"${max_val:,.0f}")
                        if interval:
                            parts.append(f"/{interval.lower()}")
                        salary_str = " ".join(parts)

                job_id = hashlib.sha256(f"lever:{company}:{title}".encode()).hexdigest()[:16]

                # Determine remote type
                remote_type = "Remote" if "remote" in (location + workplace_type).lower() else "Hybrid" if "hybrid" in (location + workplace_type).lower() else "On-site"
                if not remote_type and "remote" in categories.get("allLocations", "").lower():
                    remote_type = "Remote"

                job_type = "Full-time"
                if commitment:
                    if "part" in commitment.lower():
                        job_type = "Part-time"
                    elif "contract" in commitment.lower():
                        job_type = "Contract"
                    elif "intern" in commitment.lower():
                        job_type = "Internship"

                jobs.append(Job(
                    job_id=job_id,
                    title=title,
                    company=company_name,
                    description=desc[:2000],
                    location=location or "Remote",
                    remote_type=remote_type or "Remote",
                    job_type=job_type,
                    salary=salary_str or None,
                    source="Lever",
                    apply_url=apply_url,
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
        """Load company slugs from config/lever_companies.txt."""
        path = Path("config/lever_companies.txt")
        if not path.exists():
            logger.warning("Lever companies file not found at %s", path)
            return []
        slugs: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            slugs.append(line)
        return slugs
