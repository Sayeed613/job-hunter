"""Email-based job application submitter.

Sends job applications via SMTP for jobs that accept email submissions.
The email includes the cover letter as the body and the tailored resume
as an attachment.

Requires SMTP configuration via settings (``smtp_host``, ``smtp_port``,
``smtp_username``, ``smtp_password``, ``smtp_from_email``).
"""

from __future__ import annotations

import logging
import re
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path
from typing import Any

from app.jobs.appliers.base import ApplierResult, ApplicationMethod, BaseApplier

logger = logging.getLogger("headhunter")

# Regex to find email addresses in job descriptions / URLs.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Common email subject line keywords in job descriptions.
_APPLY_EMAIL_KEYWORDS = [
    r"send\s+(?:your\s+)?(?:resume|application|cv)\s+to",
    r"email\s+(?:us|your\s+application)\s+at",
    r"apply\s+via\s+email",
    r"careers@",
    r"jobs@",
    r"resume@",
    r"apply@",
    r"hr@",
    r"hiring@",
]


class EmailApplier(BaseApplier):
    """Submit job applications via email (SMTP).

    The applier scans the job URL and description for an email address.
    If found, it sends the cover letter as the email body and attaches
    the tailored resume file.

    Requires SMTP credentials configured via :class:`Settings`.

    Example usage::

        applier = EmailApplier(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_username="you@gmail.com",
            smtp_password="app-password",
            from_email="you@gmail.com",
        )
        result = applier.apply(...)
    """

    display_name = "Email (SMTP)"

    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        from_email: str = "",
        use_tls: bool = True,
        timeout: int = 30,
    ) -> None:
        """Initialise the email applier.

        Args:
            smtp_host: SMTP server hostname.  Falls back to
                ``Settings.smtp_host``.
            smtp_port: SMTP port (default 587 for STARTTLS).
            smtp_username: SMTP username (usually the email address).
            smtp_password: SMTP password or app-specific password.
            from_email: ``From:`` address.  Falls back to
                ``Settings.smtp_from_email`` then ``smtp_username``.
            use_tls: Whether to use STARTTLS (default True).
            timeout: Socket timeout in seconds (default 30).
        """
        from app.config.settings import Settings  # noqa: PLC0415

        cfg = Settings()
        self._host = smtp_host or cfg.smtp_host or ""
        self._port = smtp_port or cfg.smtp_port or 587
        self._username = smtp_username or cfg.smtp_username or ""
        self._password = smtp_password or cfg.smtp_password or ""
        self._from = from_email or cfg.smtp_from_email or self._username
        self._use_tls = use_tls
        self._timeout = timeout

        self._available = bool(self._host and self._username and self._password)

        if not self._available:
            logger.warning(
                "SMTP not fully configured — EmailApplier will skip "
                "submissions. Set SMTP_HOST, SMTP_USERNAME, and "
                "SMTP_PASSWORD."
            )

        logger.info(
            "EmailApplier initialised",
            extra={
                "host": self._host,
                "port": self._port,
                "from": self._from,
                "available": self._available,
            },
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
        """Submit an application via email.

        Scans the job URL and description for a recipient email address.
        If found, sends the cover letter as the email body and attaches
        the tailored resume.

        Args:
            candidate_name: Full name.
            candidate_email: Sender email (``From:``).
            candidate_phone: Unused for email.
            resume_path: Path to the tailored resume file.
            cover_letter_path: Path to the cover letter file.
            job_title: Used in the email subject line.
            job_apply_url: Scanned for an email address.
            job_description: Scanned for an email address.
            extra: Optional — if ``recipient_email`` is provided it
                takes precedence over scanning.

        Returns:
            An :class:`ApplierResult` with the send result.
        """
        if not self._available:
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.EMAIL,
                error_message="SMTP not configured.",
            )

        # Determine recipient email.
        recipient: str | None = None
        if extra and extra.get("recipient_email"):
            recipient = extra["recipient_email"]
        else:
            recipient = self._find_email(job_apply_url, job_description)

        if not recipient:
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.EMAIL,
                error_message=(
                    "No email address found in the job description or URL. "
                    "Cannot send application via email."
                ),
            )

        # Read cover letter text.
        cover_text = self._read_file_text(cover_letter_path)

        # Build the email.
        subject = f"Application for {job_title} — {candidate_name}"
        body = cover_text or (
            f"Dear Hiring Team,\n\n"
            f"Please find attached my application for the {job_title} "
            f"position. I look forward to hearing from you.\n\n"
            f"Best regards,\n{candidate_name}"
        )

        try:
            self._send_email(
                to=recipient,
                subject=subject,
                body=body,
                resume_path=resume_path,
            )
        except Exception as exc:
            logger.exception("Failed to send application email")
            return ApplierResult(
                success=False,
                application_method=ApplicationMethod.EMAIL,
                error_message=f"Email send failed: {exc}",
                raw_response=str(exc),
            )

        logger.info(
            "Application email sent",
            extra={
                "to": recipient,
                "subject": subject,
                "resume": resume_path,
            },
        )

        return ApplierResult(
            success=True,
            application_method=ApplicationMethod.EMAIL,
            confirmation_url=f"mailto:{recipient}",
            error_message="",
        )

    # ── Internal helpers ─────────────────────────────────────

    @staticmethod
    def can_handle(job_apply_url: str, job_description: str = "") -> bool:
        """Return ``True`` if an email address is found."""
        return bool(_EMAIL_RE.search(job_apply_url)) or bool(
            _EMAIL_RE.search(job_description)
        )

    @staticmethod
    def _find_email(url: str, description: str) -> str | None:
        """Scan a URL and description for an email address.

        Returns the first discovered email address, or ``None``.
        """
        # First check if the description contains explicit apply-via-email
        # keywords — this increases confidence.
        desc_lower = description.lower()
        for pattern in _APPLY_EMAIL_KEYWORDS:
            if re.search(pattern, desc_lower):
                # Found an apply-via-email hint.
                break
        else:
            # No keyword match — still scan for any email but with
            # lower confidence.
            pass

        # Scan URL.
        emails = _EMAIL_RE.findall(url)
        if emails:
            return emails[0]

        # Scan description.
        emails = _EMAIL_RE.findall(description)
        if emails:
            return emails[0]

        return None

    @staticmethod
    def _read_file_text(path: str) -> str:
        """Read text content from a file (DOCX or plain text)."""
        file_path = Path(path)
        if not file_path.exists():
            return ""

        if file_path.suffix == ".docx":
            try:
                from docx import Document  # noqa: PLC0415
                doc = Document(str(file_path))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception:
                logger.warning("Could not read DOCX file for email body")
                return ""

        return file_path.read_text(encoding="utf-8", errors="replace")

    def _send_email(
        self,
        to: str,
        subject: str,
        body: str,
        resume_path: str,
    ) -> None:
        """Send a multipart email with the resume attached."""
        msg = MIMEMultipart()
        msg["From"] = self._from
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)

        # Cover letter as the email body.
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Attach resume.
        resume = Path(resume_path)
        if resume.exists():
            part = MIMEBase("application", "octet-stream")
            part.set_payload(resume.read_bytes())
            from email import encoders  # noqa: PLC0415
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{resume.name}"',
            )
            msg.attach(part)

        # Send via SMTP.
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
            if self._use_tls:
                server.starttls()
            if self._username and self._password:
                server.login(self._username, self._password)
            server.sendmail(self._from, [to], msg.as_string())
