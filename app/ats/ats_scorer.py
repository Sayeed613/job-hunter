"""ATS scoring engine — evaluates resume fit against job descriptions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.resume.models import ResumeProfile

from app.ats.keyword_extractor import KeywordExtractor

logger = logging.getLogger("headhunter")

# ── Scoring weights ──────────────────────────────────────────

_WEIGHT_KEYWORD_MATCH: float = 0.55
_WEIGHT_SKILLS: float = 0.25
_WEIGHT_EXPERIENCE: float = 0.20


@dataclass
class AtsResult:
    """Result of an ATS compatibility evaluation.

    Attributes:
        total_score: Overall ATS score between 0.0 and 1.0.
        keyword_match_score: Score contributed by keyword overlap.
        skills_score: Score contributed by direct skill proximity.
        experience_score: Score contributed by experience relevance.
        matched_keywords: Keywords present in both the JD and resume.
        missing_keywords: Keywords required by the JD but absent from
            the resume.
        keyword_match_ratio: Proportion of JD keywords covered by the
            resume (0.0 – 1.0).
        breakdown: Human-readable dict explaining the score components.
    """

    total_score: float = 0.0
    keyword_match_score: float = 0.0
    skills_score: float = 0.0
    experience_score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    keyword_match_ratio: float = 0.0
    breakdown: dict[str, Any] = field(default_factory=dict)


class AtsScorer:
    """ATS compatibility scorer.

    Evaluates how well a candidate's resume matches a job description
    by combining keyword analysis, skill proximity, and experience
    indicators into a single weighted score.
    """

    def __init__(self) -> None:
        self._extractor = KeywordExtractor()

    # ── Public API ───────────────────────────────────────────

    def score_job_description(
        self,
        job_description: str,
        resume: ResumeProfile,
    ) -> AtsResult:
        """Run a full ATS evaluation of the resume against a JD.

        Args:
            job_description: Full job description text.
            resume: The parsed :class:`ResumeProfile`.

        Returns:
            An :class:`AtsResult` with score components and keyword
            analysis.
        """
        # Extract keywords from the JD.
        jd_keywords = self._extractor.extract_keywords(job_description)

        # Compare against resume.
        comparison = self._extractor.compare_with_resume(jd_keywords, resume)

        matched: list[str] = comparison["matched_keywords"]
        missing: list[str] = comparison["missing_keywords"]
        match_ratio: float = comparison["match_ratio"]

        # ── Compute sub-scores ────────────────────────────────
        keyword_match_score = self._score_keyword_match(
            match_ratio, len(jd_keywords),
        )
        skills_score = self._score_skills(
            jd_keywords, resume.skills,
        )
        experience_score = self._score_experience(
            jd_keywords, resume.experience,
        )

        # ── Weighted total ────────────────────────────────────
        total_score = (
            _WEIGHT_KEYWORD_MATCH * keyword_match_score
            + _WEIGHT_SKILLS * skills_score
            + _WEIGHT_EXPERIENCE * experience_score
        )

        result = AtsResult(
            total_score=round(total_score, 3),
            keyword_match_score=round(keyword_match_score, 3),
            skills_score=round(skills_score, 3),
            experience_score=round(experience_score, 3),
            matched_keywords=matched,
            missing_keywords=missing,
            keyword_match_ratio=round(match_ratio, 3),
            breakdown=self._build_breakdown(
                total_score, keyword_match_score, skills_score,
                experience_score, match_ratio, len(matched),
                len(missing), len(jd_keywords),
            ),
        )

        logger.info(
            "ATS score computed",
            extra={
                "total_score": result.total_score,
                "keyword_match": result.keyword_match_ratio,
                "matched": len(matched),
                "missing": len(missing),
            },
        )

        return result

    # ── Sub-score calculations ───────────────────────────────

    @staticmethod
    def _score_keyword_match(
        match_ratio: float,
        total_keywords: int,
    ) -> float:
        """Score based on what fraction of JD keywords are covered."""
        if total_keywords == 0:
            return 0.0
        # Diminishing returns: 100 % match → 1.0, 50 % → ~0.7
        return match_ratio ** 0.5

    @staticmethod
    def _score_skills(
        jd_keywords: list[str],
        resume_skills: list[str],
    ) -> float:
        """Score based on direct skill overlap."""
        if not jd_keywords or not resume_skills:
            return 0.0

        resume_skill_set = {s.lower() for s in resume_skills}
        jd_lower = {k.lower() for k in jd_keywords}

        overlap = resume_skill_set & jd_lower
        if not overlap:
            return 0.0

        # Ratio of resume skills that appear in the JD.
        return len(overlap) / len(resume_skill_set)

    @staticmethod
    def _score_experience(
        jd_keywords: list[str],
        resume_experience: list[str],
    ) -> float:
        """Score based on how many JD keywords appear in experience text."""
        if not jd_keywords or not resume_experience:
            return 0.0

        # Count JD keywords that appear in at least one experience entry.
        experience_text = " ".join(resume_experience).lower()
        matched = sum(1 for kw in jd_keywords if kw.lower() in experience_text)

        return matched / len(jd_keywords)

    # ── Breakdown builder ────────────────────────────────────

    @staticmethod
    def _build_breakdown(
        total: float,
        keyword_match: float,
        skills: float,
        experience: float,
        match_ratio: float,
        matched_count: int,
        missing_count: int,
        total_keywords: int,
    ) -> dict[str, Any]:
        """Build a human-readable breakdown dict."""
        return {
            "total_score": round(total, 3),
            "weights": {
                "keyword_match": _WEIGHT_KEYWORD_MATCH,
                "skills": _WEIGHT_SKILLS,
                "experience": _WEIGHT_EXPERIENCE,
            },
            "sub_scores": {
                "keyword_match_raw": round(keyword_match, 3),
                "skills_raw": round(skills, 3),
                "experience_raw": round(experience, 3),
            },
            "keyword_analysis": {
                "total_jd_keywords": total_keywords,
                "matched": matched_count,
                "missing": missing_count,
                "match_ratio": round(match_ratio, 3),
            },
        }
