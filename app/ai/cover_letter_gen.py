"""Cover letter generator — uses GPT to create personalized cover letters."""

from __future__ import annotations

import logging

from app.ai.client import AIClient
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_COVER_LETTER_SYSTEM_PROMPT = """You are writing a cover letter for Sayeed Ahmed, a frontend 
developer in Bangalore, India with 1+ year of experience at Actobiz (React, Next.js, TypeScript, 
Tailwind CSS) and a 6-month internship at Tekiarz. He is applying for a frontend/web development 
role. Write like a real human, not a template. Be specific, not generic. 
Show genuine interest in the company. Sound confident but not arrogant."""


class CoverLetterGenerator:
    """Generates personalized cover letters for job applications using GPT."""

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def generate(
        self,
        resume_summary: str,
        achievements: list[str],
        job: Job,
        keywords: list[str],
    ) -> str:
        """Generate a tailored cover letter.

        Args:
            resume_summary: Top ~300 chars of the tailored resume.
            achievements: 3-5 key achievements from the resume.
            job: The Job to generate a cover letter for.
            keywords: Extracted keywords from the JD (top 5).

        Returns:
            Cover letter text.
        """
        if not self._client.is_available:
            logger.warning("AI client not available — returning empty cover letter")
            return ""

        achievements_text = "\n".join(f"- {a}" for a in achievements[:5])
        key_reqs = ", ".join(keywords[:5])

        user_prompt = (
            f"MY RESUME SUMMARY:\n{resume_summary[:300]}\n\n"
            f"MY TOP ACHIEVEMENTS:\n{achievements_text}\n\n"
            f"JOB TITLE: {job.title}\n"
            f"COMPANY: {job.company}\n"
            f"WHAT THIS COMPANY DOES: {job.description[:500]}\n"
            f"KEY REQUIREMENTS: {key_reqs}\n\n"
            f"TASK:\n"
            f"Write a 3-paragraph cover letter (~280 words).\n"
            f"Para 1 (~80 words): Express genuine enthusiasm for THIS specific role "
            f"at THIS specific company. Mention one concrete thing about the company "
            f"that appeals to me (product, mission, tech stack, or growth stage).\n"
            f"Para 2 (~140 words): Connect 2 of my actual achievements to 2 of the "
            f"key requirements. Be specific — use real numbers and technologies "
            f"from my resume. Show I can do the job.\n"
            f"Para 3 (~60 words): Confident close. State I am excited to contribute, "
            f"available immediately, and invite them to review my portfolio/GitHub.\n"
            f"Sign off with:\n"
            f"Best regards,\nSayeed Ahmed\nsayeedahmed90082@gmail.com\n+91-9008299613\n"
            f"Output ONLY the letter text. No subject line, no markdown."
        )

        try:
            return await self._client.chat(
                system_prompt=_COVER_LETTER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.7,
                max_tokens=1000,
            )
        except Exception:
            logger.exception("Cover letter generation failed")
            return ""
