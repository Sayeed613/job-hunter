"""RemoteOK job provider — fetches remote jobs from remoteok.com/api."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_REMOTEOK_API = "https://remoteok.com/api"


class RemoteOKProvider(BaseJobProvider):
    """Fetches remote jobs from RemoteOK's free JSON API (no auth required)."""

    @property
    def name(self) -> str:
        return "RemoteOK"

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
                async with session.get(_REMOTEOK_API, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.warning("RemoteOK returned status %d", resp.status)
                        return []
                    data = await resp.json()

            for item in data:
                try:
                    if not isinstance(item, dict) or "slug" not in item:
                        continue

                    raw_id = item.get("slug", "") or item.get("id", "")
                    job_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]

                    # Parse tags as required skills
                    tags = item.get("tags", [])
                    skills = [t.get("value", "") for t in tags if isinstance(t, dict)]

                    job = Job(
                        job_id=job_id,
                        title=item.get("position", "Unknown"),
                        company=item.get("company", "Unknown"),
                        description=item.get("description", ""),
                        location=item.get("location", "Remote"),
                        remote_type="Remote",
                        job_type="Full-time",
                        salary=item.get("salary", ""),
                        source="RemoteOK",
                        apply_url=item.get("url", ""),
                        posted_at=datetime.fromtimestamp(
                            int(item.get("epoch", 0)), tz=timezone.utc
                        ) if item.get("epoch") else None,
                        skills_required=skills,
                    )
                    jobs.append(job)

                except Exception:
                    continue

            logger.info("RemoteOK: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("RemoteOK fetch failed")
        return jobs
