"""Base class for all job providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.job import Job


class BaseJobProvider(ABC):
    """Abstract base class for job platform providers.

    All providers inherit from this and implement fetch_jobs().
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'RemoteOK')."""

    @abstractmethod
    async def fetch_jobs(self) -> list[Job]:
        """Fetch latest jobs from this platform.

        Returns:
            A list of Job objects.

        Raises:
            Should never raise — catch all exceptions and return [].
        """
