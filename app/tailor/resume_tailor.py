"""Resume tailor — customises resume content for a specific job posting.

**Never invents experience.  Only rewrites / reorders existing information.**
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.ai.models import JobMatch
from app.ats.ats_scorer import AtsResult
from app.resume.models import Project, ResumeProfile

logger = logging.getLogger("headhunter")


class ResumeTailor:
    """Tailors a :class:`ResumeProfile` to a specific job based on ATS and
    AI analysis results.

    Transformations applied (in order):
    1. **Tailor summary** — highlights matched skills and relevant experience.
    2. **Reorder skills** — matched skills first, grouped by relevance.
    3. **Reorder projects** — projects most relevant to the job appear first.
    """

    def tailor_summary(
        self,
        base_summary: str,
        job_match: JobMatch,
        ats_result: Optional[AtsResult] = None,
    ) -> str:
        """Tailor the professional summary to emphasise job-relevant skills.

        Args:
            base_summary: The original summary from the resume.
            job_match: The :class:`JobMatch` from the AI evaluator.
            ats_result: Optional :class:`AtsResult` for extra keyword
                context.

        Returns:
            A rewritten summary that:
            - Retains the original tone and facts.
            - Highlights matched skills inline.
            - Does **not** invent experience.
        """
        if not base_summary:
            return base_summary

        matched = job_match.matched_skills

        # Insert a keyword-rich sentence early if the summary doesn't
        # already mention at least half the matched skills.
        mentioned = sum(
            1 for s in matched if s.lower() in base_summary.lower()
        )
        if matched and mentioned < len(matched) * 0.5:
            skills_phrase = ", ".join(matched[:6])
            insertion = (
                f" Skilled in {skills_phrase}"
                f"{' and more' if len(matched) > 6 else ''}."
            )
            # Insert after the first sentence.
            first_period = base_summary.find(".")
            if first_period != -1:
                tailored = base_summary[: first_period + 1] + insertion + base_summary[first_period + 1 :]
            else:
                tailored = base_summary + insertion
        else:
            tailored = base_summary

        logger.info(
            "Summary tailored",
            extra={
                "original_length": len(base_summary),
                "tailored_length": len(tailored),
                "skills_highlighted": len(matched),
            },
        )

        return tailored

    def reorder_skills(
        self,
        skills: list[str],
        job_match: JobMatch,
    ) -> list[str]:
        """Reorder skills so that matched skills appear first.

        Args:
            skills: The original skill list.
            job_match: The :class:`JobMatch` providing matched/missing
                skills.

        Returns:
            A new list with matched skills at the front (preserving
            their relative order from the JD), followed by the remaining
            skills in their original order.
        """
        matched_lower = {s.lower() for s in job_match.matched_skills}

        matched_skills = [s for s in skills if s.lower() in matched_lower]
        other_skills = [s for s in skills if s.lower() not in matched_lower]

        reordered = matched_skills + other_skills

        logger.info(
            "Skills reordered",
            extra={
                "total": len(skills),
                "matched_first": len(matched_skills),
            },
        )

        return reordered

    def reorder_projects(
        self,
        projects: list[Project],
        job_match: JobMatch,
    ) -> list[Project]:
        """Reorder projects so that recommended projects appear first.

        Recommended projects (from :attr:`JobMatch.recommended_projects`)
        are moved to the front in the order they appear in the match,
        followed by the remaining projects in their original order.

        Args:
            projects: The original project list.
            job_match: The :class:`JobMatch` with recommended project
                names.

        Returns:
            A reordered list of :class:`Project` instances.
        """
        recommended_names = [
            name.lower() for name in job_match.recommended_projects
        ]
        name_order = {
            name: i for i, name in enumerate(recommended_names)
        }

        recommended = [
            p for p in projects if p.name.lower() in name_order
        ]
        # Preserve the match's ordering for recommended projects.
        recommended.sort(key=lambda p: name_order.get(p.name.lower(), 999))

        other = [p for p in projects if p.name.lower() not in name_order]

        reordered = recommended + other

        logger.info(
            "Projects reordered",
            extra={
                "total": len(projects),
                "recommended_first": len(recommended),
            },
        )

        return reordered

    def optimize_resume(
        self,
        profile: ResumeProfile,
        job_match: JobMatch,
        ats_result: Optional[AtsResult] = None,
    ) -> ResumeProfile:
        """Run all tailoring transformations on a resume profile.

        This is a convenience method that applies:
        - :meth:`tailor_summary`
        - :meth:`reorder_skills`
        - :meth:`reorder_projects`

        The returned :class:`ResumeProfile` is a new instance; the
        original is not mutated.

        Args:
            profile: The original :class:`ResumeProfile`.
            job_match: A :class:`JobMatch` from the matching pipeline.
            ats_result: Optional :class:`AtsResult` for summary tailoring.

        Returns:
            A new :class:`ResumeProfile` with tailored content.
        """
        tailored = ResumeProfile(
            name=profile.name,
            email=profile.email,
            phone=profile.phone,
            location=profile.location,
            summary=self.tailor_summary(profile.summary, job_match, ats_result),
            skills=self.reorder_skills(profile.skills, job_match),
            projects=self.reorder_projects(profile.projects, job_match),
            experience=list(profile.experience),  # unchanged
            education=list(profile.education),    # unchanged
            certifications=list(profile.certifications),  # unchanged
        )

        logger.info(
            "Resume optimised",
            extra={
                "name": profile.name,
                "skills_reordered": True,
                "projects_reordered": True,
                "summary_tailored": profile.summary != tailored.summary,
            },
        )

        return tailored
