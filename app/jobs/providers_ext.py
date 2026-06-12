"""Extended job providers — API-based and scraping-based implementations.

Providers
---------
API-based (public endpoints, no auth required):
- :class:`GreenhouseProvider` — boards-api.greenhouse.io
- :class:`LeverProvider` — api.lever.co
- :class:`AshbyProvider` — api.ashbyhq.com

Scraping-based (requests + BeautifulSoup):
- :class:`WellfoundProvider` — wellfound.com
- :class:`NaukriProvider` — naukri.com
- :class:`FounditProvider` — foundit.in
- :class:`CutshortProvider` — cutshort.io
- :class:`InstahyreProvider` — instahyre.com
- :class:`GulfTalentProvider` — gulftalent.com

Each provider inherits from :class:`JobProvider` and respects the
common :class:`RawJob` / :class:`Job` contract.

.. note::
   Scraping-based providers depend on the target site's HTML structure
   and may break if the site is updated.  They are best-effort and
   log warnings when parsing fails.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.jobs.providers import JobProvider, RawJob
from app.models.job import Job

logger = logging.getLogger("headhunter")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ── Shared helpers ───────────────────────────────────────────


def _hash_url(url: str) -> str:
    """Return a short SHA-256 hex digest for use as a Firestore doc ID."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _strip_html(text: str) -> str:
    """Remove HTML tags from *text*."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_iso_date(raw: str | None) -> datetime:
    """Parse an ISO-8601 date string or fall back to now."""
    if not raw:
        return datetime.now(timezone.utc)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(str(raw), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _create_session() -> requests.Session:
    """Create a requests session with standard headers."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
    )
    return session


def _normalize_job_from_raw(raw: RawJob) -> Job:
    """Convert a :class:`RawJob` to a canonical :class:`Job`.

    This is the standard normalizer used by all providers in this
    module.  The document ID is a hash of the URL for idempotent
    saves.
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


# ═══════════════════════════════════════════════════════════════
# API-BASED PROVIDERS
# ═══════════════════════════════════════════════════════════════


class GreenhouseProvider(JobProvider):
    """Fetches jobs from Greenhouse public job boards.

    API docs: https://developers.greenhouse.io/job-board.html

    The *board_token* is the company identifier from their career
    page URL (e.g. ``boards.greenhouse.io/exampleco`` → token
    ``exampleco``).
    """

    BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

    def __init__(
        self,
        board_tokens: list[str] | None = None,
        timeout: int = 30,
    ) -> None:
        """Initialise the provider.

        Args:
            board_tokens: List of Greenhouse board tokens to query.
                When ``None`` defaults to a curated list of
                well-known companies.
            timeout: HTTP request timeout in seconds.
        """
        self._board_tokens = board_tokens or [
            "stripe",
            "datadog",
            "hashicorp",
            "gitlab",
            "cloudflare",
            "vercel",
            "dropbox",
            "airbnb",
            "notion",
            "linear",
        ]
        self._timeout = timeout
        self._session = _create_session()

    # ── Public interface ─────────────────────────────────────

    def fetch_jobs(self) -> list[RawJob]:
        """Fetch jobs from all configured board tokens."""
        raw_jobs: list[RawJob] = []

        for token in self._board_tokens:
            try:
                data = self._fetch_board(token)
            except requests.RequestException:
                logger.warning(
                    "Greenhouse board %s unreachable — skipping", token,
                )
                continue

            for item in data.get("jobs", []):
                try:
                    raw_jobs.append(self._item_to_raw(item, token))
                except Exception:
                    logger.warning(
                        "Skipping unparseable Greenhouse job",
                        extra={"board": token, "job_id": item.get("id")},
                    )

        logger.info(
            "Greenhouse fetch complete",
            extra={"boards": len(self._board_tokens), "jobs": len(raw_jobs)},
        )
        return raw_jobs

    def normalize_job(self, raw: RawJob) -> Job:
        return _normalize_job_from_raw(raw)

    # ── Internal helpers ─────────────────────────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        reraise=True,
    )
    def _fetch_board(self, token: str) -> dict[str, Any]:
        """Fetch a single board's job listings (paginated)."""
        url = f"{self.BASE_URL}/{token}/jobs"
        params = {"per_page": 100, "content": "true"}
        response = self._session.get(url, params=params, timeout=self._timeout)
        response.raise_for_status()
        return response.json()

    def _item_to_raw(self, item: dict[str, Any], token: str) -> RawJob:
        """Parse a single Greenhouse job dict into a RawJob."""
        title = (item.get("title") or "").strip()
        # The board token is the canonical company identifier on Greenhouse.
        company = (item.get("board_token") or token).strip()
        location = (item.get("location", {}).get("name") or item.get("offices", [{}])[0].get("name") or "Remote").strip()
        url = (item.get("absolute_url") or "").strip()
        description = _strip_html(item.get("content") or "")
        posted_at = _parse_iso_date(item.get("updated_at"))

        if not title or not url:
            raise ValueError(f"Incomplete Greenhouse job (id={item.get('id')})")

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description=description[:5000],
            source="greenhouse",
            posted_at=posted_at,
        )


