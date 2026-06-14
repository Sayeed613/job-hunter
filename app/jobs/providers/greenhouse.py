"""Greenhouse job provider — fetches jobs from company Greenhouse job boards via their public API.

Uses the Greenhouse Job Board API (no auth required):
  GET https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true

For each matching job, the apply URL points to the Greenhouse-hosted application form
which the ApplicationRouter handles via _standard_apply (greenhouse route).
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job
from app.utils.http_headers import api_headers

logger = logging.getLogger("job_automation_bot")

_GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseProvider(BaseJobProvider):
    """Fetches jobs from company Greenhouse job boards via the public API.

    Reads company slugs from config/greenhouse_companies.txt.
    Each slug corresponds to a board at https://boards.greenhouse.io/{slug}.
    """

    def __init__(self) -> None:
        self._companies: list[str] = []

    @property
    def name(self) -> str:
        return "Greenhouse"

    async def fetch_jobs(self) -> list[Job]:
        companies = self._load_companies()
        if not companies:
            logger.warning("Greenhouse: no company slugs loaded — check config/greenhouse_companies.txt")
            return []

        jobs: list[Job] = []
        for company in companies:
            try:
                page_jobs = await self._fetch_company(company)
                jobs.extend(page_jobs)
            except Exception:
                logger.debug("Greenhouse: failed for %s", company, exc_info=True)
                continue

        logger.info("Greenhouse: fetched %d jobs from %d companies", len(jobs), len(companies))
        return jobs

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        reraise=False,
    )
    async def _fetch_company(self, company: str) -> list[Job]:
        """Fetch jobs for a single Greenhouse company board."""
        jobs: list[Job] = []
        url = f"{_GREENHOUSE_API}/{company}/jobs?content=true"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
                headers=api_headers(referer=f"https://boards.greenhouse.io/{company}/"),
            ) as resp:
                if resp.status != 200:
                    logger.debug("Greenhouse: %s returned %d", company, resp.status)
                    return []
                data = await resp.json()

        for item in data.get("jobs", []):
            try:
                title = item.get("title", "")
                if not title:
                    continue

                # Extract company name from offices if available (more accurate than slug)
                offices = item.get("offices", [])
                office_names = [o.get("name", "") for o in offices if isinstance(o, dict)]
                company_name = office_names[0] if office_names else self._slug_to_name(company)

                # Location from the location object
                loc_obj = item.get("location", {}) or {}
                location = loc_obj.get("name", "") if isinstance(loc_obj, dict) else ""
                location = location or (", ".join(office_names) if office_names else "Remote")

                # Description — strip HTML tags
                desc_html = item.get("content", "") or ""
                desc = re.sub(r"<[^>]+>", "", desc_html).strip() if desc_html else ""

                # Apply URL
                apply_url = item.get("absolute_url", "")
                if not apply_url:
                    continue

                # Updated at → posted_at
                updated_at = item.get("updated_at", "")
                posted_at = None
                if updated_at:
                    try:
                        posted_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    except Exception:
                        pass

                job_id = hashlib.sha256(f"greenhouse:{company}:{title}".encode()).hexdigest()[:16]

                # Determine remote type from location
                remote_type = "Remote" if "remote" in location.lower() else "Hybrid" if "hybrid" in location.lower() else "On-site"

                jobs.append(Job(
                    job_id=job_id,
                    title=title,
                    company=company_name,
                    description=desc[:2000],
                    location=location or "Remote",
                    remote_type=remote_type,
                    job_type="Full-time",
                    source="Greenhouse",
                    apply_url=apply_url,
                    posted_at=posted_at,
                ))
            except Exception:
                continue

        return jobs

    @staticmethod
    def _slug_to_name(slug: str) -> str:
        """Convert a company slug to a readable name.

        'stripe' → 'Stripe', 'scale_ai' → 'Scale AI', 'dbt-labs' → 'dbt Labs'
        """
        name = slug.replace("-", " ").replace("_", " ").strip()
        # Handle camelCase slugs like "scaleai" → "Scaleai" (best effort)
        return name.title() if " " in name else name.capitalize()

    @staticmethod
    def _load_companies() -> list[str]:
        """Load company slugs from config/greenhouse_companies.txt."""
        path = Path("config/greenhouse_companies.txt")
        if not path.exists():
            logger.warning("Greenhouse companies file not found at %s", path)
            return []
        slugs: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            slugs.append(line)
        return slugs
