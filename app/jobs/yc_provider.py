"""YCProvider — Y Combinator jobs via browser automation.

``workatastartup.com`` is behind Cloudflare and uses client-side
rendering, so simple HTTP requests are blocked.  This provider uses
Playwright (headless Chromium) to render the page, let JavaScript
execute, and then parse the DOM with BeautifulSoup.

The provider is placed here (``app/jobs/yc_provider.py``) rather than
``app/jobs/providers/yc_provider.py`` because ``app/jobs/providers.py``
(the file that defines :class:`JobProvider` and :class:`RawJob`) would be
shadowed by a ``providers/`` directory.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from app.jobs.providers import JobProvider, RawJob
from app.jobs.providers_ext import _hash_url
from app.models.job import Job

logger = logging.getLogger("headhunter")

_SEARCH_URL = "https://www.workatastartup.com/jobs"

# ── Card-level field selectors ───────────────────────────────
# These patterns target common CSS class naming conventions used by
# the workatastartup.com React frontend.


def _class_contains(*substrings: str) -> re.Pattern[str]:
    """Return a compiled regex matching any of *substrings* in a class attr."""
    return re.compile(
        r"\b(?:" + "|".join(re.escape(s) for s in substrings) + r")\b",
        re.IGNORECASE,
    )


# ═══════════════════════════════════════════════════════════════
# Provider
# ═══════════════════════════════════════════════════════════════


class YCProvider(JobProvider):
    """Fetches job listings from Y Combinator's Work at a Startup board.

    The site uses Cloudflare protection and client-side rendering, so this
    provider launches a headless Chromium browser via Playwright to render
    the page.  After the job cards appear in the DOM the HTML is parsed
    with BeautifulSoup.

    Example usage::

        provider = YCProvider()
        raw_jobs = provider.fetch_jobs()
        for raw in raw_jobs:
            job = provider.normalize_job(raw)
            ...

    .. note::

        Playwright's browser binary (``chromium``) must be installed.
        Run ``python -m playwright install chromium`` if not already
        present.
    """

    SEARCH_URL: str = _SEARCH_URL

    def __init__(self, headless: bool = True, timeout_ms: int = 30_000) -> None:
        """Initialise the provider.

        Args:
            headless: Whether to run the browser in headless mode.
                Set to ``False`` for debugging.
            timeout_ms: Maximum time (milliseconds) to wait for the
                page to load.
        """
        self._headless = headless
        self._timeout_ms = timeout_ms

    # ── Public interface ─────────────────────────────────────

    def fetch_jobs(self) -> list[RawJob]:
        """Fetch jobs from workatastartup.com via headless browser.

        Returns:
            A list of :class:`RawJob` instances, or an empty list if the
            browser session fails or no jobs are found.
        """
        raw_jobs: list[RawJob] = []

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self._headless)
                try:
                    context = browser.new_context(
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        viewport={"width": 1280, "height": 1024},
                    )
                    page = context.new_page()

                    logger.info("Navigating to %s", _SEARCH_URL)
                    page.goto(
                        _SEARCH_URL,
                        wait_until="networkidle",
                        timeout=self._timeout_ms,
                    )

                    # Wait for job-card-like elements to appear.
                    page.wait_for_selector(
                        "[class*=job]",
                        timeout=self._timeout_ms,
                    )
                    # Give dynamic content a moment to settle.
                    page.wait_for_timeout(2_000)

                    html = page.content()
                finally:
                    browser.close()
        except Exception:
            logger.exception("YCProvider: Playwright browser session failed")
            return []

        soup = BeautifulSoup(html, "html.parser")
        raw_jobs = self._parse_jobs(soup)

        logger.info(
            "YCProvider: fetch complete",
            extra={"jobs_found": len(raw_jobs)},
        )
        return raw_jobs

    def normalize_job(self, raw: RawJob) -> Job:
        """Convert a :class:`RawJob` to a canonical :class:`Job`.

        The document ID is a SHA-256 hash of the URL for idempotent
        Firestore saves.
        """
        return Job(
            id=_hash_url(raw.url),
            title=raw.title,
            company=raw.company,
            location=raw.location,
            url=raw.url,
            description=raw.description,
            source=raw.source,
            created_at=raw.posted_at,
            match_score=None,
        )

    # ── Internal helpers ─────────────────────────────────────

    @staticmethod
    def _parse_jobs(soup: BeautifulSoup) -> list[RawJob]:
        """Extract job listings from the rendered page HTML."""
        raw_jobs: list[RawJob] = []
        seen_urls: set[str] = set()

        # Strategy: find all <a> links pointing to job detail pages,
        # then walk up to the parent card container for richer data.
        job_links = soup.find_all("a", href=re.compile(r"/jobs/\d+"))

        for link in job_links:
            href = link.get("href", "")
            if href.startswith("/"):
                href = f"https://www.workatastartup.com{href}"

            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Walk up to the closest card-level ancestor.
            card = link
            for _ in range(5):
                parent = card.find_parent()
                if parent is None or parent.name in ("html", "body"):
                    break
                card = parent
                # Heuristic: a div with several children is likely the card.
                if (
                    parent.name == "div"
                    and len(parent.find_all(["a", "h2", "h3", "span"], recursive=False)) >= 2
                ):
                    break

            try:
                job = _parse_card(card, link, href)
                if job:
                    raw_jobs.append(job)
            except Exception:
                continue

        # Fallback: if no jobs found via links, try direct card parsing.
        if not raw_jobs:
            cards = soup.find_all(
                "div", class_=_class_contains("job", "card", "listing"),
            )
            for card in cards:
                try:
                    link = card.find("a", href=re.compile(r"/jobs/\d+"))
                    if not link:
                        continue
                    href = link.get("href", "")
                    if href.startswith("/"):
                        href = f"https://www.workatastartup.com{href}"
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    job = _parse_card(card, link, href)
                    if job:
                        raw_jobs.append(job)
                except Exception:
                    continue

        return raw_jobs


def _parse_card(card: Any, link: Any, href: str) -> RawJob | None:
    """Parse a job card element into a :class:`RawJob`.

    Args:
        card: The BeautifulSoup element representing the job card.
        link: The anchor element containing the job URL.
        href: The absolute URL of the job posting.

    Returns:
        A :class:`RawJob` or ``None`` if the card cannot be parsed.
    """
    # ── Title ────────────────────────────────────────────────
    title_el = link or card.find(
        ["h2", "h3", "strong", "a"],
        class_=_class_contains("title", "position", "role", "name"),
    )
    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        title = link.get_text(strip=True) if link else ""

    if not title:
        return None

    # ── Company ──────────────────────────────────────────────
    company_el = card.find(
        ["span", "div", "small", "p"],
        class_=_class_contains("company", "org", "employer", "name"),
    )
    company = company_el.get_text(strip=True) if company_el else "YC Startup"

    # ── Location ─────────────────────────────────────────────
    loc_el = card.find(
        ["span", "div", "small"],
        class_=_class_contains("location", "place", "region"),
    )
    location = loc_el.get_text(strip=True) if loc_el else "Remote"

    # ── Employment type ──────────────────────────────────────
    type_el = card.find(
        ["span", "div", "small"],
        class_=_class_contains("type", "employment", "commitment", "full", "part"),
    )
    employment_type = type_el.get_text(strip=True) if type_el else ""

    # ── Salary ───────────────────────────────────────────────
    salary_el = card.find(
        ["span", "div", "small"],
        class_=_class_contains("salary", "comp", "pay", "range"),
    )
    salary = salary_el.get_text(strip=True) if salary_el else ""

    # ── Extract from full card text (fallback for embedded salary/type) ──
    card_text = card.get_text(" ", strip=True)
    if not salary:
        salary_match = re.search(r"\$\d{2,3}K\s*[-–]\s*\$\d{2,3}K", card_text)
        if salary_match:
            salary = salary_match.group(0)
    if not employment_type:
        type_match = re.search(
            r"\b(Full.?Time|Part.?Time|Contract|Internship|Intern)\b",
            card_text,
            re.IGNORECASE,
        )
        if type_match:
            employment_type = type_match.group(0)

    # ── Description ──────────────────────────────────────────
    desc_el = card.find(
        ["div", "p", "span"],
        class_=_class_contains("desc", "summary", "text", "about"),
    )
    description = desc_el.get_text(strip=True) if desc_el else ""

    # Enrich description with available metadata.
    meta_parts = [description]
    if employment_type:
        meta_parts.append(f"Type: {employment_type}")
    if salary:
        meta_parts.append(f"Salary: {salary}")
    full_desc = " | ".join(p for p in meta_parts if p)

    return RawJob(
        title=title,
        company=company,
        location=location,
        url=href,
        description=full_desc[:5000],
        source="ycombinator",
        posted_at=datetime.now(timezone.utc),
    )
