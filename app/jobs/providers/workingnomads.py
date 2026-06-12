"""Working Nomads job provider — fetches remote jobs from workingnomads.com."""

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

_WN_URL = "https://www.workingnomads.com/api/jobs?remote=true"


class WorkingNomadsProvider(BaseJobProvider):
    """Fetches remote jobs from Working Nomads API."""

    @property
    def name(self) -> str:
        return "WorkingNomads"

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
                    _WN_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning("WorkingNomads returned status %d — site may be blocking", resp.status)
                        return []
                    data = await resp.json()

            items = data if isinstance(data, list) else data.get("jobs", [])
            for item in items:
                try:
                    title = item.get("title", "")
                    company = item.get("company_name", "") or item.get("company", "")
                    desc = item.get("description", "") or ""
                    url = item.get("url", "") or item.get("apply_url", "")
                    location = item.get("location", "Remote")
                    pub_date = item.get("published_at", "") or item.get("created_at", "")

                    if not title or not company:
                        continue

                    job_id = hashlib.sha256(f"wn:{company}:{title}".encode()).hexdigest()[:16]
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
                        source="WorkingNomads",
                        apply_url=url,
                        posted_at=posted_at,
                    ))
                except Exception:
                    continue

            logger.info("WorkingNomads: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("WorkingNomads fetch failed")
        return jobs
