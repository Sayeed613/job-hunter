"""Job orchestration service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.database.firestore_repository import FirestoreRepository
from app.jobs.providers import JobProvider

logger = logging.getLogger("headhunter")


@dataclass
class JobCollectionResult:
    """Aggregate statistics returned after a job collection run."""

    total_found: int = 0
    new_jobs: int = 0
    duplicates: int = 0


class JobService:
    """Orchestrates job collection across multiple providers.

    The service:

    1. Iterates over all registered :class:`JobProvider` instances.
    2. Fetches raw listings from each provider.
    3. Deduplicates by URL (in-memory during the run and against Firestore).
    4. Skips jobs whose URL already exists in the database.
    5. Normalises and persists new jobs via :class:`FirestoreRepository`.
    """

    def __init__(
        self,
        repository: FirestoreRepository,
        providers: list[JobProvider] | None = None,
    ) -> None:
        """Initialise the service.

        Args:
            repository: A Firestore repository instance for persistence.
            providers: Optional list of providers to register immediately.
        """
        self._repository = repository
        self._providers: list[JobProvider] = providers or []

    def register_provider(self, provider: JobProvider) -> None:
        """Register an additional job provider.

        Args:
            provider: A :class:`JobProvider` implementation.
        """
        self._providers.append(provider)

    def collect_jobs(self) -> JobCollectionResult:
        """Execute a full job collection cycle.

        The method iterates through each registered provider, fetches raw
        jobs, deduplicates them, and persists new ones.

        Returns:
            A :class:`JobCollectionResult` with aggregate statistics.
        """
        stats = JobCollectionResult()

        if not self._providers:
            logger.warning("No job providers registered — skipping collection")
            return stats

        seen_urls: set[str] = set()

        for provider in self._providers:
            provider_name = provider.__class__.__name__

            # ── Fetch ────────────────────────────────────────
            try:
                raw_jobs = provider.fetch_jobs()
            except Exception:
                logger.exception(
                    "Provider %s raised an unhandled exception during fetch",
                    provider_name,
                )
                continue

            logger.info(
                "Fetched raw jobs from provider",
                extra={"provider": provider_name, "count": len(raw_jobs)},
            )

            # ── Process each listing ──────────────────────────
            for raw in raw_jobs:
                stats.total_found += 1

                # In-memory dedup (same run, multiple providers).
                if raw.url in seen_urls:
                    stats.duplicates += 1
                    continue
                seen_urls.add(raw.url)

                # Firestore dedup (already persisted).
                try:
                    if self._repository.job_exists(raw.url):
                        stats.duplicates += 1
                        continue
                except Exception:
                    logger.exception(
                        "Error checking job existence for %s",
                        raw.url,
                    )
                    stats.duplicates += 1
                    continue

                # Normalise & save.
                try:
                    job = provider.normalize_job(raw)
                    self._repository.save_job(job)
                    stats.new_jobs += 1
                except Exception:
                    logger.exception(
                        "Failed to normalise or save job %s",
                        raw.url,
                    )

        logger.info(
            "Job collection complete",
            extra={
                "total_found": stats.total_found,
                "new_jobs": stats.new_jobs,
                "duplicates": stats.duplicates,
            },
        )

        return stats
