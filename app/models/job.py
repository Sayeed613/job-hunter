"""Job domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Job:
    """Represents a single job posting discovered or processed by the system.

    Attributes:
        job_id: Unique identifier (sha256 of company+title).
        title: Job title.
        company: Name of the hiring company.
        description: Full job description text.
        location: Geographic location.
        remote_type: "Remote", "Hybrid", or "Onsite".
        job_type: "Full-time", "Part-time", "Hourly", "Contract".
        salary: Salary string (e.g. "$80k-$120k").
        salary_min: Minimum salary as number.
        salary_max: Maximum salary as number.
        currency: Currency code (e.g. "USD", "INR").
        source: Platform name (e.g. "LinkedIn", "Indeed").
        apply_url: Direct application URL.
        posted_at: When the job was posted.
        skills_required: List of required skills from the JD.
        experience_years: Required years of experience.
        raw_html: Raw HTML of the job page (for scraping).
        scraped_at: When the job was scraped.
    """

    job_id: str
    title: str
    company: str
    description: str
    location: str = ""
    remote_type: str = "Remote"
    job_type: str = "Full-time"
    salary: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    currency: Optional[str] = None
    source: str = ""
    apply_url: str = ""
    posted_at: Optional[datetime] = None
    skills_required: list[str] = field(default_factory=list)
    experience_years: Optional[int] = None
    raw_html: Optional[str] = None
    scraped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
