"""Findwork job provider — fetches developer jobs from findwork.dev API."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_FINDWORK_API = "https://findwork.dev/api/jobs/?remote=true&sort_by=date&search=frontend+react+python"


class FindworkProvider(BaseJobProvider):
    """Fetches developer jobs from Findwork's free API (no auth needed for basic use)."""

    @property
    def name(self) -> str:
        return "Findwork"

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
                    _FINDWORK_API,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "application/json",
                    },
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Findwork returned status %d — API may need auth", resp.status)
                        return []
                    data = await resp.json()

            for item in data.get("results", []):
                try:
                    title = item.get("role", "") or item.get("title", "")
                    company = item.get("company_name", "")
                    desc = item.get("text", "") or ""
                    url = item.get("url", "")
                    location = item.get("location", "Remote")
                    salary = item.get("salary", "")
                    pub_date = item.get("date_posted", "")

                    if not title or not company:
                        continue

                    job_id = hashlib.sha256(f"findwork:{company}:{title}".encode()).hexdigest()[:16]
                    posted_at = None
                    if pub_date:
                        try:
                            posted_at = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                        except Exception:
                            pass

                    jobs.append(Job(
                        job_id=job_id,
                        title=title,
                        company=company,
                        description=desc[:2000],
                        location=location or "Remote",
                        remote_type="Remote",
                        source="Findwork",
                        apply_url=url,
                        salary=str(salary) if salary else None,
                        posted_at=posted_at,
                    ))
                except Exception:
                    continue

            logger.info("Findwork: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Findwork fetch failed")
        return jobs
