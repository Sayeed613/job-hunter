"""Scheduler — recurring job collection and processing cycle using APScheduler.

Workflow (runs every 30 minutes)
---------------------------------
1. Collect new jobs from all registered providers.
2. Filter jobs by location and employment-type criteria.
3. For each qualifying job, run the :class:`ApplicationPipeline`.
4. Send a Telegram summary of the cycle.
5. Persist cycle results and update last-execution timestamp.

Duplicate prevention
--------------------
Jobs whose job ID already exists as an application record in Firestore
are skipped — the pipeline's repository is used to check for existing
application records before processing.
"""

from __future__ import annotations

import atexit
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.jobs.providers import JobProvider
from app.models.job import Job
from app.pipeline.application_pipeline import ApplicationPipeline, PipelineResult
from app.resume.models import ResumeProfile
from app.telegram.notifier import Notifier

logger = logging.getLogger("headhunter")

# ── Filter criteria — jobs matching ANY of these pass ─────────

_LOCATION_FILTERS: list[str] = [
    "bangalore",
    "hybrid bangalore",
    "remote india",
    "international remote",
]

_EMPLOYMENT_TYPE_FILTERS: list[str] = [
    "intern",
    "internship",
    "full time",
    "full-time",
    "contract",
    "part time",
    "part-time",
]

# ── State file for tracking execution ─────────────────────────

_STATE_FILE = Path("data") / "scheduler_state.json"


@dataclass
class CycleResult:
    """Aggregate statistics from a single scheduler cycle.

    Attributes:
        cycle_id: ISO-8601 timestamp identifying this cycle.
        total_collected: Number of raw job listings fetched from all
            providers.
        filtered_in: Number of jobs that passed the location/type filter.
        already_processed: Number of filtered jobs that were skipped
            because an application already exists for them.
        processed: Number of jobs sent through the pipeline.
        completed: Number of jobs that completed successfully.
        rejected: Number of jobs rejected by the recommendation engine.
        errors: Number of jobs that hit a pipeline error.
        pipeline_results: Detailed results from every pipeline run.
    """

    cycle_id: str = ""
    total_collected: int = 0
    filtered_in: int = 0
    already_processed: int = 0
    processed: int = 0
    completed: int = 0
    rejected: int = 0
    errors: int = 0
    pipeline_results: list[dict] = field(default_factory=list)