class LeverProvider(JobProvider):
    """Fetches jobs from Lever public postings API.

    API docs: https://github.com/lever/postings-api

    The *account_name* is the company identifier from their career
    page URL (e.g. ``jobs.lever.co/exampleco`` → account
    ``exampleco``).
    """

    BASE_URL = "https://api.lever.co/v0/postings"

    def __init__(
        self,
        account_names: list[str] | None = None,
        timeout: int = 30,
    ) -> None:
        """Initialise the provider.

        Args:
            account_names: List of Lever account names to query.
                When ``None`` defaults to a curated list.
            timeout: HTTP request timeout in seconds.
        """
        self._account_names = account_names or [
            "linear",
            "calcom",
            "vercel",
            "raycast",
            "coda",
            "readme",
        ]
        self._timeout = timeout
        self._session = _create_session()

    # ── Public interface ─────────────────────────────────────

    def fetch_jobs(self) -> list[RawJob]:
        """Fetch jobs from all configured Lever accounts."""
        raw_jobs: list[RawJob] = []

        for account in self._account_names:
            try:
                data = self._fetch_postings(account)
            except requests.RequestException:
                logger.warning(
                    "Lever account %s unreachable — skipping", account,
                )
                continue

            for item in data:
                try:
                    raw_jobs.append(self._item_to_raw(item, account))
                except Exception:
                    logger.warning(
                        "Skipping unparseable Lever posting",
                        extra={"account": account, "id": item.get("id")},
                    )

        logger.info(
            "Lever fetch complete",
            extra={"accounts": len(self._account_names), "jobs": len(raw_jobs)},
        )
        return raw_jobs

    def normalize_job(self, raw: RawJob) -> Job:
        return _normalize_job_from_raw(raw)

    # ── Internal helpers ─────────────────────────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        reraise=True,
    )
    def _fetch_postings(self, account: str) -> list[dict[str, Any]]:
        """Fetch all postings for a single Lever account."""
        url = f"{self.BASE_URL}/{account}"
        response = self._session.get(url, timeout=self._timeout)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _item_to_raw(item: dict[str, Any], account: str) -> RawJob:
        """Parse a single Lever posting dict into a RawJob."""
        title = (item.get("text") or "").strip()
        company = account.replace("-", " ").title().strip()
        categories = item.get("categories", {})
        location = (categories.get("location") or "Remote").strip()
        url = (item.get("hostedUrl") or "").strip()
        description = _strip_html(item.get("descriptionPlain") or item.get("description") or "")
        posted_at = _parse_iso_date(item.get("createdAt"))

        if not title or not url:
            raise ValueError(f"Incomplete Lever posting (id={item.get('id')})")

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description=description[:5000],
            source="lever",
            posted_at=posted_at,
        )


