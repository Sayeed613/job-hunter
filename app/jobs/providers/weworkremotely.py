"""We Work Remotely job provider — fetches from RSS feed."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_WWR_RSS = "https://weworkremotely.com/remote-jobs.rss"


class WeWorkRemotelyProvider(BaseJobProvider):
    """Fetches remote jobs from We Work Remotely's RSS feed."""

    @property
    def name(self) -> str:
        return "WeWorkRemotely"

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
                async with session.get(_WWR_RSS, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.warning("WWR returned status %d", resp.status)
                        return []
                    text = await resp.text()

            # Simple XML parsing without lxml dependency
            import xml.etree.ElementTree as ET

            root = ET.fromstring(text)
            ns = {"content": "http://purl.org/rss/1.0/modules/content/"}

            channel = root.find("channel") or root
            for item_elem in channel.findall("item"):
                try:
                    title = (item_elem.findtext("title") or "").strip()
                    company = (item_elem.findtext("source") or title or "Unknown").strip()
                    link = (item_elem.findtext("link") or "").strip()
                    desc = (item_elem.findtext("description") or "").strip()
                    pub_date_str = (item_elem.findtext("pubDate") or "").strip()

                    if not title or not link:
                        continue

                    job_id = hashlib.sha256(link.encode()).hexdigest()[:16]

                    # Try to parse pubDate
                    posted_at = None
                    if pub_date_str:
                        try:
                            from email.utils import parsedate_to_datetime
                            posted_at = parsedate_to_datetime(pub_date_str)
                        except Exception:
                            pass

                    job = Job(
                        job_id=job_id,
                        title=title,
                        company=company,
                        description=desc,
                        location="Remote",
                        remote_type="Remote",
                        job_type="Full-time",
                        source="WeWorkRemotely",
                        apply_url=link,
                        posted_at=posted_at,
                    )
                    jobs.append(job)

                except Exception:
                    continue

            logger.info("WeWorkRemotely: fetched %d jobs", len(jobs))

        except ET.ParseError:
            logger.warning("WeWorkRemotely: failed to parse RSS XML")
        except Exception:
            logger.exception("WeWorkRemotely fetch failed")
        return jobs
