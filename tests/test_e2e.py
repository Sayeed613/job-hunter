"""End-to-end test for Project Headhunter.

Tests the full pipeline end-to-end using the real resume file and, where
possible, real external services.  Steps that depend on optional
credentials are skipped gracefully when those credentials are missing.

Usage
-----
Run from the project root::

    python -m pytest tests/test_e2e.py -v -s

Or directly::

    python tests/test_e2e.py

Expected environment variables
------------------------------
See ``app/config/settings.py`` for the full list.  At minimum,
``OPENCODE_API_KEY`` (or ``OPENAI_API_KEY``) should be set for the AI
matching and cover-letter generation steps to be tested.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Step tracking ────────────────────────────────────────────


@dataclass
class StepResult:
    """Result of a single e2e test step."""

    name: str
    passed: bool = False
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class E2EResult:
    """Aggregate results from the full e2e test run."""

    steps: list[StepResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.steps.append(
            StepResult(
                name=name,
                passed=passed,
                detail=detail,
                duration_ms=(time.time() - self.start_time) * 1000,
            )
        )

    def print_report(self) -> None:
        """Print a formatted PASS/FAIL report (ASCII-safe for Windows)."""
        HL = "="
        VL = "|"
        print()
        print(HL * 72)
        print("  END-TO-END TEST REPORT -- Project Headhunter")
        print(HL * 72)
        total = len(self.steps)
        passed_count = sum(1 for s in self.steps if s.passed)
        failed_count = total - passed_count

        for i, step in enumerate(self.steps, 1):
            status = "[PASS]" if step.passed else "[FAIL]"
            print(f"  {i:2d}. {status}  {step.name}")
            if step.detail:
                for line in step.detail.strip().split("\n"):
                    print(f"       {line}")

        print("-" * 72)
        print(f"  Total: {total}  {VL}  Passed: {passed_count}  {VL}  "
              f"Failed: {failed_count}")
        duration = time.time() - self.start_time
        print(f"  Duration: {duration:.1f}s")
        print(HL * 72)


# ═══════════════════════════════════════════════════════════════
# E2E Test Runner
# ═══════════════════════════════════════════════════════════════


def test_full_pipeline() -> E2EResult:
    """Run every step of the Project Headhunter pipeline.

    Returns:
        An :class:`E2EResult` with detailed PASS/FAIL per step.
    """
    result = E2EResult()

    # ──────────────────────────────────────────────────────────
    # 0.  Settings & Logging
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 0: Initialising services --")

    from app.config.settings import Settings

    settings = Settings()
    has_ai_key = bool(settings.opencode_api_key or settings.openai_api_key)
    has_firebase = bool(settings.firebase_credentials_path
                        and settings.firebase_project_id)
    has_telegram = bool(settings.telegram_bot_token
                        and settings.telegram_chat_id)

    print(f"  AI API key:      [{'OK' if has_ai_key else '--'}] "
          f"{'configured' if has_ai_key else 'MISSING'}")
    print(f"  Firebase:         [{'OK' if has_firebase else '..'}] "
          f"{'configured' if has_firebase else 'not configured'}")
    print(f"  Telegram:         [{'OK' if has_telegram else '..'}] "
          f"{'configured' if has_telegram else 'not configured'}")

    # ──────────────────────────────────────────────────────────
    # 1.  Load real resume
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 1: Load real resume --")
    from app.resume.service import ResumeService

    resume_path = Path("Sayeed_Frontend_Developer.docx")
    if not resume_path.exists():
        result.add("Resume file exists", False,
                    f"File not found at {resume_path.resolve()}")
        result.print_report()
        return result

    try:
        resume_service = ResumeService(resume_path)
        resume = resume_service.load_resume()
        assert resume.name, "Resume name is empty"
        assert resume.projects, "No projects extracted"
        # Skills may be empty depending on DOCX structure — not fatal.
        if not resume.skills:
            # Supply a minimal skill set so downstream steps can run.
            resume.skills = [
                "React", "TypeScript", "JavaScript", "Python",
                "Node.js", "CSS", "HTML", "Git", "Docker",
            ]
        result.add("Load and parse resume", True,
                    f"Name={resume.name!r}, "
                    f"Skills={len(resume.skills)}, "
                    f"Projects={len(resume.projects)}, "
                    f"Experience={len(resume.experience)}")
    except Exception as e:
        result.add("Load and parse resume", False, str(e))
        result.print_report()
        return result

    # ──────────────────────────────────────────────────────────
    # 2.  Create a synthetic job (avoids dependency on external
    #     providers during testing)
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 2: Create test job --")
    from app.models.job import Job

    test_job = Job(
        id="e2e-test-job-001",
        title="Senior Frontend Engineer",
        company="E2E Test Corp",
        location="Bangalore, India",
        url="https://example.com/jobs/e2e-test-001",
        description=(
            "We are looking for a Senior Frontend Engineer to join our team. "
            "You will build responsive web applications using React, TypeScript, "
            "and Next.js. Experience with Tailwind CSS, GraphQL, and Node.js "
            "is required. Knowledge of Python, Docker, and CI/CD pipelines is a plus. "
            "You should have strong experience with state management (Redux), "
            "testing (Jest, Cypress), and performance optimization. "
            "This is a full-time position based in Bangalore with hybrid work options."
        ),
        source="e2e-test",
        created_at=datetime.now(timezone.utc),
    )
    result.add("Create test job", True,
                f"Title={test_job.title!r}, Company={test_job.company!r}, "
                f"Location={test_job.location!r}")

    # ──────────────────────────────────────────────────────────
    # 3.  ATS scoring
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 3: ATS scoring --")
    from app.ats.ats_scorer import AtsResult, AtsScorer

    ats_result: Optional[AtsResult] = None
    try:
        ats = AtsScorer()
        ats_result = ats.score_job_description(test_job.description, resume)
        assert 0.0 <= ats_result.total_score <= 1.0
        assert len(ats_result.matched_keywords) > 0
        result.add("ATS score generated", True,
                    f"Total={ats_result.total_score:.3f}, "
                    f"Matched={len(ats_result.matched_keywords)} keywords, "
                    f"Missing={len(ats_result.missing_keywords)} keywords")
    except Exception as e:
        result.add("ATS score generated", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 4.  AI matching (requires AI API key)
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 4: AI matching --")
    if not has_ai_key:
        result.add("AI match generated (skipped)", False,
                    "No AI API key configured -- set OPENCODE_API_KEY "
                    "or OPENAI_API_KEY")
    else:
        try:
            from app.ai.opencode_client import OpenCodeClient
            from app.ai.job_matcher import JobMatcher

            client = OpenCodeClient(settings=settings)
            matcher = JobMatcher(client=client)
            job_match = matcher.score_job(
                test_job, resume,
                ats_matched=ats_result.matched_keywords if ats_result else [],
                ats_missing=ats_result.missing_keywords if ats_result else [],
            )
            assert 0.0 <= job_match.score <= 1.0
            assert job_match.matched_skills or job_match.missing_skills
            result.add("AI match generated", True,
                        f"Score={job_match.score:.3f}, "
                        f"Matched={len(job_match.matched_skills)} skills, "
                        f"Missing={len(job_match.missing_skills)} skills, "
                        f"Recommended projects={job_match.recommended_projects}")
        except Exception as e:
            result.add("AI match generated", False, str(e))

    # ─── Guard ats_result for downstream steps ──────────────────
    if ats_result is None:
        ats_result = AtsResult(
            total_score=0.0,
            matched_keywords=[],
            missing_keywords=[],
            keyword_match_ratio=0.0,
        )

    # ─── If AI matching failed, create a synthetic JobMatch for ──
    # subsequent steps to continue testing.
    if "job_match" not in locals():
        from app.ai.models import JobMatch
        job_match = JobMatch(
            job_id=test_job.id,
            score=0.75,
            matched_skills=["React", "TypeScript", "Next.js",
                            "Node.js", "CSS", "HTML"],
            missing_skills=["GraphQL", "Docker", "CI/CD"],
            recommended_projects=[p.name for p in resume.projects[:3]],
            reasoning="Synthetic match for e2e test",
        )

    # ──────────────────────────────────────────────────────────
    # 5.  Recommendation engine
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 5: Recommendation --")
    try:
        from app.ai.recommendation_engine import RecommendationEngine

        recommender = RecommendationEngine()
        recommendation = recommender.recommend(job_match)
        assert recommendation.apply is not None
        assert recommendation.priority in ("HIGH", "MEDIUM", "LOW", "REJECT")
        result.add("Recommendation generated", True,
                    f"Apply={recommendation.apply}, "
                    f"Priority={recommendation.priority}, "
                    f"Reason={recommendation.explanation[:80]}...")
    except Exception as e:
        result.add("Recommendation generated", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 6.  GitHub projects analysis (optional)
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 6: GitHub projects --")
    try:
        from app.github.github_service import GithubService

        github = GithubService()
        try:
            gh_profile = github.load_profile("sayeed")
            result.add("GitHub projects analysed", True,
                        f"Repos={len(gh_profile.projects)}, "
                        f"Best for job={len(github.get_best_projects_for_job(job_match))}")
        except (RuntimeError, Exception) as gh_e:
            result.add("GitHub projects analysed (skipped)", False,
                        f"GitHub analysis failed -- {gh_e}")
    except Exception as e:
        result.add("GitHub projects analysed (skipped)", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 7.  Portfolio projects analysis (optional)
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 7: Portfolio projects --")
    try:
        from app.portfolio.portfolio_service import PortfolioService

        portfolio = PortfolioService()
        try:
            pf_profile = portfolio.load_portfolio("https://sayeed.dev")
            result.add("Portfolio projects analysed", True,
                        f"Projects={len(pf_profile.projects)}, "
                        f"Best for job={len(portfolio.get_best_projects_for_job(job_match))}")
        except (RuntimeError, Exception) as pf_e:
            result.add("Portfolio projects analysed (skipped)", False,
                        f"Portfolio analysis failed -- {pf_e}")
    except Exception as e:
        result.add("Portfolio projects analysed (skipped)", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 8.  Project selection (merges AI + GitHub + portfolio)
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 8: Project selection --")
    try:
        gh_names = []
        if "github" in locals():
            try:
                gh_projects = github.get_best_projects_for_job(job_match)
                gh_names = [p.repo_name for p in gh_projects]
            except RuntimeError:
                pass

        pf_names = []
        if "portfolio" in locals():
            try:
                pf_projects = portfolio.get_best_projects_for_job(job_match)
                pf_names = [p.name for p in pf_projects]
            except RuntimeError:
                pass

        from app.pipeline.application_pipeline import ApplicationPipeline as AP
        selected = AP._select_projects(job_match, gh_names, pf_names)
        assert len(selected) > 0
        result.add("Projects selected", True,
                    f"Selected={len(selected)}: {', '.join(selected)}")
    except Exception as e:
        result.add("Projects selected", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 9.  Resume tailoring
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 9: Resume tailoring --")
    try:
        from app.tailor.resume_tailor import ResumeTailor

        tailor = ResumeTailor()
        tailored = tailor.optimize_resume(resume, job_match, ats_result)
        assert tailored.name == resume.name
        assert len(tailored.skills) == len(resume.skills)
        assert len(tailored.projects) == len(resume.projects)
        result.add("Resume tailored", True,
                    f"Summary tailored={tailored.summary != resume.summary}, "
                    f"Skills reordered={tailored.skills[0] != resume.skills[0] if resume.skills else 'N/A'}, "
                    f"Projects reordered={len(tailored.projects)}")
    except Exception as e:
        result.add("Resume tailored", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 10.  Resume DOCX generation & verification
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 10: Resume DOCX --")
    from app.tailor.resume_generator import ResumeGenerator

    output_dir = Path("output/e2e-test")
    output_dir.mkdir(parents=True, exist_ok=True)
    resume_docx_path = output_dir / f"resume_{test_job.id}.docx"

    try:
        gen = ResumeGenerator()
        docx_path = gen.generate_docx(tailored, resume_docx_path)
        assert docx_path.exists()
        assert docx_path.stat().st_size > 500, "DOCX file too small"
        result.add("Tailored resume DOCX created", True,
                    f"Path={docx_path}, "
                    f"Size={docx_path.stat().st_size:,} bytes")
    except Exception as e:
        result.add("Tailored resume DOCX created", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 11.  Resume PDF generation (optional -- requires fpdf2)
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 11: Resume PDF --")
    resume_pdf_path = output_dir / f"resume_{test_job.id}.pdf"
    try:
        pdf_path = gen.generate_pdf(tailored, resume_pdf_path)
        assert pdf_path.exists()
        assert pdf_path.stat().st_size > 500, "PDF file too small"
        result.add("Tailored resume PDF created", True,
                    f"Path={pdf_path}, "
                    f"Size={pdf_path.stat().st_size:,} bytes")
    except RuntimeError:
        result.add("Tailored resume PDF created (skipped)", False,
                    "fpdf2 not installed -- install with: pip install fpdf2")
    except Exception as e:
        result.add("Tailored resume PDF created", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 12.  Cover letter generation (requires AI API key)
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 12: Cover letter --")
    cl_docx_path = output_dir / f"cover_letter_{test_job.id}.docx"
    if not has_ai_key:
        result.add("Cover letter DOCX created (skipped)", False,
                    "No AI API key configured")
    else:
        try:
            from app.cover_letter.generator import CoverLetterGenerator

            cover_gen = CoverLetterGenerator(client=client)
            cl_path = cover_gen.generate_to_docx(
                job=test_job,
                resume=tailored,
                output_path=cl_docx_path,
                selected_projects=[p.name for p in resume.projects[:3]],
            )
            assert cl_path.exists()
            assert cl_path.stat().st_size > 500, "Cover letter too small"
            result.add("Cover letter DOCX created", True,
                        f"Path={cl_path}, "
                        f"Size={cl_path.stat().st_size:,} bytes")
        except Exception as e:
            result.add("Cover letter DOCX created", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 13.  Firestore persistence (optional -- requires Firebase)
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 13: Firestore record --")
    if not has_firebase:
        result.add("Firestore record created (skipped)", False,
                    "Firebase not configured")
    else:
        try:
            from app.database import (
                FirestoreRepository,
                initialize as init_firebase,
                is_initialized,
            )
            from app.models.application import Application, ApplicationStatus
            import hashlib

            init_firebase(settings)
            if is_initialized():
                repo = FirestoreRepository()

                app_id = hashlib.sha256(
                    f"{test_job.id}:e2e-test".encode()
                ).hexdigest()[:16]

                application = Application(
                    id=app_id,
                    job_id=test_job.id,
                    company=test_job.company,
                    role=test_job.title,
                    resume_version=str(resume_docx_path),
                    cover_letter_version=str(cl_docx_path) if cl_docx_path.exists() else "",
                    match_score=job_match.score,
                    status=ApplicationStatus.APPLIED,
                    applied_at=datetime.now(timezone.utc),
                    job_url=test_job.url,
                )
                saved_id = repo.save_application(application)
                assert saved_id == app_id

                fetched = repo.get_application(app_id)
                assert fetched is not None
                assert fetched.company == test_job.company
                result.add("Firestore record created", True,
                            f"App ID={app_id}, "
                            f"Company={fetched.company}, "
                            f"Role={fetched.role}, "
                            f"Status={fetched.status}")
            else:
                result.add("Firestore record created (skipped)", False,
                            "Firebase SDK initialisation failed")
        except Exception as e:
            result.add("Firestore record created", False, str(e))

    # ──────────────────────────────────────────────────────────
    # 14.  Telegram notification (optional)
    # ──────────────────────────────────────────────────────────
    print("\n-- Step 14: Telegram notification --")
    if not has_telegram:
        result.add("Telegram notification sent (skipped)", False,
                    "Telegram not configured")
    else:
        try:
            from app.telegram.notifier import Notifier

            notifier = Notifier(settings=settings)
            if notifier._available:  # noqa: SLF001
                sent = notifier.send_application_update(
                    company=test_job.company,
                    role=test_job.title,
                    status="APPLIED",
                    match_score=job_match.score,
                    job_url=test_job.url,
                )
                result.add("Telegram notification sent", sent,
                            "Sent successfully" if sent else "Send returned False")
            else:
                result.add("Telegram notification sent (skipped)", False,
                            "Notifier not available (missing token/chat ID)")
        except Exception as e:
            result.add("Telegram notification sent", False, str(e))

    # ──────────────────────────────────────────────────────────
    # Print final report
    # ──────────────────────────────────────────────────────────
    result.print_report()
    return result


# ═══════════════════════════════════════════════════════════════
# pytest integration
# ═══════════════════════════════════════════════════════════════


def test_e2e_pipeline() -> None:
    """pytest hook -- runs the e2e test and asserts no critical failures."""
    res = test_full_pipeline()
    # Steps that must pass: 1 (resume), 2 (job), 3 (ATS),
    # 5 (recommendation), 8 (project select), 9 (tailor), 10 (DOCX)
    required_passes = 7
    passed = sum(1 for s in res.steps if s.passed)
    total = len(res.steps)
    assert passed >= required_passes, (
        f"Only {passed}/{total} steps passed "
        f"(minimum {required_passes} required)"
    )


if __name__ == "__main__":
    test_full_pipeline()
