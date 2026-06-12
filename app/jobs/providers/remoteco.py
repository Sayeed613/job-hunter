"""Remote.co job provider — fetches remote jobs from remote.co."""

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

_REMOTECO_URL = "https://remote.co/remote-jobs/"


class RemoteCoProvider(BaseJobProvider):
    """Fetches remote jobs from Remote.co (curated remote job board)."""

    @property
    def name(self) -> str:
        return "RemoteCo"

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
                    _REMOTECO_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning("RemoteCo returned status %d — skipping", resp.status)
                        return []
                    html = await resp.text()

            # Parse job listing rows
            job_rows = re.findall(
                r'<tr[^>]*>(.*?)</tr>',
                html, re.DOTALL,
            )
            for row in job_rows[:50]:
                try:
                    cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                    if len(cells) < 3:
                        continue

                    # First cell usually has the job title + link
                    link_match = re.search(
                        r'href="(https?://remote\.co[^"]*)"[^>]*>(.*?)</a>',
                        cells[0], re.DOTALL,
                    )
                    if not link_match:
                        continue

                    url = link_match.group(1)
                    title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()

                    # Second cell: company
                    company = re.sub(r"<[^>]+>", "", cells[1]).strip() if len(cells) > 1 else ""

                    # Third cell: category/location
                    location = re.sub(r"<[^>]+>", "", cells[2]).strip() if len(cells) > 2 else "Remote"

                    if not title or not company:
                        continue

                    job_id = hashlib.sha256(f"remote.co:{company}:{title}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id,
                        title=title,
                        company=company,
                        description="",
                        location=location or "Remote",
                        remote_type="Remote",
                        source="RemoteCo",
                        apply_url=url,
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

            logger.info("RemoteCo: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("RemoteCo fetch failed")
        return jobs
