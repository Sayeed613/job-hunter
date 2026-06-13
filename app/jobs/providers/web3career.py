"""Web3 Career job provider — fetches web3/blockchain remote jobs from web3.career."""

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

_WEB3_URL = "https://web3.career/remote-jobs"


class Web3CareerProvider(BaseJobProvider):
    """Fetches remote web3/blockchain jobs from Web3 Career."""

    @property
    def name(self) -> str:
        return "Web3Career"

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
                    _WEB3_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers=browser_headers(referer="https://web3.career/"),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Web3Career returned status %d", resp.status)
                        return []
                    html = await resp.text()

            # Web3.career uses a table-based layout
            # Try JSON-LD first
            json_ld = re.findall(
                r'<script type="application/ld\+json">(.*?)</script>',
                html, re.DOTALL,
            )
            import json
            for match in json_ld:
                try:
                    data = json.loads(match)
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if isinstance(item, dict) and item.get("@type") == "JobPosting":
                            title = item.get("title", "")
                            company = item.get("hiringOrganization", {}).get("name", "")
                            desc = item.get("description", "") or ""
                            url = item.get("url", "")
                            location = item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")

                            if not title or not company:
                                continue

                            job_id = hashlib.sha256(f"web3:{company}:{title}".encode()).hexdigest()[:16]
                            jobs.append(Job(
                                job_id=job_id,
                                title=title,
                                company=company,
                                description=re.sub(r"<[^>]+>", "", desc)[:2000],
                                location=location or "Remote",
                                remote_type="Remote",
                                source="Web3Career",
                                apply_url=url,
                                posted_at=datetime.now(timezone.utc),
                            ))
                except Exception:
                    continue

            # Fallback: try HTML table rows
            if not jobs:
                rows = re.findall(
                    r'<tr[^>]*>(.*?)</tr>',
                    html, re.DOTALL,
                )
                for row in rows[:30]:
                    try:
                        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                        if len(cells) < 3:
                            continue

                        link_match = re.search(r'href="(https?://[^"]+)"', row)
                        url = link_match.group(1) if link_match else ""

                        titles = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', row, re.DOTALL)
                        title = re.sub(r"<[^>]+>", "", titles[0]).strip() if titles else ""

                        company_match = re.search(r'class="[^"]*company[^"]*"[^>]*>(.*?)<', row, re.IGNORECASE)
                        company = re.sub(r"<[^>]+>", "", company_match.group(1)).strip() if company_match else ""

                        if not title or not company:
                            continue

                        job_id = hashlib.sha256(f"web3:{company}:{title}".encode()).hexdigest()[:16]
                        jobs.append(Job(
                            job_id=job_id,
                            title=title,
                            company=company,
                            description="",
                            location="Remote",
                            remote_type="Remote",
                            source="Web3Career",
                            apply_url=url,
                            posted_at=datetime.now(timezone.utc),
                        ))
                    except Exception:
                        continue

            logger.info("Web3Career: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Web3Career fetch failed")
        return jobs
