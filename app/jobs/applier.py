"""Job application orchestrator — routes jobs to the right applier.

The :class:`JobApplier` auto-detects the best submission method for
each job posting by examining the application URL and description,
then delegates to the appropriate :class:`BaseApplier` implementation.

Supported methods
-----------------
* **Greenhouse API** — ``POST /v1/boards/{board}/jobs/{id}``
* **Lever API** — ``POST /v0/postings/{site}/{posting_id}``
* **Ashby API** — ``POST /applicationForm.submit``
* **Email (SMTP)** — sends cover letter + resume via email
* **Manual** — falls back when no automated method is available

Usage::

    from app.jobs.applier import JobApplier

    applier = JobApplier()
    result = applier.submit(
        candidate_name="Sayeed Khan",
        candidate_email="sayeed@example.com",
        candidate_phone="+91-...",
        resume_path="output/resume_123.docx",
        cover_letter_path="output/cover_123.docx",
        job_title="Senior Frontend Engineer",
        job_apply_url="https://boards.greenhouse.io/exampleco/jobs/123",
        job_description="...",
        job_source="greenhouse",
    )
"""

from __future__ import annotations

import logging
from typing import Any

from app.jobs.appliers.base import ApplierResult, ApplicationMethod, BaseApplier
from app.jobs.appliers.greenhouse import GreenhouseApplier
from app.jobs.appliers.lever import LeverApplier
from app.jobs.appliers.ashby import AshbyApplier
from app.jobs.appliers.email_applier import EmailApplier
from app.jobs.appliers.web_applier import WebApplier

logger = logging.getLogger("headhunter")


class JobApplier:
    """Orchestrates application submission across multiple methods.

    Maintains a registry of :class:`BaseApplier` instances and routes
    each job to the first applier whose ``can_handle()`` matches the
    job's URL or metadata.

    Appliers are tried in priority order (API → email → manual).
    """

    def __init__(self) -> None:
        """Initialise the orchestrator with default appliers.

        All appliers are lazily configured from :class:`Settings`.
        """
        self._appliers: list[BaseApplier] = []
        self._initialised = False

    def _ensure_appliers(self) -> None:
        """Lazy-init the applier registry."""
        if self._initialised:
            return

        from app.config.settings import Settings  # noqa: PLC0415
        cfg = Settings()

        self._appliers = [
            GreenhouseApplier(),
            LeverApplier(),
            AshbyApplier(),
            EmailApplier(),
            WebApplier(headless=cfg.browser_headless),
        ]
        self._initialised = True

    def register_applier(self, applier: BaseApplier) -> None:
        """Register an additional applier.

        Args:
            applier: A :class:`BaseApplier` implementation.
        """
        self._ensure_appliers()
        self._appliers.append(applier)

    # ── Public API ───────────────────────────────────────────

    def submit(
        self,
        candidate_name: str,
        candidate_email: str,
        candidate_phone: str,
        resume_path: str,
        cover_letter_path: str,
        job_title: str,
        job_apply_url: str,
        job_description: str,
        job_source: str = "",
        extra: dict | None = None,
    ) -> ApplierResult:
        """Submit an application using the best matching method.

        Args:
            candidate_name: Full name of the candidate.
            candidate_email: Email address.
            candidate_phone: Phone number.
            resume_path: Filesystem path to the tailored resume.
            cover_letter_path: Filesystem path to the cover letter.
            job_title: Job title.
            job_apply_url: Application URL from the job posting.
            job_description: Full job description text.
            job_source: Provider source string (e.g. ``"greenhouse"``,
                ``"lever"``).  Used as a hint for routing.
            extra: Optional extra parameters forwarded to the applier.

        Returns:
            An :class:`ApplierResult` with the outcome.
        """
        self._ensure_appliers()

        # Determine the best applier.
        applier = self._select_applier(
            job_apply_url=job_apply_url,
            job_description=job_description,
            job_source=job_source,
        )

        if applier is None:
            logger.info(
                "No automated applier available for job — marking as manual",
                extra={"job_title": job_title, "url": job_apply_url},
            )
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.MANUAL,
                error_message=(
                    f"No automated submission method available for "
                    f"{job_title}. Please apply manually at: {job_apply_url}"
                ),
            )

        logger.info(
            "Submitting application via %s",
            applier.display_name,
            extra={
                "job_title": job_title,
                "company": extra.get("company", "") if extra else "",
            },
        )

        try:
            result = applier.apply(
                candidate_name=candidate_name,
                candidate_email=candidate_email,
                candidate_phone=candidate_phone,
                resume_path=resume_path,
                cover_letter_path=cover_letter_path,
                job_title=job_title,
                job_apply_url=job_apply_url,
                job_description=job_description,
                extra=extra,
            )
        except Exception:
            logger.exception(
                "Applier %s raised unhandled exception",
                applier.display_name,
            )
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.UNKNOWN,
                error_message=f"Unhandled exception in {applier.display_name}.",
            )

        if result.success:
            logger.info(
                "Application submitted successfully via %s",
                applier.display_name,
                extra={
                    "method": result.application_method.value,
                    "app_id": result.application_id,
                },
            )
        else:
            logger.warning(
                "Application submission failed via %s",
                applier.display_name,
                extra={"error": result.error_message},
            )

        return result

    # ── Applier selection ────────────────────────────────────

    def _select_applier(
        self,
        job_apply_url: str,
        job_description: str,
        job_source: str,
    ) -> BaseApplier | None:
        """Choose the best applier for a job posting.

        Priority order:
        1. If ``source`` is one of the known API sources, try that applier first.
        2. Otherwise, check each applier's ``can_handle()`` in order.
        3. As a last resort, try the email applier if an email address is found.

        Args:
            job_apply_url: The job's application URL.
            job_description: The job's description text.
            job_source: The provider name (e.g. ``"greenhouse"``).

        Returns:
            The best :class:`BaseApplier`, or ``None`` if none matches.
        """
        # Source-based routing.
        source_map: dict[str, type[BaseApplier]] = {
            "greenhouse": GreenhouseApplier,
            "lever": LeverApplier,
            "ashby": AshbyApplier,
        }

        preferred_cls = source_map.get(job_source.lower().strip())
        if preferred_cls is not None:
            for a in self._appliers:
                if isinstance(a, preferred_cls):
                    return a
                if isinstance(a, preferred_cls) and a.can_handle(job_apply_url):
                    return a

        # URL-based detection (generic).
        for applier in self._appliers:
            if hasattr(applier, "can_handle") and callable(applier.can_handle):
                try:
                    if isinstance(applier, EmailApplier):
                        # Email applier needs description too.
                        if applier.can_handle(job_apply_url, job_description):
                            return applier
                    elif applier.can_handle(job_apply_url):
                        return applier
                except Exception:
                    continue

        return None
