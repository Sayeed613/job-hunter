"""Resume parser — extracts structured data from .docx resume files.

If the resume file is not found, falls back to hardcoded defaults
for Sayeed Ahmed (the candidate).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from docx import Document

from app.resume.models import Project, ResumeProfile

logger = logging.getLogger("job_automation_bot")

# ── Regex patterns ───────────────────────────────────────────
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_PATTERN = re.compile(
    r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
)
_SECTION_HEADERS = re.compile(
    r"^(summary|professional\s*summary|objective|profile|"
    r"skills|technical\s*skills|core\s*competencies|"
    r"experience|work\s*experience|internship\s*experience|"
    r"employment|professional\s*experience|"
    r"projects|project[s]?\s*done|key\s*projects|"
    r"education|academic\s*background|"
    r"certifications|certificates|licenses|additional\s*information)",
    re.IGNORECASE,
)


class ResumeParser:
    """Parses .docx resume files into structured ResumeProfile.

    Falls back to hardcoded Sayeed Ahmed profile if the file is missing.
    """

    def parse_docx(self, path: str) -> ResumeProfile:
        """Read a .docx file and return a populated ResumeProfile.

        Args:
            path: Filesystem path to the .docx file.

        Returns:
            A ResumeProfile with extracted fields.
        """
        file_path = Path(path)

        if not file_path.exists():
            logger.warning("Resume file not found at %s — using hardcoded profile", path)
            return self._hardcoded_profile()

        try:
            document = Document(str(file_path))
        except Exception as exc:
            logger.exception("Failed to open document %s", path)
            return self._hardcoded_profile()

        paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        if not paragraphs:
            logger.warning("Document %s is empty — using hardcoded profile", path)
            return self._hardcoded_profile()

        profile = ResumeProfile()
        # Search contact info across first 5 paragraphs (email/phone/location are in para 2)
        contact_text = "\n".join(paragraphs[:5])
        self._extract_contact_info(contact_text, profile)
        profile.name = paragraphs[0].strip() or profile.name
        sections = self._split_sections(paragraphs[1:])

        profile.summary = self._extract_summary(sections)
        profile.skills = self._extract_skills(sections)
        profile.experience = self._extract_experience(sections)
        profile.projects = self._extract_projects(sections)
        profile.education = self._extract_education(sections)
        profile.certifications = self._extract_certifications(sections)

        # If the DOCX parsing failed to extract meaningful skills/experience,
        # fall back to the hardcoded profile values (candidate-specific defaults)
        hardcoded = self._hardcoded_profile()
        merged_skills = False
        if len(profile.skills) < 5 and len(hardcoded.skills) >= 5:
            profile.skills = hardcoded.skills
            merged_skills = True
        if len(profile.experience) == 0 and len(hardcoded.experience) > 0:
            profile.experience = hardcoded.experience
        if not profile.summary and hardcoded.summary:
            profile.summary = hardcoded.summary
        if len(profile.education) == 0 and len(hardcoded.education) > 0:
            profile.education = hardcoded.education
        if len(profile.certifications) == 0 and len(hardcoded.certifications) > 0:
            profile.certifications = hardcoded.certifications

        logger.info(
            "Resume parsed",
            extra={
                "file": path,
                "candidate": profile.name,
                "skills": len(profile.skills),
                "projects": len(profile.projects),
                "merged_skills": merged_skills,
            },
        )
        return profile

    @staticmethod
    def _hardcoded_profile() -> ResumeProfile:
        """Return Sayeed Ahmed's profile with hardcoded data from the spec."""
        return ResumeProfile(
            name="Sayeed Ahmed",
            email="sayeedahmed90082@gmail.com",
            phone="+91-9008299613",
            location="Bangalore, Karnataka, India",
            summary=(
                "Frontend developer with 1+ year of professional experience at Actobiz "
                "and a 6-month internship at Tekiarz. Skilled in building responsive web "
                "applications using React, Next.js, TypeScript, and Tailwind CSS. "
                "Experienced in integrating REST APIs, converting Figma designs to code, "
                "and optimizing frontend performance. Proficient in modern JavaScript "
                "(ES6+), Git, and agile development practices."
            ),
            skills=[
                "React",
                "Next.js",
                "TypeScript",
                "JavaScript",
                "Tailwind CSS",
                "HTML5",
                "CSS3",
                "Python",
                "FastAPI",
                "Node.js",
                "Git",
                "REST APIs",
                "MongoDB",
                "PostgreSQL",
                "Firebase",
                "Docker",
                "AWS",
                "Figma",
                "Agile/Jira",
            ],
            experience=[
                "Frontend Developer | Actobiz | 2024–2025 (1 year) | "
                "Built responsive web applications using React, Next.js, and Tailwind CSS. "
                "Integrated RESTful APIs, implemented state management, and optimized "
                "performance through lazy loading and code splitting. Collaborated with "
                "designers to convert Figma mockups into pixel-perfect UIs. "
                "Worked in an agile environment using Git and Jira.",
                "Software Engineer Intern | Tekiarz | 2024 (6 months) | "
                "Developed responsive web pages using HTML5, CSS3, and JavaScript (ES6+). "
                "Built interactive UI components with React.js. Converted Figma mockups "
                "to functional web pages. Integrated RESTful APIs using Axios/Fetch. "
                "Optimized frontend performance with lazy loading, code splitting, and "
                "image compression. Used Git/GitHub for version control and Jira for "
                "project management.",
            ],
            projects=[
                Project(
                    name="Project Headhunter",
                    description="AI-powered job automation bot. Scrapes 10+ platforms, "
                    "tailors resumes using GPT, and auto-applies via browser automation. "
                    "Tech: Python, Playwright, OpenAI, Firebase.",
                    technologies=["Python", "Playwright", "OpenAI", "Firebase"],
                ),
                Project(
                    name="E-Commerce Dashboard",
                    description="Full-stack dashboard for inventory management. "
                    "React frontend, FastAPI backend, PostgreSQL database.",
                    technologies=["React", "FastAPI", "PostgreSQL", "Docker"],
                ),
                Project(
                    name="Real-Time Chat App",
                    description="WebSocket-based chat application with user "
                    "authentication and message persistence.",
                    technologies=["React", "Node.js", "WebSocket", "MongoDB"],
                ),
                Project(
                    name="Uber Clone",
                    description="A React.js application with ride booking flows, "
                    "real-time driver tracking, and Google Maps API integration.",
                    technologies=["React", "Google Maps API", "JavaScript"],
                ),
                Project(
                    name="Personal Portfolio",
                    description="Responsive portfolio site with dark mode, "
                    "animated sections, and modern UI design.",
                    technologies=["HTML5", "CSS3", "JavaScript"],
                ),
            ],
            education=[
                "Bachelor of Computer Applications (BCA) | Sabarmathi University | 2021–2024",
            ],
            certifications=[
                "Frontend Development Certification — freeCodeCamp",
                "The Complete JavaScript Course 2024 — Udemy",
                "React – The Complete Guide — Udemy",
            ],
        )

    @staticmethod
    def _extract_contact_info(text: str, profile: ResumeProfile) -> None:
        profile.name = text.split("\n")[0].strip() if text else ""
        emails = _EMAIL_PATTERN.findall(text)
        if emails:
            profile.email = emails[0]
        phones = _PHONE_PATTERN.findall(text)
        if phones:
            cleaned = "".join(part for part in phones[0] if part)
            if not cleaned:
                match = _PHONE_PATTERN.search(text)
                if match:
                    cleaned = match.group()
            profile.phone = cleaned.strip(".- ")
        for segment in re.split(r"[|\n]", text):
            segment = segment.strip()
            if not segment:
                continue
            if "@" in segment or re.search(r"\d{3}", segment):
                continue
            if re.match(r"^[A-Za-z\s,.-]+$", segment) and segment != profile.name:
                profile.location = segment
                break

    @staticmethod
    def _split_sections(paragraphs: list[str]) -> dict[str, list[str]]:
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

    @staticmethod
    def _extract_summary(sections: dict[str, list[str]]) -> str:
        for key in ("summary", "professional_summary", "objective", "profile"):
            if key in sections:
                return " ".join(sections[key])
        return ""

    @staticmethod
    def _extract_skills(sections: dict[str, list[str]]) -> list[str]:
        skills: list[str] = []
        for key in ("skills", "technical_skills", "core_competencies"):
            if key not in sections:
                continue
            for line in sections[key]:
                parts = re.split(r"[,•·▪●○◆\-–—|]+", line)
                for part in parts:
                    cleaned = part.strip()
                    if cleaned and len(cleaned) > 1:
                        skills.append(cleaned)
        return skills

    @staticmethod
    def _extract_experience(sections: dict[str, list[str]]) -> list[str]:
        for key in (
            "experience", "work_experience", "internship_experience",
            "employment", "professional_experience",
        ):
            if key in sections:
                return sections[key]
        return []

    @staticmethod
    def _extract_projects(sections: dict[str, list[str]]) -> list[Project]:
        projects: list[Project] = []
        for key in ("projects", "project_s_done", "key_projects"):
            if key not in sections:
                continue
            for line in sections[key]:
                if ":" in line:
                    name_part, _, desc_part = line.partition(":")
                    projects.append(Project(name=name_part.strip(), description=desc_part.strip()))
                else:
                    projects.append(Project(name=line.strip()))
            break
        return projects

    @staticmethod
    def _extract_education(sections: dict[str, list[str]]) -> list[str]:
        for key in ("education", "academic_background"):
            if key in sections:
                return sections[key]
        return []

    @staticmethod
    def _extract_certifications(sections: dict[str, list[str]]) -> list[str]:
        for key in ("certifications", "certificates", "licenses"):
            if key in sections:
                return sections[key]
        return []
