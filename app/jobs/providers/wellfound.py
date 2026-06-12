"""Wellfound (AngelList) job provider — fetches startup jobs from the public API."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_WELLFOUND_API = "https://wellfound.com/api/v1/jobs?remote=true&roles=engineer"


class WellfoundProvider(BaseJobProvider):
    """Fetches startup jobs from Wellfound (AngelList).

    Filters: remote jobs, engineering roles.
    """

    @property
    def name(self) -> str:
        return "Wellfound"

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
                    _WELLFOUND_API,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Wellfound returned status %d", resp.status)
                        return []
                    data = await resp.json()

            # Wellfound returns different response formats depending on the endpoint
            items = data if isinstance(data, list) else data.get("jobs", data.get("data", []))

            for item in items:
                try:
                    if isinstance(item, dict):
                        title = item.get("title") or item.get("role") or ""
                        company = (
                            item.get("company", {}).get("name", "")
                            if isinstance(item.get("company"), dict)
                            else item.get("startup_name", "")
                        )
                        desc = item.get("description", "") or ""
                        location = item.get("location", "Remote")
                        url = item.get("url") or item.get("apply_url") or ""
                        salary = item.get("salary", "")

                        if not title or not company:
                            continue

                        job_id = hashlib.sha256(f"wellfound:{company}:{title}".encode()).hexdigest()[:16]
                        job = Job(
                            job_id=job_id,
                            title=title,
                            company=company,
                            description=desc[:2000],
                            location=location or "Remote",
                            remote_type="Remote",
                            source="Wellfound",
                            apply_url=url,
                            salary=str(salary) if salary else None,
                        )
                        jobs.append(job)
                except Exception:
                    continue

            logger.info("Wellfound: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Wellfound fetch failed")
        return jobs
