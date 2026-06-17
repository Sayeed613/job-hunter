"""End-to-end tests for the Job Automation Bot (current architecture).

Tests the real pipeline components without submitting live applications.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import Settings
from app.models.job import Job
from app.pipeline.orchestrator import Pipeline
from app.resume.parser import ResumeParser
from app.tailor.resume_generator import ResumeGenerator


RESUME_PATH = Path("Sayeed_Frontend_Developer.docx")


def _make_pipeline() -> Pipeline:
    from app.ai.client import AIClient
    from app.database import FirestoreRepository
    from app.notifier import WhatsAppNotifier

    settings = Settings()
    return Pipeline(
        ai_client=AIClient(settings=settings),
        repository=FirestoreRepository(),
        notifier=WhatsAppNotifier(),
        settings=settings,
    )


@pytest.mark.skipif(not RESUME_PATH.exists(), reason="Resume file not found")
def test_resume_parsing() -> None:
    resume = ResumeParser().parse_docx(str(RESUME_PATH))
    assert resume.name
    assert resume.skills or resume.experience or resume.projects


def test_job_filter_remote_and_bangalore() -> None:
    pipeline = _make_pipeline()
    jobs = [
        Job(
            job_id="remote-1",
            title="Python Developer",
            company="RemoteCo",
            description="python react developer",
            location="Remote",
            remote_type="Remote",
        ),
        Job(
            job_id="blr-1",
            title="Frontend Engineer",
            company="BangaloreCo",
            description="react typescript",
            location="Bangalore, India",
            remote_type="Hybrid",
        ),
        Job(
            job_id="nyc-hybrid",
            title="Software Engineer",
            company="NYC Co",
            description="python developer",
            location="New York, NY",
            remote_type="Hybrid",
        ),
        Job(
            job_id="sf-onsite",
            title="Backend Engineer",
            company="SF Co",
            description="python django",
            location="San Francisco, CA",
            remote_type="Onsite",
        ),
    ]

    filtered, debug = pipeline._filter_jobs(jobs)
    passed_ids = {j.job_id for j in filtered}

    assert "remote-1" in passed_ids
    assert "blr-1" in passed_ids
    assert "nyc-hybrid" not in passed_ids
    assert "sf-onsite" not in passed_ids
    assert debug["hybrid_non_bangalore_leaked"] == 0


@pytest.mark.skipif(not RESUME_PATH.exists(), reason="Resume file not found")
def test_resume_docx_generation() -> None:
    resume = ResumeParser().parse_docx(str(RESUME_PATH))
    output_dir = Path("output/e2e-test")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "test_resume.docx"

    gen = ResumeGenerator()
    docx_path = gen.generate_docx(resume, out_path)

    assert docx_path.exists()
    assert docx_path.stat().st_size > 500


def test_settings_load() -> None:
    settings = Settings()
    assert settings.app_name == "Job Automation Bot"
    assert settings.max_applications_per_cycle > 0
    assert hasattr(settings, "openai_api_key")


def test_job_model_fields() -> None:
    job = Job(
        job_id="test-001",
        title="Engineer",
        company="Test Corp",
        description="A" * 50,
        location="Remote",
        apply_url="https://example.com/apply",
        posted_at=datetime.now(timezone.utc),
    )
    assert job.job_id == "test-001"
    assert job.apply_url.startswith("https://")