class AshbyProvider(JobProvider):
    """Fetches jobs from Ashby career portals API.

    API docs: https://developers.ashbyhq.com/reference/publicapirpcs-career-portal

    The *organization_slug* is the company identifier from their
    career page URL (e.g. ``jobs.ashbyhq.com/exampleco`` → slug
    ``exampleco``).
    """

    BASE_URL = "https://api.ashbyhq.com/career-portals/jobs"

    def __init__(
        self,
        organization_slugs: list[str] | None = None,
        timeout: int = 30,
    ) -> None:
        """Initialise the provider.

        Args:
            organization_slugs: List of Ashby organization slugs.
                When ``None`` defaults to a curated list.
            timeout: HTTP request timeout in seconds.
        """
        self._slugs = organization_slugs or [
            "notion",
            "linear",
            "framer",
            "height",
            "synthesia",
            "deel",
        ]
        self._timeout = timeout
        self._session = _create_session()

    # ── Public interface ─────────────────────────────────────

    def fetch_jobs(self) -> list[RawJob]:
        """Fetch jobs from all configured Ashby organizations."""
        raw_jobs: list[RawJob] = []

        for slug in self._slugs:
            try:
                data = self._fetch_portal(slug)
            except requests.RequestException:
                logger.warning(
                    "Ashby portal %s unreachable — skipping", slug,
                )
                continue

            for item in data:
                try:
                    raw_jobs.append(self._item_to_raw(item, slug))
                except Exception:
                    logger.warning(
                        "Skipping unparseable Ashby job",
                        extra={"slug": slug, "id": item.get("id")},
                    )

        logger.info(
            "Ashby fetch complete",
            extra={"orgs": len(self._slugs), "jobs": len(raw_jobs)},
        )
        return raw_jobs

    def normalize_job(self, raw: RawJob) -> Job:
        return _normalize_job_from_raw(raw)

    # ── Internal helpers ─────────────────────────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        reraise=True,
    )
    def _fetch_portal(self, slug: str) -> list[dict[str, Any]]:
        """Fetch all jobs for a single Ashby organization."""
        params = {"organizationSlug": slug}
        response = self._session.get(self.BASE_URL, params=params, timeout=self._timeout)
        response.raise_for_status()
        data = response.json()
        return data.get("jobs", [])

    @staticmethod
    def _item_to_raw(item: dict[str, Any], slug: str) -> RawJob:
        """Parse a single Ashby job dict into a RawJob."""
        title = (item.get("title") or "").strip()
        company = slug.replace("-", " ").title().strip()
        location = (item.get("location") or "Remote").strip()
        url = (item.get("jobUrl") or "").strip()
        description = _strip_html(item.get("descriptionHtml") or "")
        # Ashby does not always provide a creation date — use current time.
        posted_at = _parse_iso_date(item.get("publishedAt"))

        if not title or not url:
            raise ValueError(f"Incomplete Ashby job (id={item.get('id')})")

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description=description[:5000],
            source="ashby",
            posted_at=posted_at,
        )


# ═══════════════════════════════════════════════════════════════
# SCRAPING-BASED PROVIDERS
# ═══════════════════════════════════════════════════════════════


class _BaseScrapingProvider(JobProvider):
    """Common base for providers that scrape HTML search results.

    Subclasses must define :attr:`SEARCH_URL` and override
    :meth:`_parse_search_page` to extract job cards from the
    parsed HTML.
    """

    SEARCH_URL: str = ""
    TIMEOUT: int = 30

    def __init__(self, timeout: int | None = None) -> None:
        self._timeout = timeout or self.TIMEOUT
        self._session = _create_session()
        self._session.headers["Accept"] = "text/html,application/xhtml+xml"

    def fetch_jobs(self) -> list[RawJob]:
        """Fetch jobs by downloading and parsing the search results page."""
        if not self.SEARCH_URL:
            logger.warning(
                "%s has no SEARCH_URL configured — skipping",
                self.__class__.__name__,
            )
            return []

        try:
            html = self._fetch_page()
        except requests.RequestException:
            logger.exception(
                "Failed to fetch %s search page", self.__class__.__name__,
            )
            return []

        soup = BeautifulSoup(html, "html.parser")

        try:
            raw_jobs = self._parse_search_page(soup)
        except Exception:
            logger.exception(
                "Failed to parse %s search page", self.__class__.__name__,
            )
            return []

        logger.info(
            "%s scrape complete",
            self.__class__.__name__,
            extra={"jobs_found": len(raw_jobs)},
        )
        return raw_jobs

    def normalize_job(self, raw: RawJob) -> Job:
        return _normalize_job_from_raw(raw)

    # ── Subclass hooks ───────────────────────────────────────

    def _fetch_page(self) -> str:
        """Download the search results page.

        Override in subclasses that need custom params or headers.
        """
        response = self._session.get(self.SEARCH_URL, timeout=self._timeout)
        response.raise_for_status()
        return response.text

    def _parse_search_page(self, soup: BeautifulSoup) -> list[RawJob]:
        """Extract job cards from the parsed HTML.

        Must be overridden by each subclass.
        """
        raise NotImplementedError


