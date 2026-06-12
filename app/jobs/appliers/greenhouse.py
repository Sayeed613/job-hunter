"""Greenhouse job-board API application submitter.

Submits applications to Greenhouse-hosted job boards using the
``POST /v1/boards/{board_token}/jobs/{job_id}`` endpoint.

Supports both ``multipart/form-data`` (file upload) and
``application/json`` (base64-encoded file) formats.

.. note::
    Greenhouse requires a **Job Board API key** for authenticated
    submissions.  This must be configured via ``Settings.greenhouse_api_key``
    or the ``GREENHOUSE_API_KEY`` environment variable.
"""

from __future__ import annotations

import logging
import mimetypes
import os
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

_API_BASE = "https://boards-api.greenhouse.io/v1/boards"

# ── Regex to extract board token and job ID from a Greenhouse URL ──
# Matches patterns like:
#   boards.greenhouse.io/{board_token}/jobs/{job_id}
#   boards.greenhouse.io/{board_token}/jobs/{job_id}?...
_GREENHOUSE_URL_RE = re.compile(
    r"boards\.greenhouse\.io/([^/]+)/jobs/(\d+)",
    re.IGNORECASE,
)


class GreenhouseApplier(BaseApplier):
    """Submit applications to Greenhouse job boards via their public API.

    The applier auto-detects the ``board_token`` and ``job_id`` from
    the job's application URL.  If the URL cannot be parsed, the
    submission is marked as a failure.

    Requires a ``GREENHOUSE_API_KEY`` — see :class:`Settings.greenhouse_api_key`.

    Example usage::

        applier = GreenhouseApplier(api_key="abc123")
        result = applier.apply(
            candidate_name="Jane Doe",
            candidate_email="jane@example.com",
            ...
        )
    """

    display_name = "Greenhouse API"

    def __init__(
        self,
        api_key: str = "",
        timeout: int = 60,
    ) -> None:
        """Initialise the applier.

        Args:
            api_key: Greenhouse Job Board API key.  Falls back to
                ``Settings.greenhouse_api_key``.
            timeout: HTTP request timeout in seconds (default 60).
        """
        # Lazy import to avoid circular dependency at module level.
        from app.config.settings import Settings  # noqa: PLC0415

        cfg = Settings()
        self._api_key = api_key or cfg.greenhouse_api_key or ""
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "ProjectHeadhunter/1.0",
                "Accept": "application/json",
            },
        )

        if not self._api_key:
            logger.warning(
                "GREENHOUSE_API_KEY not set — Greenhouse submissions "
                "will fail with authentication errors."
            )

        logger.info(
            "GreenhouseApplier initialised",
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
        job_title: str,  # noqa: ARG002 — kept for interface consistency
        job_apply_url: str,
        job_description: str,  # noqa: ARG002 — kept for interface consistency
        extra: dict | None = None,
    ) -> ApplierResult:
        """Submit an application via the Greenhouse Job Board API.

        Args:
            candidate_name: Full name (used to derive ``first_name`` /
                ``last_name``).
            candidate_email: Email address.
            candidate_phone: Phone number.
            resume_path: Path to the tailored resume file.
            cover_letter_path: Path to the cover letter file.
            job_title: Unused by Greenhouse (the job ID is extracted
                from the URL).
            job_apply_url: The job posting URL — used to extract the
                board token and job ID.
            job_description: Unused by Greenhouse.
            extra: Optional overrides — if ``board_token`` or
                ``job_id`` are provided here they take precedence over
                URL parsing.

        Returns:
            An :class:`ApplierResult` with the API response details.
        """
        if not self._api_key:
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.API_GREENHOUSE,
                error_message="GREENHOUSE_API_KEY not configured.",
            )

        # Extract or use override values.
        board_token: str | None = None
        job_id: str | None = None

        if extra:
            board_token = extra.get("board_token")
            job_id = extra.get("job_id")

        if not board_token or not job_id:
            parsed = self._parse_url(job_apply_url)
            if parsed:
                board_token, job_id = parsed

        if not board_token or not job_id:
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.API_GREENHOUSE,
                error_message=(
                    f"Cannot parse Greenhouse URL: {job_apply_url}. "
                    "Expected format: boards.greenhouse.io/{board}/jobs/{id}"
                ),
            )

        # Build the request.
        url = f"{_API_BASE}/{board_token}/jobs/{job_id}"

        # Split name.
        parts = candidate_name.strip().split(None, 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

        # Read cover letter text for the ``comments`` field.
        cover_text = ""
        cl_path = Path(cover_letter_path)
        if cl_path.exists() and cl_path.suffix == ".docx":
            try:
                from docx import Document  # noqa: PLC0415
                doc = Document(str(cl_path))
                cover_text = "\n".join(
                    p.text for p in doc.paragraphs if p.text.strip()
                )
            except Exception:
                logger.warning("Could not read cover letter DOCX — sending without text")
        elif cl_path.exists():
            cover_text = cl_path.read_text(encoding="utf-8", errors="replace")

        # Use multipart form-data for file upload.
        try:
            response = self._post_application(
                url=url,
                first_name=first_name,
                last_name=last_name,
                email=candidate_email,
                phone=candidate_phone,
                resume_path=resume_path,
                cover_text=cover_text,
            )
        except requests.RequestException as exc:
            logger.exception("Greenhouse API request failed")
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.API_GREENHOUSE,
                error_message=f"HTTP request failed: {exc}",
                raw_response=str(exc),
            )

        return self._process_response(response)

    # ── URL parsing ─────────────────────────────────────────

    @staticmethod
    def can_handle(job_apply_url: str) -> bool:
        """Return ``True`` if this applier can handle the given URL."""
        return bool(_GREENHOUSE_URL_RE.search(job_apply_url))

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str] | None:
        """Extract ``(board_token, job_id)`` from a Greenhouse URL.

        Returns ``None`` if the URL does not match.
        """
        match = _GREENHOUSE_URL_RE.search(url)
        if match:
            return match.group(1), match.group(2)
        return None

    # ── Internal helpers ─────────────────────────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout),
        ),
        reraise=True,
    )
    def _post_application(
        self,
        url: str,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        resume_path: str,
        cover_text: str,
    ) -> requests.Response:
        """POST the application via multipart/form-data."""
        # Basic auth: API key as username, blank password.
        auth = (self._api_key, "")

        # Build form fields.
        data: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
        }
        if cover_text:
            data["comments"] = cover_text

        # Files.
        files: dict[str, Any] = {}
        resume = Path(resume_path)
        if resume.exists():
            content_type, _ = mimetypes.guess_type(str(resume))
            files["resume"] = (
                resume.name,
                resume.read_bytes(),
                content_type or "application/octet-stream",
            )

        logger.info(
            "Submitting to Greenhouse",
            extra={
                "url": url,
                "candidate": first_name,
                "resume_size": resume.stat().st_size if resume.exists() else 0,
                "cover_length": len(cover_text),
            },
        )

        response = self._session.post(
            url,
            auth=auth,
            data=data,
            files=files if files else None,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _process_response(response: requests.Response) -> ApplierResult:
        """Parse the Greenhouse API response into an :class:`ApplierResult`."""
        try:
            body = response.json()
        except Exception:
            body = {}

        candidate_id = body.get("id", "")
        application_id = body.get("application_id", "")

        success = response.status_code in (200, 201)

        if success:
            logger.info(
                "Greenhouse application submitted",
                extra={
                    "candidate_id": candidate_id,
                    "application_id": application_id,
                },
            )
        else:
            logger.warning(
                "Greenhouse application rejected",
                extra={
                    "status_code": response.status_code,
                    "body": body,
                },
            )

        return ApplierResult(
            success=success,
            application_method=ApplicationMethod.API_GREENHOUSE,
            confirmation_url=response.url,
            application_id=str(application_id or candidate_id),
            error_message=body.get("error") or body.get("message", "") if not success else "",
            raw_response=str(body)[:1000],
        )
