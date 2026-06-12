"""Job provider abstractions and concrete implementations."""

from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.models.job import Job

logger = logging.getLogger("headhunter")


# ── Raw data model ───────────────────────────────────────────


@dataclass
class RawJob:
    """Raw job listing as returned by an external provider.

    This lightweight container holds unnormalised data before it is
    converted into the canonical :class:`Job` domain model.
    """

    title: str
    company: str
    location: str
    url: str
    description: str
    source: str
    posted_at: datetime


# ── Abstract provider ────────────────────────────────────────


class JobProvider(ABC):
    """Abstract base for any job listing source."""

    @abstractmethod
    def fetch_jobs(self) -> list[RawJob]:
        """Fetch raw job listings from the external source.

        Returns:
            A list of :class:`RawJob` instances.  Return an empty list
            when the source is unreachable or returns no results.
        """

    @abstractmethod
    def normalize_job(self, raw: RawJob) -> Job:
        """Convert a :class:`RawJob` into the canonical :class:`Job`.

        Args:
            raw: The raw listing returned by :meth:`fetch_jobs`.

        Returns:
            A fully populated :class:`Job` instance ready for persistence.
        """


# ── RemoteOK ─────────────────────────────────────────────────


class RemoteOKProvider(JobProvider):
    """Provider that reads job listings from the RemoteOK API.

    API docs: https://remoteok.com/api
    """

    BASE_URL: str = "https://remoteok.com/api"
    USER_AGENT: str = "ProjectHeadhunter/1.0"

    def __init__(self, timeout: int = 15) -> None:
        """Initialise the provider.

        Args:
            timeout: HTTP request timeout in seconds.
        """
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json",
            },
        )

    # ── Retry wrapper ────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout),
        ),
        reraise=True,
    )
    def _get_json(self) -> list[dict[str, Any]]:
        """Make the HTTP GET request with exponential-backoff retry."""
        response = self._session.get(self.BASE_URL, timeout=self._timeout)
        response.raise_for_status()
        return response.json()

    # ── Public interface ─────────────────────────────────────

    def fetch_jobs(self) -> list[RawJob]:
        """Fetch raw job listings from RemoteOK.

        Returns:
            A list of :class:`RawJob` instances, or an empty list if the
            API is unreachable or returns malformed data.
        """
        try:
            data = self._get_json()
        except requests.RequestException:
            logger.exception("RemoteOK API request failed after retries")
            return []

        # The first element is a metadata dict (no slug) — skip it.
        if data and isinstance(data[0], dict) and "slug" not in data[0]:
            data = data[1:]

        raw_jobs: list[RawJob] = []
        for item in data:
            try:
                raw_jobs.append(self._item_to_raw(item))
            except Exception:
                logger.warning(
                    "Skipping unparseable RemoteOK item",
                    extra={"slug": item.get("slug", "?")},
                )
                continue

        logger.info(
            "RemoteOK fetch complete",
            extra={"total_raw": len(raw_jobs)},
        )
        return raw_jobs

    def normalize_job(self, raw: RawJob) -> Job:
        """Convert a RemoteOK :class:`RawJob` into a canonical :class:`Job`.

        The document ID is derived from a SHA-256 hash of the URL so the
        same posting consistently maps to the same Firestore document,
        enabling idempotent saves.
        """
        job_id = hashlib.sha256(raw.url.encode("utf-8")).hexdigest()[:16]
        return Job(
            id=job_id,
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

    def _item_to_raw(self, item: dict[str, Any]) -> RawJob:
        """Parse a single RemoteOK API response dict into a :class:`RawJob`.

        Raises:
            ValueError: When required fields (title, company, url) are
                missing or empty.
        """
        title = (item.get("position") or "").strip()
        company = (item.get("company") or "").strip()
        location = (item.get("location") or "remote").strip()
        url = (item.get("url") or "").strip()
        description = (item.get("description") or "").strip()
        posted_at = self._parse_date(item.get("date", ""))

        if not title or not company or not url:
            raise ValueError(
                f"Incomplete job item (slug={item.get('slug', 'unknown')})",
            )

        # Strip HTML tags from the description for cleaner storage.
        description = re.sub(r"<[^>]+>", "", description).strip()

        return RawJob(
            title=title,
            company=company,
            location=location,
            url=url,
            description=description,
            source="remoteok",
            posted_at=posted_at,
        )

    @staticmethod
    def _parse_date(raw: Any) -> datetime:
        """Parse a date value returned by the RemoteOK API.

        RemoteOK typically returns ISO-8601 strings (e.g.
        ``"2024-01-15T12:00:00Z"``).  If parsing fails, the current UTC
        time is used as a safe fallback.
        """
        if not raw:
            return datetime.now(timezone.utc)

        if isinstance(raw, datetime):
            if raw.tzinfo is None:
                return raw.replace(tzinfo=timezone.utc)
            return raw

        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(str(raw), fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        logger.warning("Unrecognised date format %r, falling back to now", raw)
        return datetime.now(timezone.utc)