class WellfoundProvider(_BaseScrapingProvider):
    """Scrapes job listings from Wellfound (formerly AngelList).

    Note: Wellfound does not provide a public API.  This scraper
    uses their search page and may break if the site structure
    changes.
    """

    SEARCH_URL = "https://wellfound.com/jobs"

    def _parse_search_page(self, soup: BeautifulSoup) -> list[RawJob]:
        """Extract job cards from Wellfound's search results."""
        raw_jobs: list[RawJob] = []

        # Wellfound renders job cards as <section> or <div> elements
        # with role="listitem" or class containing "card".
        cards = soup.find_all(
            ["section", "div", "li"],
            class_=re.compile(r"card|job|result|listitem", re.IGNORECASE),
        )
        if not cards:
            cards = soup.find_all(True, role="listitem")

        for card in cards:
            try:
                job = self._parse_card(card)
                if job:
                    raw_jobs.append(job)
            except Exception:
                continue

        return raw_jobs

    @staticmethod
    def _parse_card(card: Any) -> RawJob | None:
        """Parse a single job card element."""
        # Title.
        title_el = card.find(["h2", "h3", "h4", "strong", "a"],
                             class_=re.compile(r"title|position|role", re.I))
        if not title_el:
            title_el = card.find("a", href=re.compile(r"/jobs/"))
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        # URL.
        url = ""
        link = title_el if title_el.name == "a" else title_el.find_parent("a")
        if link and link.get("href"):
            href = link["href"]
            url = href if href.startswith("http") else f"https://wellfound.com{href}"

        # Company.
        company_el = card.find(["span", "div", "small"],
                               class_=re.compile(r"company|employer|org", re.I))
        company = company_el.get_text(strip=True) if company_el else "Unknown"

        # Location.
        loc_el = card.find(["span", "div", "small"],
                           class_=re.compile(r"location|region|place", re.I))
        location = loc_el.get_text(strip=True) if loc_el else "Remote"

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description="",
            source="wellfound",
            posted_at=datetime.now(timezone.utc),
        )


class NaukriProvider(JobProvider):
    """Fetches jobs from Naukri.com using their internal JSON API.

    Naukri exposes an internal JSON search endpoint that returns
    structured job data without HTML parsing.  This provider calls
    that endpoint directly rather than scraping rendered pages.

    Note: Naukri may block automated requests.  Additional headers
    or proxy configuration may be needed for reliable operation.
    """

    SEARCH_URL = (
        "https://www.naukri.com/jobapi/v4/search/?"
        "noOfResults=50&searchType=adv&keyword=&location="
        "&industryType=&functionArea=&employmentType=&salaryRange="
    )
    TIMEOUT: int = 30

    def __init__(self, timeout: int | None = None) -> None:
        """Initialise the provider."""
        self._timeout = timeout or self.TIMEOUT
        self._session = _create_session()

    def fetch_jobs(self) -> list[RawJob]:
        """Fetch jobs from Naukri's JSON API."""
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://www.naukri.com/",
            "Origin": "https://www.naukri.com",
            "Appid": "109",
            "Systemid": "109",
        }
        try:
            response = self._session.get(
                self.SEARCH_URL, headers=headers, timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            logger.exception("Naukri API request failed")
            return []

        job_details = data.get("jobDetails", [])
        raw_jobs: list[RawJob] = []

        for item in job_details:
            try:
                raw_jobs.append(self._item_to_raw(item))
            except Exception:
                continue

        logger.info(
            "Naukri fetch complete",
            extra={"jobs_found": len(raw_jobs)},
        )
        return raw_jobs

    def normalize_job(self, raw: RawJob) -> Job:
        return _normalize_job_from_raw(raw)

    @staticmethod
    def _item_to_raw(item: dict[str, Any]) -> RawJob:
        """Parse a Naukri API response item."""
        title = (item.get("title") or "").strip()
        company = (item.get("companyDetail", {}).get("companyName") or "Unknown").strip()
        location = (item.get("placeholders", [{}])[0].get("label") or "India").strip()
        url = (item.get("jobDetailUrl") or "").strip()
        if url and not url.startswith("http"):
            url = f"https://www.naukri.com{url}"
        description = _strip_html(item.get("jobDescription") or "")
        posted_at = _parse_iso_date(item.get("createdDate"))

        if not title or not url:
            raise ValueError("Incomplete Naukri job")

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description=description[:3000],
            source="naukri",
            posted_at=posted_at,
        )


