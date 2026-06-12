"""Recommendation engine — converts job match scores into apply/don't-apply decisions."""

from __future__ import annotations

import logging

from app.ai.models import JobMatch, JobRecommendation

logger = logging.getLogger("headhunter")

# ── Score thresholds (on a 0.0 – 1.0 scale) ──────────────────

_THRESHOLD_HIGH: float = 0.85    # ≥ 85 → HIGH priority, apply
_THRESHOLD_MEDIUM: float = 0.70  # 70–84 → MEDIUM priority, apply
_THRESHOLD_LOW: float = 0.55     # 55–69 → LOW priority, don't apply
                                  # < 55  → REJECT, don't apply


class RecommendationEngine:
    """Converts a :class:`JobMatch` into an :class:`JobRecommendation`.

    The engine applies a deterministic threshold-based ruleset that
    decides whether the candidate should apply and at what priority.
    """

    def recommend(self, match: JobMatch) -> JobRecommendation:
        """Generate an application recommendation from a job match.

        Args:
            match: The :class:`JobMatch` from the LLM evaluator.

        Returns:
            A :class:`JobRecommendation` with the decision, priority,
            and a plain-text explanation.
        """
        score = match.score

        if score >= _THRESHOLD_HIGH:
            return JobRecommendation(
                apply=True,
                priority="HIGH",
                explanation=self._explain_high(match),
            )

        if score >= _THRESHOLD_MEDIUM:
            return JobRecommendation(
                apply=True,
                priority="MEDIUM",
                explanation=self._explain_medium(match),
            )

        if score >= _THRESHOLD_LOW:
            return JobRecommendation(
                apply=False,
                priority="LOW",
                explanation=self._explain_low(match),
            )

        return JobRecommendation(
            apply=False,
            priority="REJECT",
            explanation=self._explain_reject(match),
        )

    # ── Explanation builders ─────────────────────────────────

    @staticmethod
    def _explain_high(match: JobMatch) -> str:
        return (
            f"Strong match (score: {match.score:.0%}). "
            f"Matched {len(match.matched_skills)} key skills "
            f"({', '.join(match.matched_skills[:5])}). "
            f"Missing {len(match.missing_skills)} skills "
            f"({', '.join(match.missing_skills[:3])}). "
            f"Recommended projects: {', '.join(match.recommended_projects[:3])}."
        )

    @staticmethod
    def _explain_medium(match: JobMatch) -> str:
        return (
            f"Moderate match (score: {match.score:.0%}). "
            f"Matched {len(match.matched_skills)} skills, "
            f"missing {len(match.missing_skills)}. "
            f"Consider addressing gaps before applying."
        )

    @staticmethod
    def _explain_low(match: JobMatch) -> str:
        return (
            f"Weak match (score: {match.score:.0%}). "
            f"Only {len(match.matched_skills)} skills overlap "
            f"and {len(match.missing_skills)} key requirements are missing. "
            f"Not recommended for application."
        )

    @staticmethod
    def _explain_reject(match: JobMatch) -> str:
        return (
            f"Poor match (score: {match.score:.0%}). "
            f"Insufficient skill overlap for this role."
        )
