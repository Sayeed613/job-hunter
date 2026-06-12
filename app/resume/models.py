"""Data models for resume profiles and parsed resume content."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Project:
    """A personal or professional project listed on a resume.

    Attributes:
        name: Project title.
        description: Short description of the project.
        technologies: Technologies, frameworks, or tools used.
    """

    name: str = ""
    description: str = ""
    technologies: list[str] = field(default_factory=list)


@dataclass
class ResumeProfile:
    """Structured representation of a parsed resume.

    Attributes:
        name: Full name of the candidate.
        email: Email address.
        phone: Phone number.
        location: Geographic location (city, state, remote).
        summary: Professional summary or objective paragraph.
        skills: List of technical and professional skills.
        experience: List of work experience entries as free-text strings.
        projects: List of structured :class:`Project` entries.
        education: List of academic qualifications as free-text strings.
        certifications: List of certifications as free-text strings.
    """

    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    experience: list[str] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
