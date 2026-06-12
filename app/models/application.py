"""Application domain model for the Job Automation Bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Application:
    """Represents a job application submitted or tracked by the system.

    Attributes:
        job_id: Unique identifier (sha256 of company+title).
        title: Job title.
        company: Company name.
        location: Job location.
        remote_type: "Remote", "Hybrid", or "Onsite".
        job_type: "Full-time", "Part-time", "Contract".
        salary: Salary string if available.
        source: Platform name.
        apply_url: Direct application URL.
        posted_at: When the job was posted.
        applied_at: When the application was submitted.
        status: "applied", "failed", or "manual_review".
        application_method: "greenhouse", "lever", "linkedin", "generic", "email".
        resume_path: Path to tailored resume file.
        cover_letter_path: Path to cover letter file.
        matched_keywords: List of matched keywords from the JD.
        match_score: How well the JD matched the resume (0-1).
        error_message: Error details if status is "failed".
        interview_status: "no_response", "phone_screen", "rejected", "offer".
        notes: Manual notes field.
        created_at: Record creation timestamp.
        updated_at: Record update timestamp.
    """

    job_id: str
    title: str
    company: str
    location: str = ""
    remote_type: str = "Remote"
    job_type: str = "Full-time"
    salary: Optional[str] = None
    source: str = ""
    apply_url: str = ""
    posted_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None
    status: str = "applied"
    application_method: str = ""
    resume_path: str = ""
    cover_letter_path: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    match_score: float = 0.0
    error_message: Optional[str] = None
    interview_status: str = "no_response"
    notes: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
