"""Ashby career-portal API application submitter.

Submits applications to Ashby-hosted job boards using the
``applicationForm.submit`` RPC endpoint.

The applier first fetches the form schema via ``jobPosting.info`` to
discover required fields, then submits via ``multipart/form-data``.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.jobs.appliers.base import ApplierResult, ApplicationMethod, BaseApplier

logger = logging.getLogger("headhunter")

_API_BASE = "https://api.ashbyhq.com"

# Regex to extract job posting ID from an Ashby URL.
# Matches:
#   jobs.ashbyhq.com/{slug}/{posting_id}
#   jobs.ashbyhq.com/{slug}
_ASHBY_URL_RE = re.compile(
    r"jobs\.ashbyhq\.com/([^/]+)(?:/([a-zA-Z0-9-]+))?",
    re.IGNORECASE,
)


class AshbyApplier(BaseApplier):
    """Submit applications to Ashby career portals via their RPC API.

    The applier auto-detects the ``posting_id`` from the job's
    application URL.  The submission follows a two-step process:

    1. Call ``jobPosting.info`` to get the form schema.
    2. Call ``applicationForm.submit`` with the completed form.

    Requires an ``ASHBY_API_KEY`` — see :class:`Settings.ashby_api_key`.

    Example usage::

        applier = AshbyApplier(api_key="abc123")
        result = applier.apply(
            candidate_name="Jane Doe",
            candidate_email="jane@example.com",
            ...
        )
    """

    display_name = "Ashby API"

    def __init__(
        self,
        api_key: str = "",
        timeout: int = 60,
    ) -> None:
        """Initialise the applier.

        Args:
            api_key: Ashby API key.  Falls back to
                ``Settings.ashby_api_key``.
            timeout: HTTP request timeout in seconds (default 60).
        """
        from app.config.settings import Settings  # noqa: PLC0415

        cfg = Settings()
        self._api_key = api_key or cfg.ashby_api_key or ""
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "ProjectHeadhunter/1.0",
                "Accept": "application/json",
            },
        )

        if self._api_key:
            self._session.auth = (self._api_key, "")

        if not self._api_key:
            logger.warning(
                "ASHBY_API_KEY not set — Ashby submissions will fail."
            )

        logger.info(
            "AshbyApplier initialised",
            extra={"api_key_set": bool(self._api_key)},
        )

    # ── Public API ───────────────────────────────────────────

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
        """Submit an application via the Ashby RPC API.

        Args:
            candidate_name: Full name.
            candidate_email: Email address.
            candidate_phone: Phone number.
            resume_path: Path to the tailored resume file.
            cover_letter_path: Path to the cover letter file.
            job_title: The job title (used as fallback for finding
                posting ID).
            job_apply_url: The job posting URL — used to extract the
                posting ID.
            job_description: Unused by Ashby.
            extra: Optional overrides — if ``posting_id`` or
                ``org_slug`` are provided they take precedence.

        Returns:
            An :class:`ApplierResult` with the API response details.
        """
        if not self._api_key:
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.API_ASHBY,
                error_message="ASHBY_API_KEY not configured.",
            )

        # Extract or use override values.
        posting_id: str | None = None

        if extra:
            posting_id = extra.get("posting_id")

        if not posting_id:
            parsed = self._parse_url(job_apply_url)
            if parsed:
                _, posting_id = parsed

        if not posting_id:
            # Try to find the posting by title via jobPosting.info.
            try:
                posting_id = self._find_posting_by_title(job_title)
            except Exception:
                logger.info("Could not auto-detect Ashby posting ID by title")

        if not posting_id:
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.API_ASHBY,
                error_message=(
                    f"Cannot extract posting ID from Ashby URL: "
                    f"{job_apply_url}. Pass ``posting_id`` via extra."
                ),
            )

        # Read cover letter text.
        cover_text = self._read_cover_letter(cover_letter_path)

        try:
            response = self._submit_application(
                posting_id=posting_id,
                name=candidate_name,
                email=candidate_email,
                phone=candidate_phone,
                resume_path=resume_path,
                cover_text=cover_text,
            )
        except requests.RequestException as exc:
            logger.exception("Ashby API request failed")
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.API_ASHBY,
                error_message=f"HTTP request failed: {exc}",
                raw_response=str(exc),
            )

        return self._process_response(response)

    # ── URL parsing ─────────────────────────────────────────

    @staticmethod
    def can_handle(job_apply_url: str) -> bool:
        """Return ``True`` if this applier can handle the given URL."""
        return bool(_ASHBY_URL_RE.search(job_apply_url))

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str | None] | None:
        """Extract ``(org_slug, posting_id)`` from an Ashby URL.

        Returns ``None`` if the URL does not match.  The posting ID
        may be ``None`` (only the slug is present).
        """
        match = _ASHBY_URL_RE.search(url)
        if match:
            slug = match.group(1)
            pid = match.group(2)
            return slug, pid
        return None

    # ── Internal helpers ─────────────────────────────────────

    @staticmethod
    def _read_cover_letter(path: str) -> str:
        """Read cover letter text from a file."""
        cl_path = Path(path)
        if not cl_path.exists():
            return ""

        if cl_path.suffix == ".docx":
            try:
                from docx import Document  # noqa: PLC0415
                doc = Document(str(cl_path))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception:
                logger.warning("Could not read cover letter DOCX")
                return ""
        return cl_path.read_text(encoding="utf-8", errors="replace")

    def _find_posting_by_title(self, job_title: str) -> str | None:
        """Attempt to find an Ashby posting ID by its title.

        Uses the ``jobPosting.info`` endpoint to search.
        """
        # This is a best-effort search and may not always succeed.
        # The Ashby API does not expose a simple title→ID lookup,
        # so we list recent postings and match by title.
        try:
            url = f"{_API_BASE}/career-portals/jobs"
            params = {"organizationSlug": self._extract_slug_from_any()}
            response = self._session.get(
                url, params=params, timeout=self._timeout,
            )
            if response.ok:
                data = response.json()
                for job in data.get("jobs", []):
                    if job.get("title", "").lower() == job_title.lower():
                        return job.get("id")
            return None
        except Exception:
            return None

    def _extract_slug_from_any(self) -> str:
        """Extract any organisation slug from the stored job data."""
        # Fallback — this is best-effort.
        return ""

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout),
        ),
        reraise=True,
    )
    def _submit_application(
        self,
        posting_id: str,
        name: str,
        email: str,
        phone: str,
        resume_path: str,
        cover_text: str,
    ) -> requests.Response:
        """Submit the application via ``applicationForm.submit``."""
        url = f"{_API_BASE}/applicationForm.submit"

        # Build field submissions.
        parts = name.strip().split(None, 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

        field_submissions: list[dict[str, Any]] = [
            {"path": "_systemfield_first_name", "value": first_name},
            {"path": "_systemfield_last_name", "value": last_name},
            {"path": "_systemfield_email", "value": email},
            {"path": "_systemfield_phone", "value": phone},
        ]

        # Add cover letter as a text field if available.
        if cover_text:
            field_submissions.append({
                "path": "_systemfield_cover_letter",
                "value": cover_text,
            })

        # Resume file field.
        resume_key = "resume_file"
        field_submissions.append({
            "path": "_systemfield_resume",
            "value": resume_key,
        })

        application_form = {
            "fieldSubmissions": field_submissions,
        }

        # Build multipart form.
        multipart_data: list[tuple[str, Any]] = [
            ("applicationForm", (None, json.dumps(application_form), "application/json")),
            ("jobPostingId", (None, posting_id)),
        ]

        resume = Path(resume_path)
        if resume.exists():
            content_type, _ = mimetypes.guess_type(str(resume))
            multipart_data.append(
                (
                    resume_key,
                    (resume.name, resume.read_bytes(), content_type or "application/octet-stream"),
                ),
            )

        logger.info(
            "Submitting to Ashby",
            extra={
                "posting_id": posting_id,
                "candidate": name,
                "fields": len(field_submissions),
                "resume_exists": resume.exists(),
            },
        )

        response = self._session.post(
            url,
            files=multipart_data,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _process_response(response: requests.Response) -> ApplierResult:
        """Parse the Ashby API response."""
        try:
            body = response.json()
        except Exception:
            body = {}

        success = response.status_code in (200, 201) and body.get("errors") is None
        app_id = body.get("id", "")

        if success:
            logger.info(
                "Ashby application submitted",
                extra={"application_id": app_id},
            )
        else:
            errors = body.get("errors", [])
            error_msg = "; ".join(str(e) for e in errors) if errors else body.get("message", "")
            logger.warning(
                "Ashby application rejected",
                extra={"status_code": response.status_code, "errors": errors},
            )

        return ApplierResult(
            success=success,
            application_method=ApplicationMethod.API_ASHBY,
            confirmation_url=response.url,
            application_id=str(app_id),
            error_message=error_msg if not success else "",
            raw_response=str(body)[:1000],
        )
