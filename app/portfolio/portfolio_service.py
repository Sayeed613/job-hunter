"""Portfolio service — load, search, and rank portfolio projects."""

from __future__ import annotations

import logging
from typing import Optional

from app.ai.models import JobMatch
from app.portfolio.models import PortfolioProfile, PortfolioProject
from app.portfolio.portfolio_analyzer import PortfolioAnalyzer
from app.portfolio.portfolio_scraper import PortfolioScraper

logger = logging.getLogger("headhunter")


class PortfolioService:
    """Orchestrates scraping and analysis of a portfolio website.

    Combines :class:`PortfolioScraper` (HTML download + extraction) and
    :class:`PortfolioAnalyzer` (technology detection, summaries) into a
    single service that caches the analysed profile in memory.
    """

    def __init__(self) -> None:
        self._scraper = PortfolioScraper()
        self._analyzer = PortfolioAnalyzer()
        self._profile: Optional[PortfolioProfile] = None

    # ── Lifecycle ─────────────────────────────────────────────

    def load_portfolio(self, url: str) -> PortfolioProfile:
        """Download, parse, and analyse a portfolio website.

        Calling this method again re-scrapes the URL and picks up any
        changes to the page.

        Args:
            url: The full URL of the portfolio website.

        Returns:
            A :class:`PortfolioProfile` with analysed projects.
        """
        raw_projects = self._scraper.scrape(url)
        analysed = self._analyzer.analyze_all(raw_projects)

        self._profile = PortfolioProfile(
            portfolio_url=url,
            projects=analysed,
        )

        logger.info(
            "Portfolio loaded",
            extra={
                "url": url,
                "projects_analysed": len(analysed),
            },
        )

        return self._profile

    # ── Queries ───────────────────────────────────────────────

    def get_profile(self) -> PortfolioProfile:
        """Return the cached portfolio profile.

        Returns:
            The cached :class:`PortfolioProfile`.

        Raises:
            RuntimeError: If :meth:`load_portfolio` has not been called.
        """
        if self._profile is None:
            raise RuntimeError(
                "Portfolio not loaded. Call load_portfolio() first."
            )
        return self._profile

    def search_projects(self, skill: str) -> list[PortfolioProject]:
        """Return projects whose name, description, or technologies mention *skill*.

        Comparison is case-insensitive.

        Args:
            skill: The skill keyword to search for.

        Returns:
            A list of matching :class:`PortfolioProject` instances.
        """
        skill_lower = skill.lower()
        matches: list[PortfolioProject] = []

        for project in self.get_profile().projects:
            if skill_lower in project.name.lower():
                matches.append(project)
                continue
            if skill_lower in project.description.lower():
                matches.append(project)
                continue
            if any(
                skill_lower in t.lower()
                for t in project.technologies
            ):
                matches.append(project)

        return matches

    def get_best_projects_for_job(
        self,
        job_match: JobMatch,
        top_n: int = 3,
    ) -> list[PortfolioProject]:
        """Return the most relevant portfolio projects for a job match.

        Ranking criteria:
        1. Number of matched skills present in the project's technologies.
        2. Number of missing skills covered by the project (gap-filler bonus).
        3. Description relevance (keyword presence as tiebreaker).

        Args:
            job_match: A :class:`JobMatch` from the AI matching pipeline.
            top_n: Maximum number of projects to return (default 3).

        Returns:
            A ranked list of :class:`PortfolioProject` instances.
        """
        all_projects = self.get_profile().projects
        scored: list[tuple[PortfolioProject, float]] = []

        for project in all_projects:
            score = 0.0
            project_tech = {t.lower() for t in project.technologies}

            # Matched skills present in project.
            skill_overlap = sum(
                1 for s in job_match.matched_skills
                if s.lower() in project_tech
            )
            score += skill_overlap * 2.0

            # Missing skills covered by project (gap-filler).
            missing_overlap = sum(
                1 for s in job_match.missing_skills
                if s.lower() in project_tech
            )
            score += missing_overlap * 3.0

            # Name/description relevance bonus.
            desc_text = f"{project.name} {project.description}".lower()
            for skill in job_match.matched_skills + job_match.missing_skills:
                if skill.lower() in desc_text:
                    score += 0.5

            if score > 0:
                scored.append((project, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = [p for p, _ in scored[:top_n]]

        logger.info(
            "Best portfolio projects selected for job",
            extra={
                "job_id": job_match.job_id,
                "candidates": len(scored),
                "selected": len(ranked),
            },
        )

        return ranked
