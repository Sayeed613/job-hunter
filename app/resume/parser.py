"""Resume parser that extracts structured data from .docx files."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from docx import Document

from app.resume.models import Project, ResumeProfile

logger = logging.getLogger("headhunter")

# ── Regex patterns ───────────────────────────────────────────

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_PATTERN = re.compile(
    r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
)
_SECTION_HEADERS = re.compile(
    r"^(summary|professional\s*summary|objective|profile|"
    r"skills|technical\s*skills|core\s*competencies|"
    r"experience|work\s*experience|employment|professional\s*experience|"
    r"projects|project[s]?\s*done|key\s*projects|"
    r"education|academic\s*background|"
    r"certifications|certificates|licenses)",
    re.IGNORECASE,
)


class ResumeParser:
    """Parse .docx resume files into structured :class:`ResumeProfile`."""

    def parse_docx(self, path: str) -> ResumeProfile:
        """Read a .docx file and return a populated :class:`ResumeProfile`.

        Args:
            path: Filesystem path to the .docx file.

        Returns:
            A :class:`ResumeProfile` with all extracted fields.

        Raises:
            FileNotFoundError: If the path does not exist.
            ValueError: If the file is not a valid .docx document.
        """
        file_path = Path(path)

        if not file_path.exists():
            raise FileNotFoundError(f"Resume file not found: {file_path}")
        if file_path.suffix.lower() not in (".docx",):
            raise ValueError(
                f"Unsupported file format: {file_path.suffix}. "
                "Only .docx files are supported."
            )

        try:
            document = Document(str(file_path))
        except Exception as exc:
            logger.exception("Failed to open document %s", path)
            raise ValueError(
                f"Cannot parse {path} as a .docx document: {exc}"
            ) from exc

        # Collect all paragraph text, stripping empties.
        paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]

        if not paragraphs:
            logger.warning("Document %s contains no readable text", path)
            return ResumeProfile()

        profile = ResumeProfile()
        self._extract_contact_info(paragraphs[0], profile)

        sections = self._split_sections(paragraphs[1:])

        profile.summary = self._extract_summary(sections)
        profile.skills = self._extract_skills(sections)
        profile.experience = self._extract_experience(sections)
        profile.projects = self._extract_projects(sections)
        profile.education = self._extract_education(sections)
        profile.certifications = self._extract_certifications(sections)

        logger.info(
            "Resume parsed successfully",
            extra={
                "file": path,
                "candidate": profile.name,
                "skills": len(profile.skills),
                "experience_entries": len(profile.experience),
                "projects": len(profile.projects),
            },
        )
        return profile

    # ── Contact info extraction ───────────────────────────────

    @staticmethod
    def _extract_contact_info(text: str, profile: ResumeProfile) -> None:
        """Extract name, email, phone, and location from the first line."""
        # The first paragraph is typically the candidate's name.
        profile.name = text.split("\n")[0].strip() if text else ""

        emails = _EMAIL_PATTERN.findall(text)
        if emails:
            profile.email = emails[0]

        phones = _PHONE_PATTERN.findall(text)
        if phones:
            # phones[0] is a tuple from the groups; join non-empty parts.
            cleaned = "".join(part for part in phones[0] if part)
            if not cleaned:
                # Fallback: re-search for the full match
                match = _PHONE_PATTERN.search(text)
                if match:
                    cleaned = match.group()
            profile.phone = cleaned.strip(".- ")

        # Naive location: first line segment that looks like a city, state.
        # Heuristic: split on newlines / pipes and look for known patterns.
        for segment in re.split(r"[|\n]", text):
            segment = segment.strip()
            if not segment:
                continue
            # Skip segments that look like email or phone.
            if "@" in segment or re.search(r"\d{3}", segment):
                continue
            # If it looks like a location (e.g. "San Francisco, CA" or "remote")
            if re.match(r"^[A-Za-z\s,.-]+$", segment) and segment != profile.name:
                profile.location = segment
                break

    # ── Section splitting ─────────────────────────────────────

    @staticmethod
    def _split_sections(
        paragraphs: list[str],
    ) -> dict[str, list[str]]:
        """Group paragraphs into labelled sections based on header keywords."""
        sections: dict[str, list[str]] = {}
        current_section = "_header"

        for para in paragraphs:
            match = _SECTION_HEADERS.match(para.strip())
            if match:
                current_section = match.group(1).lower().replace(" ", "_")
                sections.setdefault(current_section, [])
            else:
                sections.setdefault(current_section, []).append(para)

        return sections

    # ── Field extraction ──────────────────────────────────────

    @staticmethod
    def _extract_summary(sections: dict[str, list[str]]) -> str:
        """Combine paragraphs from summary / objective sections."""
        for key in ("summary", "professional_summary", "objective", "profile"):
            if key in sections:
                return " ".join(sections[key])
        return ""

    @staticmethod
    def _extract_skills(sections: dict[str, list[str]]) -> list[str]:
        """Extract skills from the skills section.

        Skills are often comma-delimited on a single line.  This method
        splits on commas and returns individual skill tokens.
        """
        skills: list[str] = []
        for key in ("skills", "technical_skills", "core_competencies"):
            if key not in sections:
                continue
            for line in sections[key]:
                # Split on commas or bullets.
                parts = re.split(r"[,•·▪●○◆\-–—|]+", line)
                for part in parts:
                    cleaned = part.strip()
                    if cleaned and len(cleaned) > 1:
                        skills.append(cleaned)
        return skills

    @staticmethod
    def _extract_experience(sections: dict[str, list[str]]) -> list[str]:
        """Return experience paragraphs as-is (one entry per paragraph)."""
        for key in ("experience", "work_experience", "employment", "professional_experience"):
            if key in sections:
                return sections[key]
        return []

    @staticmethod
    def _extract_projects(sections: dict[str, list[str]]) -> list[Project]:
        """Parse project entries into structured :class:`Project` objects.

        Each project entry may span multiple paragraphs.  This simple
        parser treats each paragraph as a separate project.
        """
        projects: list[Project] = []
        for key in ("projects", "project_s_done", "key_projects"):
            if key not in sections:
                continue
            for line in sections[key]:
                # Heuristic: first colon-split for name vs description.
                if ":" in line:
                    name_part, _, desc_part = line.partition(":")
                    projects.append(
                        Project(
                            name=name_part.strip(),
                            description=desc_part.strip(),
                        )
                    )
                else:
                    projects.append(Project(name=line.strip()))
            break  # Only process the first matching section.
        return projects

    @staticmethod
    def _extract_education(sections: dict[str, list[str]]) -> list[str]:
        """Return education entries as-is."""
        for key in ("education", "academic_background"):
            if key in sections:
                return sections[key]
        return []

    @staticmethod
    def _extract_certifications(sections: dict[str, list[str]]) -> list[str]:
        """Return certification entries as-is."""
        for key in ("certifications", "certificates", "licenses"):
            if key in sections:
                return sections[key]
        return []
