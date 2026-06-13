"""Jobspresso job provider — fetches remote jobs from jobspresso.co."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job
from app.utils.http_headers import browser_headers

logger = logging.getLogger("job_automation_bot")

_JOBSPRESSO_URL = "https://jobspresso.co/?s=react+python+frontend&remote=1"


class JobspressoProvider(BaseJobProvider):
    """Fetches remote jobs from Jobspresso (curated remote job board)."""

    @property
    def name(self) -> str:
        return "Jobspresso"

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
                    _JOBSPRESSO_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers=browser_headers(referer="https://jobspresso.co/"),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Jobspresso returned status %d", resp.status)
                        return []
                    html = await resp.text()

            # Parse job cards
            # Jobspresso renders jobs in list items
            job_blocks = re.findall(
                r'<article[^>]*class="[^"]*job-list[^"]*"[^>]*>(.*?)</article>',
                html, re.DOTALL | re.IGNORECASE,
            )
            if not job_blocks:
                job_blocks = re.findall(
                    r'<div[^>]*class="[^"]*job[^"]*"[^>]*>(.*?)</div>',
                    html, re.DOTALL,
                )

            for block in job_blocks:
                try:
                    title_match = re.search(r'<h[23][^>]*>(.*?)</h[23]>', block, re.DOTALL)
                    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""

                    link_match = re.search(r'href="(https?://[^"]+)"', block)
                    url = link_match.group(1) if link_match else ""

                    company_match = re.search(
                        r'class="[^"]*company[^"]*"[^>]*>(.*?)<',
                        block, re.DOTALL | re.IGNORECASE,
                    )
                    company = re.sub(r"<[^>]+>", "", company_match.group(1)).strip() if company_match else ""

                    desc_match = re.search(
                        r'class="[^"]*(?:description|excerpt|text)[^"]*"[^>]*>(.*?)</div>',
                        block, re.DOTALL | re.IGNORECASE,
                    )
                    desc = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip() if desc_match else ""

                    loc_match = re.search(
                        r'class="[^"]*location[^"]*"[^>]*>(.*?)<',
                        block, re.DOTALL | re.IGNORECASE,
                    )
                    location = re.sub(r"<[^>]+>", "", loc_match.group(1)).strip() if loc_match else "Remote"

                    if not title or not company:
                        continue

                    job_id = hashlib.sha256(f"jobspresso:{company}:{title}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id,
                        title=title,
                        company=company,
                        description=desc[:2000],
                        location=location or "Remote",
                        remote_type="Remote",
                        source="Jobspresso",
                        apply_url=url,
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

            logger.info("Jobspresso: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Jobspresso fetch failed")
        return jobs
