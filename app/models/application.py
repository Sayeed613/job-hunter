"""Application domain model for Project Headhunter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional


class ApplicationStatus(Enum):
    """Tracks the lifecycle of a job application."""

    NEW = auto()
    APPLIED = auto()
    SHORTLISTED = auto()
    REJECTED = auto()
    INTERVIEW = auto()
    OFFER = auto()

    def __str__(self) -> str:
        return self.name


@dataclass
class Application:
    """Represents a job application submitted or tracked by the user.

    Attributes:
        id: Unique identifier for this application record.
        job_id: Foreign key referencing the associated :class:`Job`.
        company: Name of the hiring company (denormalised for quick access).
        role: Job title or role name applied for.
        resume_version: Identifier or path of the resume version used.
        cover_letter_version: Identifier or path of the cover letter version
            used (empty string if not provided).
        match_score: Optional relevance score (0.0 – 1.0) computed by the
            matching pipeline.
        status: Current stage in the application lifecycle.  Defaults to
            :attr:`ApplicationStatus.NEW`.
        applied_at: Timestamp of when the application was submitted.
        job_url: Direct link to the original job posting.
        application_method: How the application was submitted (e.g.
            ``"greenhouse_api"``, ``"email"``, ``"manual"``).  Empty string
            if not yet submitted.
        auto_submit_success: Whether the auto-submission succeeded.
            ``None`` if auto-submit was not attempted.
        auto_submit_error: Error message from auto-submission, if any.
        confirmation_url: Provider-side confirmation or tracking URL.
    """

    id: str
    job_id: str
    company: str
    role: str
    resume_version: str
    cover_letter_version: str = ""
    match_score: Optional[float] = None
    status: ApplicationStatus = ApplicationStatus.NEW
    applied_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    job_url: str = ""
    application_method: str = ""
    auto_submit_success: Optional[bool] = None
    auto_submit_error: str = ""
    confirmation_url: str = ""
