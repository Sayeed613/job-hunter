"""Application pipeline — orchestrates the end-to-end job processing workflow.

Flow
----
1. Run ATS analysis (keyword scoring).
2. Run AI matching (LLM-based skill/job evaluation).
3. Reject low-score jobs via :class:`RecommendationEngine`.
4. Analyse GitHub projects (if a profile has been loaded).
5. Analyse portfolio projects (if a portfolio has been loaded).
6. Select the best projects from all sources.
7. Generate a tailored resume (DOCX + PDF).
8. Generate a tailored cover letter (DOCX).
9. Persist an application record in Firestore.
10. Send a Telegram notification.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.ai.job_matcher import JobMatcher
from app.ai.models import JobMatch
from app.ai.recommendation_engine import RecommendationEngine
from app.ats.ats_scorer import AtsResult, AtsScorer
from app.cover_letter.generator import CoverLetterGenerator
from app.database.firestore_repository import FirestoreRepository
from app.github.github_service import GithubService
from app.github.models import GithubProject
from app.jobs.applier import JobApplier
from app.jobs.appliers.base import ApplierResult
from app.models.application import Application, ApplicationStatus
from app.models.job import Job
from app.portfolio.models import PortfolioProject
from app.portfolio.portfolio_service import PortfolioService
from app.resume.models import ResumeProfile
from app.tailor.resume_generator import ResumeGenerator
from app.tailor.resume_tailor import ResumeTailor
from app.telegram.notifier import Notifier

logger = logging.getLogger("headhunter")

# ── Default output directory ─────────────────────────────────

_DEFAULT_OUTPUT_DIR = Path("output")


@dataclass
class PipelineResult:
    """Outcome of running the application pipeline for a single job.

    Attributes:
        job_id: Identifier of the processed job.
        company: Company name.
        role: Job title.
        match_score: AI match score (0.0 – 1.0), or ``None`` if the
            job was rejected before scoring.
        ats_score: ATS keyword-match score (0.0 – 1.0), or ``None``
            if the job was rejected before ATS analysis.
        resume_path: Path to the generated tailored resume file, or
            empty string if none was produced.
        cover_letter_path: Path to the generated cover letter file, or
            empty string if none was produced.
        status: Pipeline exit status — one of ``"REJECTED"``,
            ``"COMPLETED"``, ``"AUTO_APPLIED"``, or ``"ERROR"``.
        rejection_reason: Human-readable explanation when the job was
            rejected, or empty string otherwise.
        error_message: Error details when ``status == "ERROR"``, or
            empty string otherwise.
        auto_submit_success: Whether auto-submit succeeded.  ``None``
            if auto-applier was not configured.
        auto_submit_method: Which submission method was used.
        confirmation_url: Provider-side confirmation URL.
    """

    job_id: str = ""
    company: str = ""
    role: str = ""
    match_score: Optional[float] = None
    ats_score: Optional[float] = None
    resume_path: str = ""
    cover_letter_path: str = ""
    status: str = "ERROR"
    rejection_reason: str = ""
    error_message: str = ""
    auto_submit_success: Optional[bool] = None
    auto_submit_method: str = ""
    confirmation_url: str = ""


class ApplicationPipeline:
    """Orchestrates the complete job-application workflow.

    The pipeline wires together every module in Project Headhunter:

    * ATS keyword scoring
    * AI-driven job matching
    * Recommendation engine (apply / reject)
    * GitHub and portfolio project analysis
    * Resume tailoring and file generation (DOCX + PDF)
    * Cover-letter generation (DOCX)
    * Auto-application submission (via Greenhouse/Lever/Ashby API or email)
    * Firestore persistence
    * Telegram notification

    All dependencies are injected via the constructor so the pipeline
    is fully testable and works with or without optional services
    (GitHub, portfolio, Telegram, applier).
    """

    def __init__(
        self,
        ats_scorer: AtsScorer,
        job_matcher: JobMatcher,
        recommendation_engine: RecommendationEngine,
        resume_tailor: ResumeTailor,
        resume_generator: ResumeGenerator,
        cover_letter_generator: CoverLetterGenerator,
        repository: FirestoreRepository,
        job_applier: JobApplier | None = None,
        github_service: GithubService | None = None,
        portfolio_service: PortfolioService | None = None,
        notifier: Notifier | None = None,
        output_dir: str | Path = _DEFAULT_OUTPUT_DIR,
    ) -> None:
        """Initialise the pipeline.

        Args:
            ats_scorer: ATS keyword-scoring engine.
            job_matcher: LLM-based job-matching service.
            recommendation_engine: Apply/reject decision engine.
            resume_tailor: Resume-tailoring service.
            resume_generator: Resume DOCX/PDF output generator.
            cover_letter_generator: Cover-letter generator.
            repository: Firestore repository for persisting
                applications.
            job_applier: Optional :class:`JobApplier` for automatic
                submission.  When ``None``, auto-apply is skipped.
            github_service: Optional GitHub service (skipped when
                ``None`` or when no profile has been loaded).
            portfolio_service: Optional portfolio service (skipped
                when ``None`` or when no portfolio has been loaded).
            notifier: Optional Telegram notifier (skipped when
                ``None``).
            output_dir: Base output directory for generated files.
                Per-job subdirectories are created automatically.
        """
        from app.config.settings import Settings as _Settings  # noqa: PLC0415

        self._ats = ats_scorer
        self._matcher = job_matcher
        self._recommender = recommendation_engine
        self._tailor = resume_tailor
        self._resume_gen = resume_generator
        self._cover_gen = cover_letter_generator
        self._repository = repository
        self._applier = job_applier
        self._github = github_service
        self._portfolio = portfolio_service
        self._notifier = notifier
        self._output_dir = Path(output_dir)
        self._settings = _Settings()
        self._auto_apply_enabled = (
            self._applier is not None and self._settings.auto_apply_enabled
        )

    # ── Public API ───────────────────────────────────────────

    def process_job(
        self,
        job: Job,
        resume: ResumeProfile,
    ) -> PipelineResult:
        """Run the full application pipeline for a single job.

        Note that the method accepts a **parsed** resume profile
        directly rather than a path to a file — callers should
        load/parse the resume via :class:`ResumeService` before
        invoking the pipeline.

        Args:
            job: The job posting to process.
            resume: The candidate's parsed :class:`ResumeProfile`.

        Returns:
            A :class:`PipelineResult` summarising every step.
        """
        company = job.company
        role = job.title
        job_id = job.id
        job_url = job.url

        # ── 1. ATS analysis ────────────────────────────────
        try:
            ats_result = self._ats.score_job_description(
                job.description, resume,
            )
            logger.info(
                "ATS analysis complete",
                extra={
                    "job_id": job_id,
                    "ats_score": ats_result.total_score,
                    "matched_keywords": len(ats_result.matched_keywords),
                    "missing_keywords": len(ats_result.missing_keywords),
                },
            )
        except Exception:
            logger.exception("ATS analysis failed for job %s", job_id)
            return PipelineResult(
                job_id=job_id, company=company, role=role,
                status="ERROR",
                error_message="ATS analysis failed.",
            )

        ats_matched = ats_result.matched_keywords
        ats_missing = ats_result.missing_keywords

        # ── 2. AI matching ─────────────────────────────────
        try:
            job_match = self._matcher.score_job(
                job, resume,
                ats_matched=ats_matched,
                ats_missing=ats_missing,
            )
            logger.info(
                "AI matching complete",
                extra={
                    "job_id": job_id,
                    "score": job_match.score,
                    "matched_skills": len(job_match.matched_skills),
                    "missing_skills": len(job_match.missing_skills),
                },
            )
        except Exception:
            logger.exception("AI matching failed for job %s", job_id)
            return PipelineResult(
                job_id=job_id, company=company, role=role,
                ats_score=ats_result.total_score,
                status="ERROR",
                error_message="AI matching failed.",
            )

        # ── 3. Recommendation / rejection ──────────────────
        recommendation = self._recommender.recommend(job_match)
        if not recommendation.apply:
            logger.info(
                "Job rejected by recommendation engine",
                extra={
                    "job_id": job_id,
                    "priority": recommendation.priority,
                    "explanation": recommendation.explanation,
                },
            )
            return PipelineResult(
                job_id=job_id, company=company, role=role,
                match_score=job_match.score,
                ats_score=ats_result.total_score,
                status="REJECTED",
                rejection_reason=recommendation.explanation,
            )

        logger.info(
            "Job accepted — proceeding with full pipeline",
            extra={
                "job_id": job_id,
                "priority": recommendation.priority,
                "score": job_match.score,
            },
        )

        # ── 4. Analyse GitHub projects ─────────────────────
        github_project_names: list[str] = []
        if self._github is not None:
            try:
                gh_projects: list[GithubProject] = (
                    self._github.get_best_projects_for_job(job_match, top_n=3)
                )
                github_project_names = [p.repo_name for p in gh_projects]
                logger.info(
                    "GitHub projects analysed",
                    extra={
                        "job_id": job_id,
                        "projects": len(github_project_names),
                    },
                )
            except RuntimeError:
                logger.info(
                    "GitHub profile not loaded — skipping GitHub analysis",
                )
            except Exception:
                logger.exception(
                    "GitHub analysis error for job %s", job_id,
                )

        # ── 5. Analyse portfolio projects ──────────────────
        portfolio_project_names: list[str] = []
        if self._portfolio is not None:
            try:
                pf_projects: list[PortfolioProject] = (
                    self._portfolio.get_best_projects_for_job(
                        job_match, top_n=3,
                    )
                )
                portfolio_project_names = [p.name for p in pf_projects]
                logger.info(
                    "Portfolio projects analysed",
                    extra={
                        "job_id": job_id,
                        "projects": len(portfolio_project_names),
                    },
                )
            except RuntimeError:
                logger.info(
                    "Portfolio not loaded — skipping portfolio analysis",
                )
            except Exception:
                logger.exception(
                    "Portfolio analysis error for job %s", job_id,
                )

        # ── 6. Select best projects ────────────────────────
        selected_project_names = self._select_projects(
            job_match,
            github_project_names,
            portfolio_project_names,
        )

        # ── 7. Generate tailored resume ────────────────────
        try:
            tailored = self._tailor.optimize_resume(
                resume, job_match, ats_result,
            )

            job_dir = self._output_dir / _sanitise_dirname(company)
            job_dir.mkdir(parents=True, exist_ok=True)

            resume_docx = self._resume_gen.generate_docx(
                tailored, job_dir / f"resume_{job_id}.docx",
            )
            resume_pdf: Optional[Path] = None
            try:
                resume_pdf = self._resume_gen.generate_pdf(
                    tailored, job_dir / f"resume_{job_id}.pdf",
                )
            except RuntimeError:
                logger.info("PDF resume not generated (fpdf2 not available)")

            resume_path = str(resume_docx)
            logger.info(
                "Tailored resume generated",
                extra={
                    "job_id": job_id,
                    "docx": resume_path,
                    "pdf": str(resume_pdf) if resume_pdf else None,
                },
            )
        except Exception:
            logger.exception("Resume generation failed for job %s", job_id)
            return PipelineResult(
                job_id=job_id, company=company, role=role,
                match_score=job_match.score,
                ats_score=ats_result.total_score,
                status="ERROR",
                error_message="Resume generation failed.",
            )

        # ── 8. Generate cover letter ───────────────────────
        cover_letter_path: str = ""
        try:
            cl_path = self._cover_gen.generate_to_docx(
                job=job,
                resume=tailored,
                output_path=job_dir / f"cover_letter_{job_id}.docx",
                selected_projects=selected_project_names,
            )
            cover_letter_path = str(cl_path)
            logger.info(
                "Cover letter generated",
                extra={"job_id": job_id, "path": cover_letter_path},
            )
        except Exception:
            logger.exception(
                "Cover letter generation failed for job %s — continuing",
                job_id,
            )

        # ── 9. Auto-apply (optional) ────────────────────────
        auto_submit_success: Optional[bool] = None
        auto_submit_method: str = ""
        confirmation_url: str = ""
        applier_result: ApplierResult | None = None

        if self._auto_apply_enabled:
            try:
                applier_result = self._applier.submit(
                    candidate_name=resume.name,
                    candidate_email=resume.email,
                    candidate_phone=resume.phone,
                    resume_path=resume_path,
                    cover_letter_path=cover_letter_path,
                    job_title=role,
                    job_apply_url=job_url,
                    job_description=job.description,
                    job_source=job.source,
                    extra={"company": company},
                )

                auto_submit_success = applier_result.success
                auto_submit_method = applier_result.application_method.value
                confirmation_url = applier_result.confirmation_url

                if applier_result.success:
                    logger.info(
                        "Auto-apply successful",
                        extra={
                            "job_id": job_id,
                            "method": auto_submit_method,
                            "app_id": applier_result.application_id,
                        },
                    )
                else:
                    logger.warning(
                        "Auto-apply failed",
                        extra={
                            "job_id": job_id,
                            "method": auto_submit_method,
                            "error": applier_result.error_message,
                        },
                    )
            except Exception as exc:
                logger.exception(
                    "Auto-apply raised unhandled error for job %s — continuing",
                    job_id,
                )
                auto_submit_success = False
                auto_submit_error = str(exc)
        else:
            logger.info(
                "Auto-apply skipped (applier not configured or disabled)",
                extra={"job_id": job_id},
            )

        # ── 10. Store application record ────────────────────
        try:
            app_id = hashlib.sha256(
                f"{job_id}:{datetime.now(timezone.utc).isoformat()}".encode(),
            ).hexdigest()[:16]

            application = Application(
                id=app_id,
                job_id=job_id,
                company=company,
                role=role,
                resume_version=resume_path,
                cover_letter_version=cover_letter_path,
                match_score=job_match.score,
                status=ApplicationStatus.APPLIED,
                applied_at=datetime.now(timezone.utc),
                job_url=job_url,
                application_method=auto_submit_method,
                auto_submit_success=auto_submit_success,
                auto_submit_error=(
                    applier_result.error_message
                    if auto_submit_success is False and applier_result is not None
                    else ""
                ),
                confirmation_url=confirmation_url,
            )
            self._repository.save_application(application)
            logger.info(
                "Application record saved",
                extra={"app_id": app_id, "job_id": job_id},
            )
        except Exception:
            logger.exception(
                "Failed to persist application for job %s — continuing",
                job_id,
            )

        # ── 11. Send Telegram notification ─────────────────
        if self._notifier is not None:
            try:
                status_text = "APPLIED" + (
                    f" (auto: {'SUCCESS' if auto_submit_success else 'FAILED'})"
                    if auto_submit_success is not None
                    else ""
                )
                self._notifier.send_application_update(
                    company=company,
                    role=role,
                    status=status_text,
                    match_score=job_match.score,
                    job_url=job_url,
                )
            except Exception:
                logger.exception(
                    "Telegram notification failed for job %s — continuing",
                    job_id,
                )

        overall_status = "COMPLETED"
        if auto_submit_success is True:
            overall_status = "AUTO_APPLIED"
        elif auto_submit_success is False:
            overall_status = "AUTO_APPLY_FAILED"

        return PipelineResult(
            job_id=job_id,
            company=company,
            role=role,
            match_score=job_match.score,
            ats_score=ats_result.total_score,
            resume_path=resume_path,
            cover_letter_path=cover_letter_path,
            status=overall_status,
            auto_submit_success=auto_submit_success,
            auto_submit_method=auto_submit_method,
            confirmation_url=confirmation_url,
        )

    # ── Project selection ────────────────────────────────────

    @staticmethod
    def _select_projects(
        job_match: JobMatch,
        github_names: list[str],
        portfolio_names: list[str],
        max_projects: int = 5,
    ) -> list[str]:
        """Merge and deduplicate project recommendations.

        Priority order:
        1. AI-recommended resume projects (from :class:`JobMatch`).
        2. GitHub projects (from :class:`GithubService`).
        3. Portfolio projects (from :class:`PortfolioService`).

        The total is capped at ``max_projects``.

        Args:
            job_match: The AI match result with recommended projects.
            github_names: Project names from GitHub analysis.
            portfolio_names: Project names from portfolio analysis.
            max_projects: Maximum number of project names to return.

        Returns:
            A deduplicated list of project names.
        """
        seen: set[str] = set()
        ordered: list[str] = []

        for name in job_match.recommended_projects:
            lower = name.lower()
            if lower not in seen:
                ordered.append(name)
                seen.add(lower)

        for name in github_names:
            lower = name.lower()
            if lower not in seen:
                ordered.append(name)
                seen.add(lower)

        for name in portfolio_names:
            lower = name.lower()
            if lower not in seen:
                ordered.append(name)
                seen.add(lower)

        return ordered[:max_projects]


# ── Helpers ──────────────────────────────────────────────────


def _sanitise_dirname(name: str) -> str:
    """Replace characters that are invalid in directory names."""
    return "".join(c if c.isalnum() or c in " _.-" else "_" for c in name).strip()
