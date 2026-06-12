"""Database package — Firebase Firestore initialisation and helpers."""

from __future__ import annotations

import logging
from typing import Optional

from app.config.settings import Settings

logger = logging.getLogger("job_automation_bot")

_firebase_initialised = False


def initialize(settings: Settings) -> None:
    """Initialise the Firebase Admin SDK using service account credentials.

    Args:
        settings: Application settings with firebase_credentials_path
            and firebase_project_id.
    """
    global _firebase_initialised
    if _firebase_initialised:
        return

    if not settings.firebase_credentials_path:
        logger.warning("FIREBASE_CREDENTIALS_PATH not set — Firebase disabled")
        return

    try:
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(settings.firebase_credentials_path)
        firebase_admin.initialize_app(
            cred,
            options={"projectId": settings.firebase_project_id} if settings.firebase_project_id else None,
        )
        _firebase_initialised = True
        logger.info("Firebase initialised successfully")
    except Exception:
        logger.exception("Failed to initialise Firebase")
        _firebase_initialised = False


def is_initialized() -> bool:
    """Check if Firebase has been initialised."""
    return _firebase_initialised


# Re-export for convenience
from app.database.firestore_repository import FirestoreRepository  # noqa: E402, F401