class FounditProvider(_BaseScrapingProvider):
    """Scrapes job listings from Foundit.in (formerly Monster India)."""

    SEARCH_URL = "https://www.foundit.in/search?q=&loc=&searchType=quick"

    def _parse_search_page(self, soup: BeautifulSoup) -> list[RawJob]:
        """Extract job cards from Foundit's search results."""
        raw_jobs: list[RawJob] = []

        cards = soup.find_all(
            ["div", "article", "li"],
            class_=re.compile(r"job|card|result", re.IGNORECASE),
        )
        if not cards:
            cards = soup.find_all("div", attrs={"data-cy": re.compile(r"job", re.I)})

        for card in cards:
            try:
                job = self._parse_card(card)
                if job:
                    raw_jobs.append(job)
            except Exception:
                continue

        return raw_jobs

    @staticmethod
    def _parse_card(card: Any) -> RawJob | None:
        """Parse a single Foundit job card."""
        title_el = card.find(["h2", "h3", "strong", "a"],
                             class_=re.compile(r"title|position|role|job", re.I))
        if not title_el:
            title_el = card.find("a", href=re.compile(r"/job/"))
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        url = ""
        link = title_el if title_el.name == "a" else title_el.find_parent("a")
        if link and link.get("href"):
            href = link["href"]
            url = href if href.startswith("http") else f"https://www.foundit.in{href}"

        company = "Unknown"
        # Foundit typically has company name after a "at" or "-" prefix.
        for part in card.get_text(" ", strip=True).split("\n"):
            part = part.strip()
            if part and len(part) > 2 and part != title:
                company = part
                break

        loc_el = card.find(["span", "div", "small"],
                           class_=re.compile(r"location|place", re.I))
        location = loc_el.get_text(strip=True) if loc_el else "India"

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description="",
            source="foundit",
            posted_at=datetime.now(timezone.utc),
        )


class CutshortProvider(_BaseScrapingProvider):
    """Scrapes job listings from Cutshort.io."""

    SEARCH_URL = "https://cutshort.io/jobs"

    def _parse_search_page(self, soup: BeautifulSoup) -> list[RawJob]:
        """Extract job cards from Cutshort's search results."""
        raw_jobs: list[RawJob] = []

        cards = soup.find_all(
            ["div", "article"],
            class_=re.compile(r"job|card|post|listing", re.IGNORECASE),
        )

        for card in cards:
            try:
                job = self._parse_card(card)
                if job:
                    raw_jobs.append(job)
            except Exception:
                continue

        return raw_jobs

    @staticmethod
    def _parse_card(card: Any) -> RawJob | None:
        """Parse a single Cutshort job card."""
        title_el = card.find(["h2", "h3", "strong", "a"],
                             class_=re.compile(r"title|position|role|name", re.I))
        if not title_el:
            title_el = card.find("a", href=re.compile(r"/job/"))
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        url = ""
        link = title_el if title_el.name == "a" else title_el.find_parent("a")
        if link and link.get("href"):
            href = link["href"]
            url = href if href.startswith("http") else f"https://cutshort.io{href}"

        company_el = card.find(["span", "div", "small"],
                               class_=re.compile(r"company|org|employer", re.I))
        company = company_el.get_text(strip=True) if company_el else "Unknown"

        loc_el = card.find(["span", "div", "small"],
                           class_=re.compile(r"location|place", re.I))
        location = loc_el.get_text(strip=True) if loc_el else "Remote"

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description="",
            source="cutshort",
            posted_at=datetime.now(timezone.utc),
        )


