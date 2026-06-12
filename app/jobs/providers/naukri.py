"""Naukri job provider — fetches jobs from Naukri.com search pages."""

from __future__ import annotations

import hashlib
import logging
import re
from html import unescape

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_NAUKRI_SEARCH = "https://www.naukri.com/{keyword}-jobs-in-{location}?k={keyword}&l={location}&remote=1&daysold=1"


class NaukriProvider(BaseJobProvider):
    """Fetches jobs from Naukri.com (India's largest job site).

    Searches for remote/hybrid jobs posted within 1 day.
    """

    @property
    def name(self) -> str:
        return "Naukri"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        reraise=False,
    )
    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        keywords = ["react", "python", "frontend", "full-stack", "nodejs", "backend"]
        locations = ["bangalore", "remote"]

        try:
            async with aiohttp.ClientSession() as session:
                for kw in keywords:
                    for loc in locations:
                        url = _NAUKRI_SEARCH.format(keyword=kw, location=loc)
                        try:
                            async with session.get(
                                url,
                                timeout=aiohttp.ClientTimeout(total=15),
                                headers={"User-Agent": "Mozilla/5.0"},
                            ) as resp:
                                if resp.status != 200:
                                    continue
                                html = await resp.text()
                                jobs.extend(self._parse_jobs(html, kw))
                        except Exception:
                            continue

            logger.info("Naukri: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Naukri fetch failed")
        return jobs

    @staticmethod
    def _parse_jobs(html: str, keyword: str) -> list[Job]:
        jobs: list[Job] = []
        # Look for job cards in the HTML
        # Pattern: job title in heading or link
        title_pattern = re.compile(
            r'<a[^>]*class=["\']title["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
        )
        company_pattern = re.compile(
            r'<a[^>]*class=["\']subTitle["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
        )
        desc_pattern = re.compile(
            r'<div[^>]*class=["\']job-description["\'][^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL
        )

        titles = title_pattern.findall(html)
        companies = company_pattern.findall(html)
        descs = desc_pattern.findall(html)

        max_len = max(len(titles), len(companies), len(descs))
        for i in range(max_len):
            title = unescape(re.sub(r"<[^>]+>", "", titles[i])).strip() if i < len(titles) else ""
            company = unescape(re.sub(r"<[^>]+>", "", companies[i])).strip() if i < len(companies) else ""
            desc = unescape(re.sub(r"<[^>]+>", "", descs[i])).strip() if i < len(descs) else ""

            if not title or not company:
                continue

            job_id = hashlib.sha256(f"naukri:{company}:{title}".encode()).hexdigest()[:16]
            job = Job(
                job_id=job_id,
                title=title,
                company=company,
                description=desc[:2000],
                location="Bangalore/Remote",
                remote_type="Remote",
                source="Naukri",
                apply_url=f"https://www.naukri.com/{keyword}-jobs",
            )
            jobs.append(job)

        return jobs
