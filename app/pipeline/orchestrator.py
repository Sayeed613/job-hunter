"""Async application pipeline — orchestrates the end-to-end job processing workflow.

Flow (per spec Section 10):
1. Collect jobs from all providers concurrently
2. Deduplicate + filter by criteria
3. Remove already-applied (Firestore check)
4. For each job: extract keywords → score match → tailor resume → generate DOCX+PDF cover letter → apply via browser → save to Firestore → notify Telegram
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from app.ai.client import AIClient
from app.ai.cover_letter_gen import CoverLetterGenerator as AICoverLetterGen
from app.ai.keyword_extractor import KeywordExtractor
from app.ai.resume_tailor import ResumeTailor as AIResumeTailor
from app.browser.router import ApplicationRouter
from app.config.settings import Settings
from app.database.firestore_repository import FirestoreRepository
from app.models.application import Application
from app.models.job import Job
from app.resume.models import ResumeProfile
from app.tailor.resume_generator import ResumeGenerator
from app.telegram.notifier import TelegramNotifier

logger = logging.getLogger("job_automation_bot")


class Pipeline:
    """Main application pipeline — search, tailor, apply, notify."""

    def __init__(
        self,
        ai_client: AIClient,
        repository: FirestoreRepository,
        notifier: TelegramNotifier,
        settings: Settings,
    ) -> None:
        self._ai = ai_client
        self._repository = repository
        self._notifier = notifier
        self._settings = settings

        self._keyword_extractor = KeywordExtractor(client=ai_client)
        self._resume_tailor = AIResumeTailor(client=ai_client)
        self._cover_letter_gen = AICoverLetterGen(client=ai_client)
        self._resume_generator = ResumeGenerator()
        self._router = ApplicationRouter()
        self._output_dir = Path("output")

    async def run_cycle(
        self,
        resume: ResumeProfile,
        providers: list,
    ) -> dict:
        """Run one full application cycle."""
        await self._notifier.cycle_started(len(providers))

        tasks = [provider.fetch_jobs() for provider in providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_jobs: list[Job] = []
        for r in results:
            if isinstance(r, list):
                all_jobs.extend(r)

        logger.info("Collected %d total jobs from %d providers", len(all_jobs), len(providers))

        if not all_jobs:
            logger.info("No jobs found this cycle")
            return {"found": 0, "applied": 0, "failed": 0, "skipped": 0}

        jobs = self._deduplicate(all_jobs)
        jobs = self._filter_jobs(jobs)

        new_jobs: list[Job] = []
        for job in jobs:
            existing = self._repository.get_application(job.job_id)
            if not existing:
                new_jobs.append(job)

        await self._notifier.jobs_found(len(new_jobs), len(all_jobs))

        if not new_jobs:
            logger.info("No new jobs to apply to")
            return {"found": len(all_jobs), "applied": 0, "failed": 0, "skipped": len(all_jobs)}

        # Initialise browser once for this cycle
        await self._router.ensure_browser(
            headless=self._settings.headless,
            linkedin_email=self._settings.linkedin_email,
            linkedin_password=self._settings.linkedin_password,
        )

        applied = 0
        failed = 0
        skipped = 0
        max_apply = min(self._settings.max_applications_per_cycle, len(new_jobs))

        for i, job in enumerate(new_jobs[:max_apply]):
            try:
                await self._notifier.job_processing(
                    i + 1, max_apply, job.title, job.company,
                    job.location, job.remote_type, job.salary or "",
                    job.apply_url,
                )

                # Extract keywords
                keywords = await self._keyword_extractor.extract(job.description)
                jd_keyword_count = len(keywords.get("hard_skills", []))
                matched_count = sum(
                    1 for s in keywords.get("hard_skills", [])
                    if s.lower() in " ".join(resume.skills).lower()
                )

                await self._notifier.tailoring(matched_count, jd_keyword_count)

                # Quick match score
                resume_text = f"{' '.join(resume.skills)}\n{resume.summary}"
                match_score = self._quick_score(resume_text, job.description)

                if match_score < 0.25:
                    logger.info("Low match score %.2f, skipping %s", match_score, job.title)
                    skipped += 1
                    continue

                # Tailor resume via GPT
                base_resume_text = self._resume_to_text(resume)
                tailored_text = await self._resume_tailor.tailor(
                    base_resume_text, job, keywords.get("hard_skills", []),
                )

                # Build a ResumeProfile from the tailored text
                tailored_profile = ResumeProfile(
                    name=resume.name,
                    email=resume.email,
                    phone=resume.phone,
                    location=resume.location,
                    summary=resume.summary,
                    skills=resume.skills,
                    experience=resume.experience,
                    projects=resume.projects,
                    education=resume.education,
                    certifications=resume.certifications,
                )

                # Generate DOCX + PDF resume
                job_dir = self._output_dir / _safe_name(job.company)
                job_dir.mkdir(parents=True, exist_ok=True)
                resume_docx = self._resume_generator.generate_docx(
                    tailored_profile, job_dir / f"resume_{job.job_id}.docx",
                )
                resume_pdf = None
                try:
                    resume_pdf = self._resume_generator.generate_pdf(
                        tailored_profile, job_dir / f"resume_{job.job_id}.pdf",
                    )
                except RuntimeError:
                    logger.info("PDF resume not available (fpdf2 not installed)")
                resume_path = str(resume_docx)

                # Generate cover letter
                cover_letter_text = await self._cover_letter_gen.generate(
                    tailored_text[:300],
                    [f"- {p.description}" for p in resume.projects[:5]],
                    job,
                    keywords.get("hard_skills", []),
                )
                cover_path = job_dir / f"cover_letter_{job.job_id}.docx"
                self._write_docx(cover_letter_text, cover_path, resume, job.company)
                cover_letter_path = str(cover_path)

                # Apply via browser
                await self._notifier.applying("browser")
                success = await self._router.apply(
                    job, resume_path, cover_letter_text, cover_letter_path,
                )

                # Save to Firestore
                app = Application(
                    job_id=job.job_id,
                    title=job.title,
                    company=job.company,
                    location=job.location,
                    remote_type=job.remote_type,
                    job_type=job.job_type,
                    salary=job.salary,
                    source=job.source,
                    apply_url=job.apply_url,
                    posted_at=job.posted_at,
                    applied_at=datetime.now(timezone.utc),
                    status="applied" if success else "failed",
                    application_method="browser",
                    resume_path=resume_path,
                    cover_letter_path=cover_letter_path,
                    matched_keywords=keywords.get("hard_skills", []),
                    match_score=match_score,
                )
                self._repository.save_application(app)

                if success:
                    await self._notifier.success(job.title, job.company)
                    applied += 1
                else:
                    await self._notifier.failure(job.title, job.company,
                                                  "Form submission failed", job.apply_url)
                    failed += 1

                await asyncio.sleep(random.uniform(20, 45))

            except Exception as e:
                logger.exception("Failed to process job %s", job.job_id)
                await self._notifier.failure(job.title, job.company, str(e), job.apply_url)
                failed += 1
                continue

        await self._router.close_browser()

        next_run = f"{self._settings.run_interval_hours} hours"
        await self._notifier.cycle_summary(applied, failed, skipped, next_run)
        logger.info("Cycle complete: %d applied, %d failed, %d skipped", applied, failed, skipped)

        return {"found": len(all_jobs), "applied": applied, "failed": failed, "skipped": skipped + (len(new_jobs) - max_apply)}

    @staticmethod
    def _deduplicate(jobs: list[Job]) -> list[Job]:
        seen_urls: set[str] = set()
        seen_ct: set[str] = set()
        unique: list[Job] = []
        for job in jobs:
            url_key = job.apply_url.lower().strip()
            if url_key and url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            ct_key = f"{job.company.lower().strip()}:{job.title.lower().strip()}"
            if ct_key in seen_ct:
                continue
            seen_ct.add(ct_key)
            unique.append(job)
        return unique

    def _filter_jobs(self, jobs: list[Job]) -> list[Job]:
        preferred_remote = {"remote", "hybrid"}
        preferred_locations = self._get_locations()
        excluded = self._get_excluded_companies()
        filtered: list[Job] = []
        for job in jobs:
            if job.company.lower().strip() in excluded:
                continue
            if job.remote_type.lower() not in preferred_remote:
                continue
            loc = job.location.lower()
            if not any(p in loc for p in preferred_locations):
                continue
            if job.experience_years is not None:
                if job.experience_years < self._settings.min_experience:
                    continue
                if job.experience_years > self._settings.max_experience:
                    continue
            filtered.append(job)
        return filtered

    def _get_locations(self) -> list[str]:
        raw = self._settings.locations
        return [loc.strip().lower() for loc in raw.split(",") if loc.strip()]

    def _get_excluded_companies(self) -> set[str]:
        raw = self._settings.excluded_companies
        if not raw:
            return set()
        return {c.strip().lower() for c in raw.split(",") if c.strip()}

    @staticmethod
    def _quick_score(resume_text: str, job_description: str) -> float:
        resume_lower = resume_text.lower()
        jd_lower = job_description.lower()
        resume_words = set(w for w in resume_lower.split() if len(w) > 3)
        jd_words = set(w for w in jd_lower.split() if len(w) > 3)
        if not jd_words:
            return 0.5
        overlap = resume_words & jd_words
        return len(overlap) / len(jd_words)

    @staticmethod
    def _resume_to_text(resume: ResumeProfile) -> str:
        parts = [f"Name: {resume.name}"]
        if resume.summary:
            parts.append(f"Summary: {resume.summary}")
        if resume.skills:
            parts.append(f"Skills: {', '.join(resume.skills)}")
        if resume.experience:
            parts.append("Experience:")
            parts.extend(resume.experience)
        if resume.projects:
            parts.append("Projects:")
            for p in resume.projects:
                techs = f" ({', '.join(p.technologies)})" if p.technologies else ""
                parts.append(f"  - {p.name}{techs}: {p.description}")
        if resume.education:
            parts.append("Education:")
            parts.extend(resume.education)
        return "\n".join(parts)

    @staticmethod
    def _write_docx(letter_text: str, output_path: Path, resume: ResumeProfile, company: str) -> None:
        """Write a cover letter to a simple .docx file."""
        from docx import Document
        from docx.shared import Pt
        from datetime import date

        doc = Document()
        for p in [resume.name, resume.email, resume.phone or "", resume.location or ""]:
            if p:
                doc.add_paragraph(p)
        doc.add_paragraph("")
        doc.add_paragraph(date.today().strftime("%B %d, %Y"))
        doc.add_paragraph(f"Dear {company} Hiring Manager,")
        doc.add_paragraph("")
        for para_text in letter_text.split("\n\n"):
            stripped = para_text.strip()
            if stripped:
                doc.add_paragraph(stripped)
        doc.add_paragraph("")
        doc.add_paragraph("Sincerely,")
        doc.add_paragraph(resume.name)
        doc.save(str(output_path))


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in " _.-" else "_" for c in name).strip()
