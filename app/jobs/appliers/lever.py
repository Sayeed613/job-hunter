"""Lever Postings API application submitter.

Submits applications to Lever-hosted job boards using the
``POST /v0/postings/{site}/{posting_id}?key={api_key}`` endpoint.

Supports ``multipart/form-data`` for resume file upload.
Cover letter text is sent via the ``comments`` field.
"""

from __future__ import annotations

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

_API_BASE = "https://api.lever.co/v0/postings"

# Regex to extract Lever site name and posting ID from URL.
# Matches:
#   jobs.lever.co/{site}/{posting_id}
#   jobs.lever.co/{site}/{posting_id}?...
_LEVER_URL_RE = re.compile(
    r"jobs\.lever\.co/([^/]+)/([a-zA-Z0-9-]+)",
    re.IGNORECASE,
)


class LeverApplier(BaseApplier):
    """Submit applications to Lever job boards via their Postings API.

    The applier auto-detects the ``site`` and ``posting_id`` from the
    job's application URL.  If the URL cannot be parsed, the submission
    is marked as a failure.

    Requires a ``LEVER_API_KEY`` — see :class:`Settings.lever_api_key`.

    Example usage::

        applier = LeverApplier(api_key="abc123")
        result = applier.apply(
            candidate_name="Jane Doe",
            candidate_email="jane@example.com",
            ...
        )
    """

    display_name = "Lever API"

    def __init__(
        self,
        api_key: str = "",
        timeout: int = 60,
    ) -> None:
        """Initialise the applier.

        Args:
            api_key: Lever API key.  Falls back to
                ``Settings.lever_api_key``.
            timeout: HTTP request timeout in seconds (default 60).
        """
        from app.config.settings import Settings  # noqa: PLC0415

        cfg = Settings()
        self._api_key = api_key or cfg.lever_api_key or ""
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
                "LEVER_API_KEY not set — Lever submissions will fail."
            )

        logger.info(
            "LeverApplier initialised",
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
        """Submit an application via the Lever Postings API.

        Args:
            candidate_name: Full name.
            candidate_email: Email address.
            candidate_phone: Phone number.
            resume_path: Path to the tailored resume file.
            cover_letter_path: Path to the cover letter file.
            job_title: Unused (the posting ID is extracted from URL).
            job_apply_url: The job posting URL — used to extract site
                and posting ID.
            job_description: Unused by Lever.
            extra: Optional overrides — if ``site`` or ``posting_id``
                are provided they take precedence.

        Returns:
            An :class:`ApplierResult` with the API response details.
        """
        if not self._api_key:
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.API_LEVER,
                error_message="LEVER_API_KEY not configured.",
            )

        # Extract or use override values.
        site: str | None = None
        posting_id: str | None = None

        if extra:
            site = extra.get("site")
            posting_id = extra.get("posting_id")

        if not site or not posting_id:
            parsed = self._parse_url(job_apply_url)
            if parsed:
                site, posting_id = parsed

        if not site or not posting_id:
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.API_LEVER,
                error_message=(
                    f"Cannot parse Lever URL: {job_apply_url}. "
                    "Expected format: jobs.lever.co/{site}/{posting_id}"
                ),
            )

        # Read cover letter text.
        cover_text = self._read_cover_letter(cover_letter_path)

        try:
            response = self._post_application(
                site=site,
                posting_id=posting_id,
                name=candidate_name,
                email=candidate_email,
                phone=candidate_phone,
                resume_path=resume_path,
                comments=cover_text,
            )
        except requests.RequestException as exc:
            logger.exception("Lever API request failed")
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.API_LEVER,
                error_message=f"HTTP request failed: {exc}",
                raw_response=str(exc),
            )

        return self._process_response(response)

    # ── URL parsing ─────────────────────────────────────────

    @staticmethod
    def can_handle(job_apply_url: str) -> bool:
        """Return ``True`` if this applier can handle the given URL."""
        return bool(_LEVER_URL_RE.search(job_apply_url))

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str] | None:
        """Extract ``(site, posting_id)`` from a Lever URL."""
        match = _LEVER_URL_RE.search(url)
        if match:
            return match.group(1), match.group(2)
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
        site: str,
        posting_id: str,
        name: str,
        email: str,
        phone: str,
        resume_path: str,
        comments: str,
    ) -> requests.Response:
        """POST the application via multipart/form-data."""
        url = f"{_API_BASE}/{site}/{posting_id}?key={self._api_key}"

        data: dict[str, Any] = {
            "name": name,
            "email": email,
            "phone": phone,
        }
        if comments:
            data["comments"] = comments

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
            "Submitting to Lever",
            extra={
                "url": url,
                "candidate": name,
                "resume_size": resume.stat().st_size if resume.exists() else 0,
            },
        )

        response = self._session.post(
            url,
            data=data,
            files=files if files else None,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _process_response(response: requests.Response) -> ApplierResult:
        """Parse the Lever API response."""
        try:
            body = response.json()
        except Exception:
            body = {}

        success = response.status_code in (200, 201)
        app_id = body.get("id", "")

        if success:
            logger.info(
                "Lever application submitted",
                extra={"application_id": app_id},
            )
        else:
            logger.warning(
                "Lever application rejected",
                extra={"status_code": response.status_code},
            )

        return ApplierResult(
            success=success,
            application_method=ApplicationMethod.API_LEVER,
            confirmation_url=response.url,
            application_id=str(app_id),
            error_message=body.get("error", "") if not success else "",
            raw_response=str(body)[:1000],
        )
