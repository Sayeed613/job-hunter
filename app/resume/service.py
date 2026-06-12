"""Resume service — load, cache, and query resume profiles."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.resume.models import Project, ResumeProfile
from app.resume.parser import ResumeParser

logger = logging.getLogger("headhunter")


class ResumeService:
    """Service that loads a resume from a .docx file and caches the result.

    The service is designed to be instantiated once at startup with the
    path to the user's resume file.  Callers can then query the parsed
    profile, filter projects, or retrieve skills without re-parsing.
    """

    def __init__(self, resume_path: str | Path) -> None:
        """Initialise the service.

        Args:
            resume_path: Path to the .docx resume file.  The file is
                **not** parsed until :meth:`load_resume` is called.
        """
        self._path = Path(resume_path)
        self._parser = ResumeParser()
        self._profile: Optional[ResumeProfile] = None

    # ── Lifecycle ─────────────────────────────────────────────

    def load_resume(self, path: str | Path | None = None) -> ResumeProfile:
        """Parse (or reload) the resume file and cache the profile.

        Calling this method more than once re-parses the file, so any
        changes made to the document on disk will be picked up.

        Args:
            path: Optional override path.  When provided, the service
                uses this file instead of the one passed at construction
                time and updates its internal path.

        Returns:
            The parsed :class:`ResumeProfile`.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file is not a valid .docx document.
        """
        if path is not None:
            self._path = Path(path)
        self._profile = self._parser.parse_docx(str(self._path))
        logger.info(
            "Resume loaded",
            extra={
                "path": str(self._path),
                "candidate": self._profile.name,
                "skills_count": len(self._profile.skills),
                "projects_count": len(self._profile.projects),
            },
        )
        return self._profile

    # ── Queries ───────────────────────────────────────────────

    def get_profile(self) -> ResumeProfile:
        """Return the cached resume profile.

        Returns:
            The cached :class:`ResumeProfile`.

        Raises:
            RuntimeError: If :meth:`load_resume` has not been called yet.
        """
        if self._profile is None:
            raise RuntimeError(
                "Resume not loaded. Call load_resume() first."
            )
        return self._profile

    def get_skills(self) -> list[str]:
        """Return the list of skills from the cached profile.

        Returns:
            A list of skill strings.

        Raises:
            RuntimeError: If :meth:`load_resume` has not been called yet.
        """
        return self.get_profile().skills

    def get_projects(self) -> list[Project]:
        """Return the list of projects from the cached profile.

        Returns:
            A list of :class:`Project` instances.

        Raises:
            RuntimeError: If :meth:`load_resume` has not been called yet.
        """
        return self.get_profile().projects

    def search_projects_by_skill(self, skill: str) -> list[Project]:
        """Return projects whose name or technologies mention *skill*.

        The comparison is case-insensitive.

        Args:
            skill: The skill keyword to search for.

        Returns:
            A list of matching :class:`Project` instances.
        """
        skill_lower = skill.lower()
        matches: list[Project] = []
        for project in self.get_projects():
            if skill_lower in project.name.lower():
                matches.append(project)
                continue
            if any(
                skill_lower in tech.lower()
                for tech in project.technologies
            ):
                matches.append(project)
        return matches
