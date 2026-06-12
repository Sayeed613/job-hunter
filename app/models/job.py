"""Job domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Job:
    """Represents a single job posting discovered or processed by the system.

    Attributes:
        id: Unique identifier for the job (e.g. source-specific ID or UUID).
        title: Job title (e.g. "Senior Backend Engineer").
        company: Name of the hiring company.
        location: Geographic location (e.g. "San Francisco, CA" or "remote").
        url: Link to the original job posting.
        description: Full or truncated job description text.
        source: Platform or source the job was scraped from (e.g. "linkedin",
            "indeed", "company_careers_page").
        created_at: Timestamp of when this record was created.
        match_score: Optional relevance score (0.0 – 1.0) computed by the
            matching pipeline.  ``None`` until scored.
    """

    id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    source: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    match_score: Optional[float] = None
