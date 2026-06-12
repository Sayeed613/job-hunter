"""GitHub service — profile loading, project search, and job-specific ranking."""

from __future__ import annotations

import logging
from typing import Optional

from app.ai.models import JobMatch
from app.github.github_client import GithubClient
from app.github.models import GithubProfile, GithubProject
from app.github.repo_analyzer import RepoAnalyzer

logger = logging.getLogger("headhunter")


class GithubService:
    """Orchestrates fetching and analysis of a GitHub profile.

    Combines :class:`GithubClient` (API calls) and :class:`RepoAnalyzer`
    (skill detection, summary generation) into a single service that
    caches the analysed profile in memory.
    """

    def __init__(
        self,
        client: GithubClient | None = None,
    ) -> None:
        """Initialise the service.

        Args:
            client: Optional :class:`GithubClient`.  A default client is
                created if none is provided.
        """
        self._client = client or GithubClient()
        self._analyzer = RepoAnalyzer()
        self._profile: Optional[GithubProfile] = None

    # ── Lifecycle ─────────────────────────────────────────────

    def load_profile(self, username: str) -> GithubProfile:
        """Fetch and analyse every public repository for a user.

        Calling this method again re-fetches all data, picking up any
        changes to the profile or repositories.

        Args:
            username: GitHub username.

        Returns:
            A :class:`GithubProfile` with fully analysed projects.
        """
        repos_data = self._client.get_repositories(username)

        projects: list[GithubProject] = []
        for repo_data in repos_data:
            repo_name = repo_data.get("name", "")
            readme = self._client.get_repository_readme(username, repo_name)
            project = self._analyzer.analyze(repo_data, readme)
            projects.append(project)

        # Sort: most stars first.
        projects.sort(key=lambda p: p.stars, reverse=True)

        self._profile = GithubProfile(username=username, projects=projects)

        logger.info(
            "GitHub profile loaded",
            extra={
                "username": username,
                "repos_analysed": len(projects),
            },
        )

        return self._profile

    # ── Queries ───────────────────────────────────────────────

    def get_profile(self) -> GithubProfile:
        """Return the cached profile.

        Returns:
            The cached :class:`GithubProfile`.

        Raises:
            RuntimeError: If :meth:`load_profile` has not been called.
        """
        if self._profile is None:
            raise RuntimeError(
                "GitHub profile not loaded. Call load_profile() first."
            )
        return self._profile

    def search_projects(self, skill: str) -> list[GithubProject]:
        """Return projects whose detected skills or topics include *skill*.

        Comparison is case-insensitive.  Results are sorted by star count
        descending.

        Args:
            skill: The skill keyword to search for.

        Returns:
            A list of matching :class:`GithubProject` instances.
        """
        skill_lower = skill.lower()
        matches: list[GithubProject] = []

        for project in self.get_profile().projects:
            if any(skill_lower in s.lower() for s in project.detected_skills):
                matches.append(project)
                continue
            if any(skill_lower in t.lower() for t in project.topics):
                matches.append(project)

        matches.sort(key=lambda p: p.stars, reverse=True)
        return matches

    def get_best_projects_for_job(
        self,
        job_match: JobMatch,
        top_n: int = 3,
    ) -> list[GithubProject]:
        """Return the most relevant GitHub projects for a job match.

        Ranking criteria (in order):
        1. Number of matched skills present in the project's detected skills.
        2. Star count (higher is better).
        3. Whether the project's topics overlap with the job's missing skills.

        Args:
            job_match: A :class:`JobMatch` from the AI matching pipeline.
            top_n: Maximum number of projects to return (default 3).

        Returns:
            A ranked list of :class:`GithubProject` instances.
        """
        all_projects = self.get_profile().projects
        scored: list[tuple[GithubProject, float]] = []

        for project in all_projects:
            score = 0.0

            # Bonus for each matched skill the project covers.
            project_skills = {s.lower() for s in project.detected_skills}
            skill_overlap = sum(
                1 for s in job_match.matched_skills
                if s.lower() in project_skills
            )
            score += skill_overlap * 2.0

            # Bonus for covering missing skills (gap-filler).
            missing_overlap = sum(
                1 for s in job_match.missing_skills
                if s.lower() in project_skills
            )
            score += missing_overlap * 3.0

            # Small star bonus (normalised).
            score += min(project.stars / 100, 10.0)

            if score > 0:
                scored.append((project, score))

        # Sort descending by score, then by stars as tiebreaker.
        scored.sort(key=lambda x: (x[1], x[0].stars), reverse=True)

        ranked = [p for p, _ in scored[:top_n]]

        logger.info(
            "Best projects selected for job",
            extra={
                "job_id": job_match.job_id,
                "candidates": len(scored),
                "selected": len(ranked),
            },
        )

        return ranked
