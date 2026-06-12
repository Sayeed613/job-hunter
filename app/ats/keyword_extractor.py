"""Keyword extraction from job descriptions."""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from app.resume.models import ResumeProfile

logger = logging.getLogger("headhunter")

# ── Stop-words excluded from keyword output ──────────────────

_STOP_WORDS: set[str] = {
    "the", "and", "for", "you", "will", "have", "with", "this",
    "that", "from", "your", "our", "are", "can", "has", "all",
    "not", "but", "its", "also", "per", "via", "than", "then",
    "been", "were", "was", "being", "some", "any", "each", "every",
    "able", "about", "over", "into", "more", "most", "much", "such",
    "what", "when", "where", "which", "who", "how", "why", "just",
    "well", "very", "new", "other", "same", "own", "good", "high",
    "low", "big", "small", "make", "made", "use", "used", "using",
    "work", "works", "working", "need", "needs", "needed", "must",
    "like", "love", "team", "role", "job", "position", "part",
    "full", "time", "year", "years", "plus", "including",
    "responsible", "responsibilities", "qualifications",
    "requirements", "preferred", "nice", "must", "experience",
    "skill", "skills", "ability", "able", "strong", "proven",
    "demonstrated", "knowledge", "understanding", "familiarity",
    "exposure", "proficiency", "proficient", "excellent", "good",
    "solid", "minimum", "required", "desired", "preferred",
    "including", "e.g.", "i.e.", "etc", "etc.",
}


class KeywordExtractor:
    """Extracts structured keywords from a job description.

    Uses section-aware parsing to identify important sections
    (Requirements, Qualifications, etc.) and extracts keyword
    candidates from those sections in priority order.
    """

    SECTION_PATTERNS: ClassVar[list[re.Pattern]] = [
        re.compile(
            r"^(requirements|qualifications|what you.ll need|"
            r"what we.re looking for|about you|you have|"
            r"must have|nice to have|preferred|skills|"
            r"technical skills|tech stack|technologies|"
            r"experience with|proficient in)",
            re.IGNORECASE,
        ),
    ]

    # ── Public API ───────────────────────────────────────────

    def extract_keywords(self, job_description: str) -> list[str]:
        """Extract distinct keywords from a job description.

        Args:
            job_description: The full text of the job posting.

        Returns:
            A deduplicated, lower-cased list of keyword strings,
            ordered by approximate importance (section order).
        """
        if not job_description:
            return []

        lines = job_description.split("\n")
        sections: dict[str, list[str]] = {}
        current_section = "intro"

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            match = self._match_section(stripped)
            if match:
                current_section = match
            sections.setdefault(current_section, []).append(stripped)

        keywords: list[str] = []
        seen: set[str] = set()

        for section_key in self._section_order(sections):
            for line in sections[section_key]:
                for kw in self._extract_from_line(line):
                    lower = kw.lower().strip(".,;:!?\"'()[]{}")
                    if (
                        lower
                        and len(lower) > 1
                        and lower not in seen
                        and lower not in _STOP_WORDS
                    ):
                        keywords.append(lower)
                        seen.add(lower)

        logger.info(
            "Extracted keywords from job description",
            extra={"keyword_count": len(keywords)},
        )

        return keywords

    def compare_with_resume(
        self,
        jd_keywords: list[str],
        resume: ResumeProfile,
    ) -> dict:
        """Compare JD keywords against the candidate's resume profile.

        Args:
            jd_keywords: Keywords extracted from the job description.
            resume: The parsed :class:`ResumeProfile`.

        Returns:
            A dict with keys:
            ``all_keywords``: Full list of JD keywords.
            ``matched_keywords``: Keywords found in resume skills or
                experience text.
            ``missing_keywords``: Keywords from the JD not found in
                the resume.
            ``match_ratio``: ``matched / total`` as a float (0.0 – 1.0).
        """
        resume_text = self._build_resume_text(resume)
        resume_lower = resume_text.lower()

        matched: list[str] = []
        missing: list[str] = []

        for kw in jd_keywords:
            lower_kw = kw.lower()
            if lower_kw in resume_lower:
                matched.append(kw)
            else:
                missing.append(kw)

        total = len(jd_keywords)
        match_ratio = len(matched) / total if total > 0 else 0.0

        logger.info(
            "Keyword comparison complete",
            extra={
                "total_keywords": total,
                "matched": len(matched),
                "missing": len(missing),
                "match_ratio": round(match_ratio, 3),
            },
        )

        return {
            "all_keywords": jd_keywords,
            "matched_keywords": matched,
            "missing_keywords": missing,
            "match_ratio": round(match_ratio, 3),
        }

    # ── Section analysis ─────────────────────────────────────

    @staticmethod
    def _match_section(line: str) -> str | None:
        """Return a section key if the line looks like a section header."""
        for pattern in KeywordExtractor.SECTION_PATTERNS:
            m = pattern.match(line.strip(":* "))
            if m:
                return m.group(1).lower()
        return None

    @staticmethod
    def _section_order(
        sections: dict[str, list[str]],
    ) -> list[str]:
        """Return section keys ordered from most to least important."""
        priority = [
            "must have", "requirements", "qualifications",
            "what you.ll need", "what we.re looking for",
            "preferred", "nice to have", "about you", "you have",
            "skills", "technical skills", "tech stack", "technologies",
            "experience with", "proficient in",
        ]
        ordered = [k for k in priority if k in sections]
        ordered.extend(k for k in sections if k not in ordered)
        return ordered

    # ── Line-level extraction ─────────────────────────────────

    @staticmethod
    def _extract_from_line(line: str) -> list[str]:
        """Extract keyword candidates from a single line of text."""
        candidates: list[str] = []

        parts = re.split(r"[,\t•·▪●○◆‑–—|;/]", line)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            candidates.append(part)
            for token in part.split():
                cleaned = token.strip(".,;:!?\"'()[]{}")
                if len(cleaned) >= 3:
                    candidates.append(cleaned)

        return candidates

    # ── Resume text builder ──────────────────────────────────

    @staticmethod
    def _build_resume_text(resume: ResumeProfile) -> str:
        """Concatenate all resume fields into a single searchable string."""
        parts = [resume.summary]
        parts.extend(resume.skills)
        parts.extend(resume.experience)
        parts.extend(p.name for p in resume.projects)
        parts.extend(
            " ".join(p.technologies) for p in resume.projects
            if p.technologies
        )
        parts.extend(resume.education)
        parts.extend(resume.certifications)
        return " ".join(parts)
