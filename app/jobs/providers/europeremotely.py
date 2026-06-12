"""EuropeRemotely job provider — fetches remote jobs from europeremotely.com."""

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

_EUROPE_URL = "https://europeremotely.com"


class EuropeRemotelyProvider(BaseJobProvider):
    """Fetches remote jobs from EuropeRemotely (European remote job board)."""

    @property
    def name(self) -> str:
        return "EuropeRemotely"

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
                    _EUROPE_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning("EuropeRemotely returned status %d — skipping", resp.status)
                        return []
                    html = await resp.text()

            # Parse job cards
            job_cards = re.findall(
                r'<div[^>]*class="[^"]*job[^"]*"[^>]*>(.*?)</div>',
                html, re.DOTALL,
            )
            for card in job_cards[:40]:
                try:
                    title_match = re.search(r'<h[23][^>]*>(.*?)</h[23]>', card, re.DOTALL)
                    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""

                    link_match = re.search(r'href="(https?://[^"]+)"', card)
                    url = link_match.group(1) if link_match else ""

                    company_match = re.search(
                        r'class="[^"]*company[^"]*"[^>]*>(.*?)<',
                        card, re.DOTALL,
                    )
                    company = re.sub(r"<[^>]+>", "", company_match.group(1)).strip() if company_match else ""

                    if not title:
                        continue
                    if not company:
                        company = "Europe Remotely"

                    job_id = hashlib.sha256(f"europe:{company}:{title}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id,
                        title=title,
                        company=company,
                        description="",
                        location="Remote Europe",
                        remote_type="Remote",
                        source="EuropeRemotely",
                        apply_url=url,
                        posted_at=datetime.now(timezone.utc),
                    ))
                except Exception:
                    continue

            logger.info("EuropeRemotely: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("EuropeRemotely fetch failed")
        return jobs
