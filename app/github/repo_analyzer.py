"""Repository analysis — skill detection and summary generation."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.github.models import GithubProject

logger = logging.getLogger("headhunter")

# ── Language → skill mapping ─────────────────────────────────

_LANGUAGE_SKILLS: dict[str, list[str]] = {
    "Python": ["Python"],
    "JavaScript": ["JavaScript"],
    "TypeScript": ["TypeScript"],
    "Java": ["Java"],
    "Go": ["Go", "Golang"],
    "Rust": ["Rust"],
    "Ruby": ["Ruby"],
    "PHP": ["PHP"],
    "Kotlin": ["Kotlin"],
    "Swift": ["Swift"],
    "Scala": ["Scala"],
    "C#": ["C#"],
    "C++": ["C++"],
    "C": ["C"],
    "Shell": ["Bash", "Shell"],
    "HTML": ["HTML"],
    "CSS": ["CSS"],
    "Dockerfile": ["Docker"],
    "HCL": ["Terraform"],
    "SQL": ["SQL"],
}

# ── README keywords that imply skills ────────────────────────

_README_SKILL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"react", re.IGNORECASE), "React"),
    (re.compile(r"vue|vue\.?js", re.IGNORECASE), "Vue.js"),
    (re.compile(r"angular", re.IGNORECASE), "Angular"),
    (re.compile(r"svelte", re.IGNORECASE), "Svelte"),
    (re.compile(r"node\.?js", re.IGNORECASE), "Node.js"),
    (re.compile(r"express", re.IGNORECASE), "Express"),
    (re.compile(r"django", re.IGNORECASE), "Django"),
    (re.compile(r"flask", re.IGNORECASE), "Flask"),
    (re.compile(r"fastapi", re.IGNORECASE), "FastAPI"),
    (re.compile(r"spring\s*boot", re.IGNORECASE), "Spring Boot"),
    (re.compile(r"docker|container", re.IGNORECASE), "Docker"),
    (re.compile(r"kubernetes|k8s", re.IGNORECASE), "Kubernetes"),
    (re.compile(r"terraform", re.IGNORECASE), "Terraform"),
    (re.compile(r"aws|amazon web", re.IGNORECASE), "AWS"),
    (re.compile(r"gcp|google cloud", re.IGNORECASE), "GCP"),
    (re.compile(r"azure", re.IGNORECASE), "Azure"),
    (re.compile(r"postgres|postgresql", re.IGNORECASE), "PostgreSQL"),
    (re.compile(r"mysql", re.IGNORECASE), "MySQL"),
    (re.compile(r"mongodb|mongo", re.IGNORECASE), "MongoDB"),
    (re.compile(r"redis", re.IGNORECASE), "Redis"),
    (re.compile(r"graphql", re.IGNORECASE), "GraphQL"),
    (re.compile(r"grpc", re.IGNORECASE), "gRPC"),
    (re.compile(r"tensorflow|pytorch", re.IGNORECASE), "Machine Learning"),
    (re.compile(r"ci/cd|github\s*actions|jenkins", re.IGNORECASE), "CI/CD"),
    (re.compile(r"rest\s*api", re.IGNORECASE), "REST API"),
]


class RepoAnalyzer:
    """Analyses a GitHub repository to extract skills and generate a summary.

    Uses:
    - Language data from the GitHub API
    - Repository topics
    - README content (scanned for known technology keywords)
    - Repository description
    """

    def analyze(self, repo_data: dict[str, Any], readme: str = "") -> GithubProject:
        """Analyse a repository's metadata and README.

        Args:
            repo_data: Raw repository JSON from the GitHub API (from
                ``/repos`` or ``/users/{user}/repos``).
            readme: The full README text (empty string if none).

        Returns:
            A :class:`GithubProject` with detected skills and a
            human-readable summary.
        """
        repo_name = repo_data.get("full_name", repo_data.get("name", ""))
        description = repo_data.get("description") or ""
        url = repo_data.get("html_url", "")
        topics = repo_data.get("topics", [])
        stars = repo_data.get("stargazers_count", 0)
        language = repo_data.get("language") or ""

        # Collect detected skills from multiple sources.
        detected_skills: set[str] = set()

        # 1. From the primary GitHub language.
        if language in _LANGUAGE_SKILLS:
            detected_skills.update(_LANGUAGE_SKILLS[language])

        # 2. From topics.
        for topic in topics:
            topic_clean = topic.replace("-", " ").replace("_", " ").title()
            detected_skills.add(topic_clean)

        # 3. From README content.
        for pattern, skill in _README_SKILL_PATTERNS:
            if pattern.search(readme):
                detected_skills.add(skill)

        # Generate a summary.
        summary = self._generate_summary(
            repo_name, description, detected_skills, stars,
        )

        project = GithubProject(
            repo_name=repo_name,
            description=description,
            url=url,
            languages=repo_data.get("languages", {}),
            topics=topics,
            stars=stars,
            readme=readme,
            detected_skills=sorted(detected_skills),
            summary=summary,
        )

        logger.info(
            "Repository analysed",
            extra={
                "repo": repo_name,
                "skills": len(detected_skills),
                "stars": stars,
            },
        )

        return project

    # ── Summary generator ────────────────────────────────────

    @staticmethod
    def _generate_summary(
        repo_name: str,
        description: str,
        skills: set[str],
        stars: int,
    ) -> str:
        """Build a one-line summary for the repository."""
        parts = [f"{repo_name}"]
        if description:
            short_desc = description[:120]
            if len(description) > 120:
                short_desc += "…"
            parts.append(f"— {short_desc}")
        if skills:
            skills_str = ", ".join(sorted(skills)[:8])
            parts.append(f"[{skills_str}]")
        if stars:
            parts.append(f"(★{stars})")
        return " ".join(parts)
