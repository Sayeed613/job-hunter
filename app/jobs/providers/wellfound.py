"""Wellfound (AngelList Talent) job provider — fetches startup jobs via public feeds.

Wellfound's API is not publicly documented. This provider uses the
public job listing page and atom feed to extract startup jobs.
"""

from __future__ import annotations

import hashlib
import logging
import re

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.jobs.providers.base import BaseJobProvider
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")


class WellfoundProvider(BaseJobProvider):
    """Fetches startup jobs from Wellfound (AngelList Talent) via public feeds."""

    @property
    def name(self) -> str:
        return "Wellfound"

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
        reraise=False,
    )
    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []

        try:
            # Strategy: scrape the Wellfound job listing page
            # Wellfound renders jobs server-side for the initial load
            url = "https://wellfound.com/jobs?remote=true&sort_by=created_at"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/html,application/xhtml+xml",
                    },
                ) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        jobs.extend(self._parse_html(html))
                    else:
                        logger.warning(
                            "Wellfound returned status %d — skipping",
                            resp.status,
                        )

            logger.info("Wellfound: fetched %d jobs", len(jobs))
        except Exception:
            logger.exception("Wellfound fetch failed")
        return jobs

    @staticmethod
    def _parse_html(html: str) -> list[Job]:
        """Parse job listings from the Wellfound HTML page."""
        jobs: list[Job] = []

        # Try to find JSON-LD structured data
        json_ld_matches = re.finditer(
            r'<script type="application/ld\+json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        import json

        for match in json_ld_matches:
            try:
                data = json.loads(match.group(1))
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        title = item.get("title", "")
                        company_obj = item.get(
                            "hiringOrganization",
                            item.get("directApplicant", {}),
                        )
                        company = (
                            company_obj.get("name", "")
                            if isinstance(company_obj, dict)
                            else ""
                        )
                        desc = item.get("description", "") or ""
                        location = item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                        url = item.get("url", "")
                        salary = item.get("baseSalary", {}).get("value", {}).get("value", "") if isinstance(item.get("baseSalary"), dict) else ""

                        if not title or not company:
                            continue

                        job_id = hashlib.sha256(f"wellfound:{company}:{title}".encode()).hexdigest()[:16]
                        jobs.append(Job(
                            job_id=job_id,
                            title=title,
                            company=company,
                            description=desc[:2000],
                            location=location or "Remote",
                            remote_type="Remote",
                            source="Wellfound",
                            apply_url=url,
                            salary=str(salary) if salary else None,
                        ))
            except Exception:
                continue

        # Fallback: extract from job card HTML patterns
        if not jobs:
            card_pattern = re.compile(
                r'<a[^>]*href="(/startups/[^"]*/jobs/[^"]*)"[^>]*>'
                r'\s*<strong[^>]*>(.*?)</strong>'
                r'\s*</a>',
                re.DOTALL | re.IGNORECASE,
            )
            for link_match in card_pattern.finditer(html):
                try:
                    path = link_match.group(1)
                    title = link_match.group(2).strip()
                    url = f"https://wellfound.com{path}"
                    # Company is embedded in the URL path
                    company_part = path.split("/")[2] if "/" in path else ""
                    company = company_part.replace("-", " ").title() if company_part else "Startup"

                    if not title:
                        continue

                    job_id = hashlib.sha256(f"wellfound:{company}:{title}".encode()).hexdigest()[:16]
                    jobs.append(Job(
                        job_id=job_id,
                        title=title,
                        company=company,
                        description="",
                        location="Remote",
                        remote_type="Remote",
                        source="Wellfound",
                        apply_url=url,
                    ))
                except Exception:
                    continue

        return jobs

    @staticmethod
    def _parse_nextjs_item(item: dict) -> Job | None:
        """Parse a single job item from Wellfound's Next.js data."""
        try:
            title = item.get("title", "") or item.get("role", "")
            company_data = item.get("company", item.get("startup", {}))
            company = company_data.get("name", "") if isinstance(company_data, dict) else ""
            desc = item.get("description", item.get("overview", "")) or ""
            location = item.get("location", "Remote")
            url = item.get("url", item.get("apply_url", ""))
            salary = item.get("salary", "")

            if not title or not company:
                return None

            job_id = hashlib.sha256(f"wellfound:{company}:{title}".encode()).hexdigest()[:16]
            return Job(
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
        except Exception:
            return None
