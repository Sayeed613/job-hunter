"""Domain models for Project Headhunter."""

from app.models.application import Application, ApplicationStatus
from app.models.job import Job

__all__ = [
    "Application",
    "ApplicationStatus",
    "Job",
]