class Scheduler:
    """Recurring scheduler that collects, filters, and processes jobs.

    Uses APScheduler's :class:`BackgroundScheduler` to run a full
    cycle every 30 minutes.  Each cycle:

    #. Fetches new jobs via registered :class:`JobProvider` instances.
    #. Deduplicates against URLs already in Firestore.
    #. Saves new jobs to Firestore.
    #. Filters by location (Bangalore, Hybrid Bangalore, Remote India,
       International Remote) and employment type (Internship, Full Time,
       Contract, Part Time).
    #. Skips jobs that already have an application record.
    #. Runs the :class:`ApplicationPipeline` for each qualifying job.
    #. Sends a Telegram summary (if a notifier is configured).
    #. Persists cycle statistics and the last-execution timestamp.

    The scheduler is **started** with :meth:`start` and **stopped** with
    :meth:`shutdown` (handles ``SIGINT`` / ``SIGTERM`` gracefully).
    """

    def __init__(
        self,
        pipeline: ApplicationPipeline,
        resume: ResumeProfile,
        providers: list[JobProvider] | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            pipeline: The :class:`ApplicationPipeline` to run on each
                qualifying job.
            resume: The candidate's parsed :class:`ResumeProfile`,
                reused across all pipeline invocations in a cycle.
            providers: List of :class:`JobProvider` instances to fetch
                jobs from.  If omitted, no jobs are collected.
            notifier: Optional :class:`Notifier` for Telegram summaries.
        """
        self._pipeline = pipeline
        self._resume = resume
        self._providers = providers or []
        self._notifier = notifier

        self._scheduler: object | None = None
        self._initialised: bool = False

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        """Start the APScheduler background scheduler.

        The first cycle runs immediately; subsequent cycles run every
        30 minutes.  The call returns immediately (non-blocking).
        """
        try:
            from apscheduler.schedulers.background import BackgroundScheduler  # noqa: PLC0415
        except ImportError:
            logger.error(
                "APScheduler is not installed. "
                "Install it with: pip install apscheduler"
            )
            return

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            self._run_cycle,
            "interval",
            minutes=30,
            id="headhunter_cycle",
            name="Headhunter collection & processing cycle",
            next_run_time=None,  # run immediately, then every 30 min
        )

        scheduler.start()
        self._scheduler = scheduler
        self._initialised = True

        # Register automatic shutdown on interpreter exit.
        atexit.register(self.shut_down, wait=True)

        next_run = scheduler.get_job("headhunter_cycle").next_run_time
        logger.info(
            "Scheduler started — cycle every 30 minutes",
            extra={"next_run": str(next_run)},
        )

    def shut_down(self, wait: bool = True) -> None:
        """Gracefully shut down the scheduler.

        Args:
            wait: When ``True`` (default), wait for any currently
                running job to finish before returning.
        """
        if not self._initialised or self._scheduler is None:
            return
        self._scheduler.shutdown(wait=wait)
        self._initialised = False
        logger.info("Scheduler shut down gracefully")

    # ── Cycle execution ──────────────────────────────────────

    def _run_cycle(self) -> CycleResult:
        """Execute one full collection → filter → pipeline → notify cycle.

        Returns:
            A :class:`CycleResult` with aggregate statistics.
        """
        cycle_id = datetime.now(timezone.utc).isoformat()
        logger.info("=== Scheduler cycle started ===", extra={"cycle_id": cycle_id})

        result = CycleResult(cycle_id=cycle_id)

        # ── 1. Collect & normalise jobs from all providers ──
        all_jobs: list[Job] = []
        for provider in self._providers:
            try:
                raw_jobs = provider.fetch_jobs()
                result.total_collected += len(raw_jobs)

                for raw in raw_jobs:
                    try:
                        job = provider.normalize_job(raw)
                        all_jobs.append(job)
                    except Exception:
                        logger.warning(
                            "Failed to normalise job from %s",
                            provider.__class__.__name__,
                        )
            except Exception:
                logger.exception(
                    "Provider %s failed during fetch",
                    provider.__class__.__name__,
                )

        if not all_jobs:
            logger.info("No jobs collected — cycle complete")
            self._update_state(cycle_id, {"skipped": "no_jobs"})
            return result

        # Sort by posted_at descending (newest first).
        all_jobs.sort(key=lambda j: j.created_at, reverse=True)

        logger.info(
            "Jobs collected from providers",
            extra={"total_raw": result.total_collected, "normalised": len(all_jobs)},
        )

        # ── 2. Deduplicate against Firestore ─────────────────
        seen_urls: set[str] = set()
        seen_company_title: set[tuple[str, str]] = set()
        repository = self._pipeline._repository  # noqa: SLF001

        new_jobs: list[Job] = []
        for job in all_jobs:
            # In-memory dedup (same cycle, multiple providers).
            if job.url in seen_urls:
                continue
            seen_urls.add(job.url)

            # Company + title dedup (same role posted by same company
            # across different URLs / providers).
            ct_key = (job.company.lower().strip(), job.title.lower().strip())
            if ct_key in seen_company_title:
                continue
            seen_company_title.add(ct_key)

            # Firestore dedup.
            try:
                if repository.job_exists(job.url):
                    continue
            except Exception:
                logger.warning(
                    "Error checking job existence for %s — skipping", job.url,
                )
                continue

            # Save to Firestore.
            try:
                repository.save_job(job)
                new_jobs.append(job)
            except Exception:
                logger.warning("Failed to save job %s — skipping", job.url)

        if not new_jobs:
            logger.info("No new jobs after deduplication — cycle complete")
            self._update_state(cycle_id, {"skipped": "all_duplicates"})
            return result

        logger.info(
            "New jobs after dedup",
            extra={"new": len(new_jobs), "duplicates": len(all_jobs) - len(new_jobs)},
        )

        # ── 3. Build set of already-processed job IDs ───────
        processed_job_ids = self._build_processed_ids()

        # ── 4. Filter & pipeline each job ───────────────────
        for job in new_jobs:
            # Filter by location / type.
            if not self._passes_filter(job):
                continue
            result.filtered_in += 1

            # Skip if already has an application.
            if job.id in processed_job_ids:
                result.already_processed += 1
                continue

            # Run the pipeline.
            result.processed += 1
            try:
                pipeline_result = self._pipeline.process_job(job, self._resume)
            except Exception:
                logger.exception("Pipeline raised unhandled error for job %s", job.id)
                result.errors += 1
                continue

            result.pipeline_results.append(asdict(pipeline_result))

            if pipeline_result.status == "COMPLETED":
                result.completed += 1
            elif pipeline_result.status == "REJECTED":
                result.rejected += 1
            else:
                result.errors += 1

            logger.info(
                "Pipeline result",
                extra={
                    "job_id": job.id,
                    "company": job.company,
                    "status": pipeline_result.status,
                    "match_score": pipeline_result.match_score,
                    "ats_score": pipeline_result.ats_score,
                },
            )

        # ── 5. Send Telegram summary ────────────────────────
        if self._notifier is not None:
            try:
                self._notifier.send_collection_summary(
                    total_found=result.total_collected,
                    new_jobs=result.processed,
                    duplicates=result.already_processed,
                )
            except Exception:
                logger.exception("Failed to send cycle summary via Telegram")

        # ── 6. Persist results ──────────────────────────────
        self._update_state(cycle_id, asdict(result))

        logger.info(
            "=== Scheduler cycle complete ===",
            extra={
                "cycle_id": cycle_id,
                "total_collected": result.total_collected,
                "filtered_in": result.filtered_in,
                "already_processed": result.already_processed,
                "processed": result.processed,
                "completed": result.completed,
                "rejected": result.rejected,
                "errors": result.errors,
            },
        )

        return result

    # ── Job filtering ────────────────────────────────────────

    @staticmethod
    def _passes_filter(job: Job) -> bool:
        """Check whether a job matches at least one filter criterion.

        A job passes if its *location* matches any of the registered
        location filters **or** its *title* or *description* contains
        any of the employment-type keywords.
        """
        location_lower = job.location.lower()
        title_lower = job.title.lower()
        desc_lower = job.description.lower()

        for loc_filter in _LOCATION_FILTERS:
            if loc_filter in location_lower:
                return True

        for type_filter in _EMPLOYMENT_TYPE_FILTERS:
            if type_filter in title_lower or type_filter in desc_lower:
                return True

        return False

    # ── Duplicate prevention ─────────────────────────────────

    def _build_processed_ids(self) -> set[str]:
        """Build a set of ``job_id`` values already turned into applications.

        Reads the most recent applications from Firestore via the
        pipeline's repository.
        """
        try:
            apps = self._pipeline._repository.list_recent_applications(limit=200)  # noqa: SLF001
            return {a.job_id for a in apps}
        except Exception:
            logger.exception("Failed to load processed job IDs")
            return set()

    # ── State persistence ────────────────────────────────────

    def _update_state(self, cycle_id: str, data: dict) -> None:
        """Persist the last-execution timestamp and cycle data to a JSON file."""
        state = {
            "last_execution": datetime.now(timezone.utc).isoformat(),
            "last_cycle_id": cycle_id,
            "last_cycle_data": data,
        }
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(
                json.dumps(state, indent=2, default=str),
                encoding="utf-8",
            )
            logger.info(
                "Scheduler state updated",
                extra={"file": str(_STATE_FILE), "cycle_id": cycle_id},
            )
        except OSError:
            logger.exception("Failed to write scheduler state file")

    @staticmethod
    def get_last_execution() -> Optional[dict]:
        """Return the last execution state from the state file.

        Returns:
            A dict with ``last_execution``, ``last_cycle_id``, and
            ``last_cycle_data`` keys, or ``None`` if no state file
            exists.
        """
        if not _STATE_FILE.exists():
            return None
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to read scheduler state file")
            return None