class InstahyreProvider(_BaseScrapingProvider):
    """Scrapes job listings from Instahyre.com."""

    SEARCH_URL = "https://www.instahyre.com/jobs"

    def _parse_search_page(self, soup: BeautifulSoup) -> list[RawJob]:
        """Extract job cards from Instahyre's search results."""
        raw_jobs: list[RawJob] = []

        cards = soup.find_all(
            ["div", "article", "li"],
            class_=re.compile(r"job|card|result|opening", re.IGNORECASE),
        )

        for card in cards:
            try:
                job = self._parse_card(card)
                if job:
                    raw_jobs.append(job)
            except Exception:
                continue

        return raw_jobs

    @staticmethod
    def _parse_card(card: Any) -> RawJob | None:
        """Parse a single Instahyre job card."""
        title_el = card.find(["h2", "h3", "strong", "a"],
                             class_=re.compile(r"title|position|role|name", re.I))
        if not title_el:
            title_el = card.find("a", href=re.compile(r"/jobs/"))
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        url = ""
        link = title_el if title_el.name == "a" else title_el.find_parent("a")
        if link and link.get("href"):
            href = link["href"]
            url = href if href.startswith("http") else f"https://www.instahyre.com{href}"

        texts = card.get_text(" ", strip=True)
        # Company is often a <span> or <div> near the title.
        company_el = card.find(["span", "div", "small"],
                               class_=re.compile(r"company|org", re.I))
        company = company_el.get_text(strip=True) if company_el else "Unknown"

        loc_el = card.find(["span", "div", "small"],
                           class_=re.compile(r"location|place", re.I))
        location = loc_el.get_text(strip=True) if loc_el else "India"

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description="",
            source="instahyre",
            posted_at=datetime.now(timezone.utc),
        )


class GulfTalentProvider(_BaseScrapingProvider):
    """Scrapes job listings from GulfTalent.com."""

    SEARCH_URL = "https://www.gulftalent.com/jobs"

    def _parse_search_page(self, soup: BeautifulSoup) -> list[RawJob]:
        """Extract job cards from GulfTalent's search results."""
        raw_jobs: list[RawJob] = []

        cards = soup.find_all(
            ["div", "article", "li"],
            class_=re.compile(r"job|card|result|listing", re.IGNORECASE),
        )

        for card in cards:
            try:
                job = self._parse_card(card)
                if job:
                    raw_jobs.append(job)
            except Exception:
                continue

        return raw_jobs

    @staticmethod
    def _parse_card(card: Any) -> RawJob | None:
        """Parse a single GulfTalent job card."""
        title_el = card.find(["h2", "h3", "strong", "a"],
                             class_=re.compile(r"title|position|role|name", re.I))
        if not title_el:
            title_el = card.find("a", href=re.compile(r"/job/"))
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        url = ""
        link = title_el if title_el.name == "a" else title_el.find_parent("a")
        if link and link.get("href"):
            href = link["href"]
            url = href if href.startswith("http") else f"https://www.gulftalent.com{href}"

        company_el = card.find(["span", "div", "small", "p"],
                               class_=re.compile(r"company|org|employer", re.I))
        company = company_el.get_text(strip=True) if company_el else "Unknown"

        loc_el = card.find(["span", "div", "small"],
                           class_=re.compile(r"location|place", re.I))
        location = loc_el.get_text(strip=True) if loc_el else "UAE"

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description="",
            source="gulftalent",
            posted_at=datetime.now(timezone.utc),
        )
