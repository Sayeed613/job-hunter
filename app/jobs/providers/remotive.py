"""Remotive job provider — fetches remote jobs from remotive.com/api."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job
from app.utils.network import is_network_restricted_error, network_error_summary

logger = logging.getLogger("job_automation_bot")

_REMOTIVE_API = "https://remotive.com/api/remote-jobs?limit=100"


class RemotiveProvider(BaseJobProvider):
    """Fetches remote jobs from Remotive's free public JSON API."""

    @property
    def name(self) -> str:
        return "Remotive"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        reraise=False,
    )
    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _REMOTIVE_API,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Remotive returned status %d", resp.status)
                        return []
                    data = await resp.json()

            for item in data.get("jobs", []):
                try:
                    title = item.get("title", "")
                    company = item.get("company_name", "")
                    desc = item.get("description", "") or ""
                    url = item.get("url", "")
                    location = item.get("candidate_required_location", "Remote")
                    salary = item.get("salary", "")
                    pub_date = item.get("publication_date", "")

                    if not title or not company:
                        continue

                    job_id = hashlib.sha256(f"remotive:{company}:{title}".encode()).hexdigest()[:16]
                    posted_at = None
                    if pub_date:
                        try:
                            posted_at = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                        except Exception:
                            pass

                    tags = item.get("tags", [])
                    skills = [t for t in tags if isinstance(t, str)] if tags else []

                    jobs.append(Job(
                        job_id=job_id,
                        title=title,
                        company=company,
                        description=desc[:2000],
                        location=location or "Remote",
                        remote_type="Remote",
                        job_type=item.get("job_type", "Full-time"),
                        salary=str(salary) if salary else None,
                        source="Remotive",
                        apply_url=url,
                        posted_at=posted_at,
                        skills_required=skills,
                    ))
                except Exception:
                    continue

            logger.info("Remotive: fetched %d jobs", len(jobs))
        except Exception as exc:
            if is_network_restricted_error(exc):
                logger.warning(
                    "Remotive skipped due to blocked network access: %s",
                    network_error_summary(exc),
                )
            else:
                logger.exception("Remotive fetch failed")
        return jobs
