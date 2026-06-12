"""Base applier abstraction and shared result types for all application handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ApplicationMethod(str, Enum):
    """How the application was submitted."""

    API_GREENHOUSE = "greenhouse_api"
    API_LEVER = "lever_api"
    API_ASHBY = "ashby_api"
    EMAIL = "email"
    WEB_FORM = "web_form"
    LINKEDIN = "linkedin"
    MANUAL = "manual"  # fallback — user must apply manually
    UNKNOWN = "unknown"


@dataclass
class ApplierResult:
    """Outcome of a single application submission attempt.

    Attributes:
        success: Whether the submission was accepted by the provider.
        application_method: Which method was used to submit.
        confirmation_url: URL or identifier returned by the provider
            confirming the application (empty string if unavailable).
        application_id: Provider-side application ID, if returned.
        error_message: Human-readable error when ``success`` is False.
        raw_response: Full response body or summary for debugging.
    """

    success: bool = False
    application_method: ApplicationMethod = ApplicationMethod.UNKNOWN
    confirmation_url: str = ""
    application_id: str = ""
    error_message: str = ""
    raw_response: str = ""


class BaseApplier(ABC):
    """Abstract base for a job application submission handler.

    Each concrete subclass implements :meth:`apply` for a specific
    method (API-based, email, web form, etc.).
    """

    # Human-readable name for logging / notifications.
    display_name: str = "Generic"

    @abstractmethod
    def apply(
        self,
        candidate_name: str,
        candidate_email: str,
        candidate_phone: str,
        resume_path: str,
        cover_letter_path: str,
        job_title: str,
        job_apply_url: str,
        job_description: str,
        extra: dict | None = None,
    ) -> ApplierResult:
        """Submit an application for a single job.

        Args:
            candidate_name: Full name of the candidate.
            candidate_email: Email address.
            candidate_phone: Phone number (may be empty).
            resume_path: Filesystem path to the tailored resume file.
            cover_letter_path: Filesystem path to the cover letter file.
            job_title: Title of the job being applied to.
            job_apply_url: Application URL from the job posting.
            job_description: Full job description text.
            extra: Optional provider-specific metadata (e.g. board
                token, organisation slug).

        Returns:
            An :class:`ApplierResult` summarising the outcome.
        """
