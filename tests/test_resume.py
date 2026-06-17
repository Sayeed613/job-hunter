"""Tests for resume loading and parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.resume.service import ResumeService

RESUME_PATH = Path("Sayeed_Frontend_Developer.docx")


@pytest.mark.skipif(not RESUME_PATH.exists(), reason="Resume file not found")
def test_load_resume() -> None:
    service = ResumeService(RESUME_PATH)
    profile = service.load_resume()

    assert profile.name
    assert profile.email
    assert profile.skills or profile.experience or profile.projects


def test_resume_service_requires_path() -> None:
    with pytest.raises(TypeError):
        ResumeService()  # type: ignore[call-arg]
