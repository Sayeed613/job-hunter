"""Data models for the AI matching and recommendation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class JobMatch:
    """Result of matching a single job posting against a resume.

    Attributes:
        job_id: Identifier of the job that was scored.
        score: Relevance score between 0.0 and 1.0.
        matched_skills: Skills from the resume that align with the job.
        missing_skills: Skills required by the job but absent from the
            resume.
        recommended_projects: Projects from the resume most relevant to
            the job.
        reasoning: Free-text explanation of the score.
    """

    job_id: str = ""
    score: float = 0.0
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    recommended_projects: list[str] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class JobRecommendation:
    """Application recommendation derived from a :class:`JobMatch`.

    Attributes:
        apply: Whether the system recommends applying.
        priority: Priority level — ``HIGH``, ``MEDIUM``, ``LOW``, or
            ``REJECT``.
        explanation: Human-readable reasoning for the recommendation.
    """

    apply: bool = False
    priority: str = "REJECT"
    explanation: str = ""
