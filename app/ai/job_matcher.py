"""Job matching service powered by an LLM."""

from __future__ import annotations

import logging

from app.ai.models import JobMatch
from app.ai.opencode_client import OpenCodeClient
from app.ai.prompts import JOB_MATCH_PROMPT
from app.models.job import Job
from app.resume.models import ResumeProfile

logger = logging.getLogger("headhunter")

_DEFAULT_TEMPERATURE: float = 0.2
_DEFAULT_MAX_TOKENS: int = 2000


class JobMatcher:
    """Uses an LLM to evaluate how well a job posting matches a resume.

    The matcher sends a structured prompt containing the job description,
    the candidate's resume profile, and ATS keyword analysis to the
    configured LLM, then parses the model's JSON response into a
    :class:`JobMatch`.
    """

    def __init__(
        self,
        client: OpenCodeClient,
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        """Initialise the matcher.

        Args:
            client: An :class:`OpenCodeClient` instance.
            temperature: Sampling temperature (default 0.2).
            max_tokens: Maximum tokens in the response (default 2000).
        """
        self._client = client
        self._temperature = temperature
        self._max_tokens = max_tokens

    # ── Public API ───────────────────────────────────────────

    def score_job(
        self,
        job: Job,
        resume_profile: ResumeProfile,
        ats_matched: list[str] | None = None,
        ats_missing: list[str] | None = None,
    ) -> JobMatch:
        """Score a single job posting against the given resume profile.

        Args:
            job: The :class:`Job` to evaluate.
            resume_profile: The candidate's :class:`ResumeProfile`.
            ats_matched: Keywords from ATS analysis that matched.
            ats_missing: Keywords from ATS analysis that were missing.

        Returns:
            A :class:`JobMatch` with score, reasoning, and lists of
            matched/missing skills and recommended projects.
        """
        projects_lines = "\n".join(
            f"- {p.name} ({', '.join(p.technologies) if p.technologies else 'no tech listed'})"
            for p in resume_profile.projects
        )

        prompt = JOB_MATCH_PROMPT.format(
            title=job.title,
            company=job.company,
            location=job.location,
            description=job.description,
            resume_summary=resume_profile.summary,
            skills=", ".join(resume_profile.skills),
            experience="\n".join(
                f"- {exp}" for exp in resume_profile.experience
            ),
            projects=projects_lines or "No projects listed",
            education="\n".join(
                f"- {edu}" for edu in resume_profile.education
            ),
            certifications=", ".join(resume_profile.certifications),
            ats_matched=", ".join(ats_matched) if ats_matched else "none",
            ats_missing=", ".join(ats_missing) if ats_missing else "none",
        )

        try:
            parsed = self._client.chat_json(
                prompt,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception:
            logger.exception(
                "LLM call failed for job %s (%s at %s)",
                job.id, job.title, job.company,
            )
            raise RuntimeError(
                f"Failed to get LLM response for job {job.id}"
            ) from None

        score = max(0.0, min(1.0, float(parsed.get("score", 0.0))))

        job_match = JobMatch(
            job_id=job.id,
            score=score,
            matched_skills=parsed.get("matched_skills", []),
            missing_skills=parsed.get("missing_skills", []),
            recommended_projects=parsed.get("recommended_projects", []),
            reasoning=parsed.get("reasoning", ""),
        )

        logger.info(
            "Job scored by LLM",
            extra={
                "job_id": job.id,
                "score": job_match.score,
                "matched": len(job_match.matched_skills),
                "missing": len(job_match.missing_skills),
            },
        )

        return job_match
