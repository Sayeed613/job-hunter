"""Firestore repository for Project Headhunter.

Provides a thin data-access layer over Cloud Firestore that handles
serialisation / deserialisation of domain models and encapsulates
collection references.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud.firestore import Client as FirestoreClient
from google.cloud.firestore import (
    DocumentReference,
    DocumentSnapshot,
)
from google.cloud.firestore_v1.base_query import FieldFilter

from app.models.application import Application, ApplicationStatus
from app.models.job import Job

logger = logging.getLogger("headhunter")

# ── Collection names ─────────────────────────────────────────
_COLLECTION_JOBS = "jobs"
_COLLECTION_APPLICATIONS = "applications"


class FirestoreRepository:
    """Data-access layer for Firestore collections.

    Uses the singleton Firestore client obtained via the Firebase Admin
    SDK.  Callers **must** initialise ``firebase_admin`` before creating
    an instance of this repository (see :class:`FirebaseClient`).
    """

    def __init__(self, client: FirestoreClient | None = None) -> None:
        """Initialise the repository with an optional pre-configured client.

        Args:
            client: A Firestore ``Client`` instance.  When ``None`` the
                repository attempts to obtain the default client via
                ``firebase_admin.firestore.client()``.
        """
        self._client = client  # lazy-resolve in _db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _db(self) -> FirestoreClient:
        """Return the Firestore client, resolving lazily if needed."""
        if self._client is None:
            import firebase_admin  # noqa: PLC0415 — delayed import
            from firebase_admin import firestore  # noqa: PLC0415

            self._client = firestore.client()
        return self._client

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _job_to_dict(job: Job) -> dict[str, Any]:
        return {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "description": job.description,
            "source": job.source,
            "created_at": job.created_at,
            "match_score": job.match_score,
        }

    @staticmethod
    def _dict_to_job(data: dict[str, Any]) -> Job:
        return Job(
            id=data.get("id", ""),
            title=data.get("title", ""),
            company=data.get("company", ""),
            location=data.get("location", ""),
            url=data.get("url", ""),
            description=data.get("description", ""),
            source=data.get("source", ""),
            created_at=_ensure_datetime(data.get("created_at")),
            match_score=data.get("match_score"),
        )

    @staticmethod
    def _application_to_dict(application: Application) -> dict[str, Any]:
        return {
            "id": application.id,
            "job_id": application.job_id,
            "company": application.company,
            "role": application.role,
            "resume_version": application.resume_version,
            "cover_letter_version": application.cover_letter_version,
            "match_score": application.match_score,
            "status": application.status.name,
            "applied_at": application.applied_at,
            "job_url": application.job_url,
        }

    @staticmethod
    def _dict_to_application(data: dict[str, Any]) -> Application:
        status_str = data.get("status", "NEW")
        try:
            status = ApplicationStatus[status_str]
        except KeyError:
            status = ApplicationStatus.NEW
            logger.warning(
                "Unknown application status %r, falling back to NEW",
                status_str,
            )

        return Application(
            id=data.get("id", ""),
            job_id=data.get("job_id", ""),
            company=data.get("company", ""),
            role=data.get("role", ""),
            resume_version=data.get("resume_version", ""),
            cover_letter_version=data.get("cover_letter_version", ""),
            match_score=data.get("match_score"),
            status=status,
            applied_at=_ensure_datetime(data.get("applied_at")),
            job_url=data.get("job_url", ""),
        )

    # ------------------------------------------------------------------
    # Job operations
    # ------------------------------------------------------------------

    def save_job(self, job: Job) -> str:
        """Create or overwrite a job document.

        Args:
            job: The job to persist.

        Returns:
            The document ID of the saved job.

        Raises:
            GoogleAPICallError: On Firestore write failure.
        """
        try:
            doc_ref: DocumentReference = (
                self._db()
                .collection(_COLLECTION_JOBS)
                .document(job.id)
            )
            doc_ref.set(self._job_to_dict(job))
            logger.info("Job saved", extra={"job_id": job.id, "title": job.title})
            return job.id
        except GoogleAPICallError:
            logger.exception("Failed to save job %s", job.id)
            raise

    def get_job(self, job_id: str) -> Optional[Job]:
        """Retrieve a job document by its ID.

        Args:
            job_id: The document ID to look up.

        Returns:
            The :class:`Job` if found, otherwise ``None``.
        """
        try:
            doc_ref: DocumentReference = (
                self._db()
                .collection(_COLLECTION_JOBS)
                .document(job_id)
            )
            snapshot: DocumentSnapshot = doc_ref.get()

            if not snapshot.exists:
                logger.info("Job not found", extra={"job_id": job_id})
                return None

            data = snapshot.to_dict()
            if data is None:
                return None

            data["id"] = snapshot.id
            return self._dict_to_job(data)

        except (GoogleAPICallError, NotFound):
            logger.exception("Failed to retrieve job %s", job_id)
            return None

    def job_exists(self, job_url: str) -> bool:
        """Check whether a job with the given URL already exists.

        Args:
            job_url: The URL of the job posting.

        Returns:
            ``True`` if at least one document with that URL exists.
        """
        try:
            docs = (
                self._db()
                .collection(_COLLECTION_JOBS)
                .where(filter=FieldFilter("url", "==", job_url))
                .limit(1)
                .get()
            )
            return len(docs) > 0
        except (GoogleAPICallError, NotFound):
            logger.exception(
                "Failed to check existence of job with URL %s", job_url
            )
            return False

    # ------------------------------------------------------------------
    # Application operations
    # ------------------------------------------------------------------

    def save_application(self, application: Application) -> str:
        """Create or overwrite an application document.

        Args:
            application: The application to persist.

        Returns:
            The document ID of the saved application.

        Raises:
            GoogleAPICallError: On Firestore write failure.
        """
        try:
            doc_ref: DocumentReference = (
                self._db()
                .collection(_COLLECTION_APPLICATIONS)
                .document(application.id)
            )
            doc_ref.set(self._application_to_dict(application))
            logger.info(
                "Application saved",
                extra={
                    "app_id": application.id,
                    "company": application.company,
                    "role": application.role,
                },
            )
            return application.id
        except GoogleAPICallError:
            logger.exception(
                "Failed to save application %s", application.id
            )
            raise

    def get_application(self, application_id: str) -> Optional[Application]:
        """Retrieve an application document by its ID.

        Args:
            application_id: The document ID to look up.

        Returns:
            The :class:`Application` if found, otherwise ``None``.
        """
        try:
            doc_ref: DocumentReference = (
                self._db()
                .collection(_COLLECTION_APPLICATIONS)
                .document(application_id)
            )
            snapshot: DocumentSnapshot = doc_ref.get()

            if not snapshot.exists:
                logger.info(
                    "Application not found", extra={"app_id": application_id}
                )
                return None

            data = snapshot.to_dict()
            if data is None:
                return None

            data["id"] = snapshot.id
            return self._dict_to_application(data)

        except (GoogleAPICallError, NotFound):
            logger.exception(
                "Failed to retrieve application %s", application_id
            )
            return None

    def update_application_status(
        self,
        application_id: str,
        status: ApplicationStatus,
    ) -> None:
        """Update the status of an existing application.

        Args:
            application_id: The document ID to update.
            status: The new status value.

        Raises:
            ValueError: If the application document does not exist.
            GoogleAPICallError: On Firestore write failure.
        """
        try:
            doc_ref: DocumentReference = (
                self._db()
                .collection(_COLLECTION_APPLICATIONS)
                .document(application_id)
            )
            snapshot: DocumentSnapshot = doc_ref.get()

            if not snapshot.exists:
                raise ValueError(
                    f"Application {application_id} does not exist"
                )

            doc_ref.update({"status": status.name})
            logger.info(
                "Application status updated",
                extra={"app_id": application_id, "status": status.name},
            )
        except GoogleAPICallError:
            logger.exception(
                "Failed to update status for application %s", application_id
            )
            raise

    def list_recent_applications(
        self,
        limit: int = 20,
    ) -> list[Application]:
        """Return the most recently submitted applications.

        Results are ordered by ``applied_at`` descending.

        Args:
            limit: Maximum number of documents to return (default 20).

        Returns:
            A list of :class:`Application` instances, newest first.
        """
        applications: list[Application] = []
        try:
            docs = (
                self._db()
                .collection(_COLLECTION_APPLICATIONS)
                .order_by("applied_at", direction="DESCENDING")
                .limit(limit)
                .get()
            )

            for snapshot in docs:
                data = snapshot.to_dict()
                if data is None:
                    continue
                data["id"] = snapshot.id
                applications.append(self._dict_to_application(data))

            logger.info(
                "Listed recent applications",
                extra={"count": len(applications)},
            )
        except (GoogleAPICallError, NotFound):
            logger.exception("Failed to list recent applications")

        return applications


# ── Helpers ──────────────────────────────────────────────────


def _ensure_datetime(value: object) -> datetime:
    """Coerce *value* to a timezone-aware :class:`datetime`.

    Firestore returns ``uint64``-encoded timestamps that deserialise to
    naive ``datetime`` objects on some SDK versions.  This helper
    normalises them.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.now(timezone.utc)
