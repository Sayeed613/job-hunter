"""Local JSON-file repository — replaces Firestore with zero external dependencies.

Stores application records in a local JSON file (``storage/applications.json``).
Same interface as the old FirestoreRepository — drop-in replacement.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.models.application import Application

logger = logging.getLogger("job_automation_bot")

_APPLICATIONS_FILE = Path("storage/applications.json")


class FirestoreRepository:
    """Local JSON-file data store for application records.

    All methods are synchronous (no network calls). Data is persisted to
    ``storage/applications.json``. If the file doesn't exist, an empty
    store is assumed.
    """

    def __init__(self, client: object = None) -> None:
        """Initialise the repository.

        Args:
            client: Ignored (kept for backward compatibility with Firestore API).
        """
        _ = client  # unused — kept for compat
        self._applications: dict[str, dict[str, Any]] = {}
        self._loaded = False

    # ── Internal helpers ─────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Load applications from disk on first access."""
        if self._loaded:
            return
        if _APPLICATIONS_FILE.exists():
            try:
                data = json.loads(_APPLICATIONS_FILE.read_text(encoding="utf-8"))
                self._applications = data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load applications file: %s", exc)
                self._applications = {}
        else:
            self._applications = {}
        self._loaded = True

    def _save(self) -> None:
        """Persist applications to disk."""
        _APPLICATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _APPLICATIONS_FILE.write_text(
            json.dumps(self._applications, indent=2, default=str),
            encoding="utf-8",
        )

    # ── Application methods ──────────────────────────────────

    def save_application(self, app: Application) -> Optional[str]:
        """Save an application record. Returns the document ID."""
        self._ensure_loaded()
        doc_id = self._application_doc_id(app)
        self._applications[doc_id] = self._application_to_dict(app)
        self._save()
        logger.info("Application saved", extra={"doc_id": doc_id, "company": app.company})
        return doc_id

    def get_application(self, job_id: str) -> Optional[Application]:
        """Get an application by its job_id (sha256 of company+title)."""
        self._ensure_loaded()
        for doc_id, data in self._applications.items():
            if data.get("job_id") == job_id:
                return self._dict_to_application(data)
        return None

    def list_recent_applications(self, limit: int = 50) -> list[Application]:
        """Return most recent applications."""
        self._ensure_loaded()
        sorted_apps = sorted(
            self._applications.values(),
            key=lambda a: a.get("applied_at") or "",
            reverse=True,
        )
        apps: list[Application] = []
        for data in sorted_apps[:limit]:
            apps.append(self._dict_to_application(data))
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
            "posted_at": str(app.posted_at) if app.posted_at else None,
            "applied_at": str(app.applied_at) if app.applied_at else None,
            "status": app.status,
            "application_method": app.application_method,
            "resume_path": app.resume_path,
            "cover_letter_path": app.cover_letter_path,
            "matched_keywords": app.matched_keywords,
            "match_score": app.match_score,
            "error_message": app.error_message,
            "interview_status": app.interview_status,
            "notes": app.notes,
            "created_at": str(app.created_at) if app.created_at else None,
            "updated_at": str(app.updated_at) if app.updated_at else None,
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
            posted_at=_parse_dt(data.get("posted_at")),
            applied_at=_parse_dt(data.get("applied_at")),
            status=data.get("status", "applied"),
            application_method=data.get("application_method", ""),
            resume_path=data.get("resume_path", ""),
            cover_letter_path=data.get("cover_letter_path", ""),
            matched_keywords=data.get("matched_keywords", []),
            match_score=data.get("match_score", 0.0),
            error_message=data.get("error_message"),
            interview_status=data.get("interview_status", "no_response"),
            notes=data.get("notes", ""),
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
        )


def _parse_dt(value: object) -> Optional[datetime]:
    """Parse a datetime string back to a datetime object."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return None
