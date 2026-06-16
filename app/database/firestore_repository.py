"""Firestore repository for the Job Automation Bot.

Provides async CRUD operations for job and application documents.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optionalhi

from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud.firestore import Client as FirestoreClient
from google.cloud.firestore_v1.base_query import FieldFilter

from app.models.application import Application
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_COLLECTION_APPLICATIONS = "applications"
_COLLECTION_JOBS = "jobs"


class FirestoreRepository:
    """Async data-access layer for Firestore collections.

    Uses the Firestore client obtained via the Firebase Admin SDK.
    """

    def __init__(self, client: FirestoreClient | None = None) -> None:
        self._client = client

    def _db(self) -> FirestoreClient:
        if self._client is None:
            import firebase_admin
            from firebase_admin import firestore
            self._client = firestore.client()
        return self._client

    # ── Application methods ──────────────────────────────────

    def save_application(self, app: Application) -> str:
        """Save an application record. Returns the document ID."""
        doc_id = self._application_doc_id(app)
        doc_ref = self._db().collection(_COLLECTION_APPLICATIONS).document(doc_id)
        doc_ref.set(self._application_to_dict(app), merge=True)
        logger.info("Application saved", extra={"doc_id": doc_id, "company": app.company})
        return doc_id

    def get_application(self, job_id: str) -> Optional[Application]:
        """Get an application by its job_id (sha256 of company+title)."""
        try:
            docs = (
                self._db()
                .collection(_COLLECTION_APPLICATIONS)
                .where(filter=FieldFilter("job_id", "==", job_id))
                .limit(1)
                .get()
            )
            if docs and len(docs) > 0:
                data = docs[0].to_dict()
                if data:
                    return self._dict_to_application(data)
            return None
        except (GoogleAPICallError, NotFound):
            return None

    def list_recent_applications(self, limit: int = 50) -> list[Application]:
        """Return most recent applications."""
        apps: list[Application] = []
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
                if data:
                    apps.append(self._dict_to_application(data))
        except (GoogleAPICallError, NotFound):
            logger.exception("Failed to list applications")
        return apps

    def get_stats(self) -> dict[str, Any]:
        """Get aggregate application statistics."""
        apps = self.list_recent_applications(limit=1000)
        total = len(apps)
        applied = sum(1 for a in apps if a.status == "applied")
        failed = sum(1 for a in apps if a.status == "failed")
        success_count = sum(1 for a in apps if a.match_score > 0)
        return {
            "total_applied": total,
            "successful": applied,
            "failed": failed,
            "success_count": success_count,
        }

    # ── Serialisation ────────────────────────────────────────

    @staticmethod
    def _application_doc_id(app: Application) -> str:
        """Generate a deterministic document ID from company+title."""
        raw = f"{app.company.lower().strip()}:{app.title.lower().strip()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def _application_to_dict(app: Application) -> dict[str, Any]:
        return {
            "job_id": app.job_id,
            "title": app.title,
            "company": app.company,
            "location": app.location,
            "remote_type": app.remote_type,
            "job_type": app.job_type,
            "salary": app.salary,
            "source": app.source,
            "apply_url": app.apply_url,
            "posted_at": app.posted_at,
            "applied_at": app.applied_at or datetime.now(timezone.utc),
            "status": app.status,
            "application_method": app.application_method,
            "resume_path": app.resume_path,
            "cover_letter_path": app.cover_letter_path,
            "matched_keywords": app.matched_keywords,
            "match_score": app.match_score,
            "error_message": app.error_message,
            "interview_status": app.interview_status,
            "notes": app.notes,
            "created_at": app.created_at,
            "updated_at": datetime.now(timezone.utc),
        }

    @staticmethod
    def _dict_to_application(data: dict[str, Any]) -> Application:
        return Application(
            job_id=data.get("job_id", ""),
            title=data.get("title", ""),
            company=data.get("company", ""),
            location=data.get("location", ""),
            remote_type=data.get("remote_type", "Remote"),
            job_type=data.get("job_type", "Full-time"),
            salary=data.get("salary"),
            source=data.get("source", ""),
            apply_url=data.get("apply_url", ""),
            posted_at=data.get("posted_at"),
            applied_at=_ensure_datetime(data.get("applied_at")),
            status=data.get("status", "applied"),
            application_method=data.get("application_method", ""),
            resume_path=data.get("resume_path", ""),
            cover_letter_path=data.get("cover_letter_path", ""),
            matched_keywords=data.get("matched_keywords", []),
            match_score=data.get("match_score", 0.0),
            error_message=data.get("error_message"),
            interview_status=data.get("interview_status", "no_response"),
            notes=data.get("notes", ""),
            created_at=_ensure_datetime(data.get("created_at")),
            updated_at=_ensure_datetime(data.get("updated_at")),
        )


def _ensure_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return datetime.now(timezone.utc)
