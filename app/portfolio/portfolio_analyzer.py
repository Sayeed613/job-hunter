"""Portfolio analyzer — technology detection and summary generation."""

from __future__ import annotations

import logging
import re

from app.portfolio.models import PortfolioProject

logger = logging.getLogger("headhunter")

# ── Keyword → technology mappings ────────────────────────────

_TECH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"react", re.IGNORECASE), "React"),
    (re.compile(r"vue|vue\.?js", re.IGNORECASE), "Vue.js"),
    (re.compile(r"angular", re.IGNORECASE), "Angular"),
    (re.compile(r"svelte", re.IGNORECASE), "Svelte"),
    (re.compile(r"next\.?js|nextjs", re.IGNORECASE), "Next.js"),
    (re.compile(r"node\.?js", re.IGNORECASE), "Node.js"),
    (re.compile(r"express", re.IGNORECASE), "Express"),
    (re.compile(r"django", re.IGNORECASE), "Django"),
    (re.compile(r"flask", re.IGNORECASE), "Flask"),
    (re.compile(r"fastapi", re.IGNORECASE), "FastAPI"),
    (re.compile(r"spring\s*boot", re.IGNORECASE), "Spring Boot"),
    (re.compile(r"python", re.IGNORECASE), "Python"),
    (re.compile(r"javascript", re.IGNORECASE), "JavaScript"),
    (re.compile(r"typescript", re.IGNORECASE), "TypeScript"),
    (re.compile(r"docker", re.IGNORECASE), "Docker"),
    (re.compile(r"kubernetes|k8s", re.IGNORECASE), "Kubernetes"),
    (re.compile(r"aws|amazon web", re.IGNORECASE), "AWS"),
    (re.compile(r"gcp|google cloud", re.IGNORECASE), "GCP"),
    (re.compile(r"azure", re.IGNORECASE), "Azure"),
    (re.compile(r"postgres|postgresql", re.IGNORECASE), "PostgreSQL"),
    (re.compile(r"mysql|mariadb", re.IGNORECASE), "SQL"),
    (re.compile(r"mongodb|mongo", re.IGNORECASE), "MongoDB"),
    (re.compile(r"redis", re.IGNORECASE), "Redis"),
    (re.compile(r"graphql", re.IGNORECASE), "GraphQL"),
    (re.compile(r"tensorflow|pytorch", re.IGNORECASE), "Machine Learning"),
    (re.compile(r"tailwind", re.IGNORECASE), "Tailwind CSS"),
    (re.compile(r"bootstrap", re.IGNORECASE), "Bootstrap"),
    (re.compile(r"api|rest", re.IGNORECASE), "REST API"),
    (re.compile(r"firebase", re.IGNORECASE), "Firebase"),
    (re.compile(r"supabase", re.IGNORECASE), "Supabase"),
    (re.compile(r"docker.*compose", re.IGNORECASE), "Docker Compose"),
    (re.compile(r"ci/cd|github actions|gitlab ci", re.IGNORECASE), "CI/CD"),
]


class PortfolioAnalyzer:
    """Analyses portfolio projects to detect technologies and generate summaries."""

    def analyze(self, project: PortfolioProject) -> PortfolioProject:
        """Analyse a raw portfolio project and enrich it with detected technologies.

        Args:
            project: A :class:`PortfolioProject` with at minimum a name
                and description filled in.

        Returns:
            The same project with ``technologies`` and ``summary`` populated.
        """
        # Detect technologies from name + description + project_url.
        detected: set[str] = set()
        text = " ".join([
            project.name,
            project.description,
            project.project_url,
            project.github_url,
        ])

        for pattern, tech in _TECH_PATTERNS:
            if pattern.search(text):
                detected.add(tech)

        project.technologies = sorted(detected)
        project.keywords = self.generate_keywords(project)
        project.summary = self._summarize(project)

        logger.info(
            "Portfolio project analysed",
            extra={
                "name": project.name,
                "technologies": len(project.technologies),
            },
        )

        return project

    def generate_keywords(self, project: PortfolioProject) -> list[str]:
        """Extract meaningful keyword tokens from a portfolio project.

        Returns a deduplicated, lower-cased list of keywords derived
        from the project's name, description, technologies, and URL
        paths, excluding common stop-words.

        Args:
            project: A :class:`PortfolioProject` instance.

        Returns:
            A list of keyword strings.
        """
        _stop = {
            "the", "and", "for", "with", "this", "that", "from", "using",
            "built", "project", "app", "web", "site", "based", "made",
        }
        text = " ".join([
            project.name, project.description,
            " ".join(project.technologies),
            project.project_url, project.github_url,
        ]).lower()

        tokens = re.split(r"[\s/._\-?:=&]+", text)
        seen: set[str] = set()
        keywords: list[str] = []

        for token in tokens:
            token = token.strip(".,;!")
            if (
                token
                and len(token) > 2
                and token not in _stop
                and not token.isdigit()
                and token not in seen
            ):
                keywords.append(token)
                seen.add(token)

        return keywords

    def analyze_all(
        self,
        projects: list[PortfolioProject],
    ) -> list[PortfolioProject]:
        """Analyse a list of portfolio projects in place.

        Args:
            projects: List of :class:`PortfolioProject` instances.

        Returns:
            The same list with each project enriched.
        """
        return [self.analyze(p) for p in projects]

    # ── Summary generator ────────────────────────────────────

    @staticmethod
    def _summarize(project: PortfolioProject) -> str:
        """Build a one-line summary for a portfolio project."""
        parts = [project.name]
        if project.description:
            short = project.description[:100]
            if len(project.description) > 100:
                short += "…"
            parts.append(f"— {short}")
        if project.technologies:
            parts.append(f"[{', '.join(project.technologies[:6])}]")
        return " ".join(parts)
