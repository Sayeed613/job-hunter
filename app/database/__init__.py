"""Database package — local JSON-file persistence (no external services).

Stores application records in ``storage/applications.json``.
No Firebase, no network calls, no configuration needed.
"""

from app.database.firestore_repository import FirestoreRepository

__all__ = [
    "FirestoreRepository",
]
