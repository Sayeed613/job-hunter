"""Firebase Admin SDK initializer — singleton with safe re-entry."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials

from app.config.settings import Settings

logger = logging.getLogger("headhunter")

# Module-level state so imports don't accidentally re-init.
_initialized: bool = False


def initialize(settings: Settings | None = None) -> None:
    """Initialise the Firebase Admin SDK exactly once.

    Credentials are loaded from the file path specified by the
    ``FIREBASE_CREDENTIALS_PATH`` environment variable.  If the
    variable is empty or the file is missing the function logs a
    warning and returns — the app can still start in a degraded
    mode (Firestore calls will fail gracefully).

    It is safe to call this function multiple times; subsequent
    calls are no-ops.

    Args:
        settings: Optional :class:`Settings` instance.  When
            ``None`` a fresh instance is created (which loads
            from the environment / ``.env`` file).
    """
    global _initialized  # noqa: PLW0603

    if _initialized:
        logger.debug("Firebase Admin SDK already initialised — skipping")
        return

    cfg = settings or Settings()

    cred_path = cfg.firebase_credentials_path
    if not cred_path:
        logger.warning(
            "FIREBASE_CREDENTIALS_PATH is not set — Firebase will not be "
            "available.  Set this variable to a valid service-account JSON "
            "file path and restart."
        )
        return

    cred_file = Path(cred_path)
    if not cred_file.is_file():
        logger.warning(
            "Firebase credentials file not found at %s — Firebase will not "
            "be available.  Check FIREBASE_CREDENTIALS_PATH and restart.",
            cred_file.resolve(),
        )
        return

    try:
        cred = credentials.Certificate(str(cred_file))
        options: dict[str, Any] = {}
        if cfg.firebase_database_url:
            options["databaseURL"] = cfg.firebase_database_url
        if cfg.firebase_project_id:
            options["projectId"] = cfg.firebase_project_id

        firebase_admin.initialize_app(cred, options=options)
        _initialized = True
        logger.info(
            "Firebase Admin SDK initialised",
            extra={
                "project_id": cfg.firebase_project_id or "auto-detected",
                "credential_file": str(cred_file),
            },
        )
    except Exception:
        logger.exception("Failed to initialise Firebase Admin SDK")
        raise


def is_initialized() -> bool:
    """Return ``True`` if the Firebase SDK has been initialised."""
    return _initialized
