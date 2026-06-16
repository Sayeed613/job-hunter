"""Resume tailor — uses GPT to tailor a resume to a specific job description."""

from __future__ import annotations

import logging

from app.ai.client import AIClient
from app.models.job import Job

logger = logging.getLogger("job_automation_bot")

_TAILOR_SYSTEM_PROMPT = """You are a professional resume writer helping Sayeed Ahmed, a frontend 
developer in Bangalore with 1+ year of experience at Actobiz and a 6-month internship at Tekiarz. 
He needs every resume to be perfectly tailored to the specific job. Be ruthlessly specific. 
Do not be generic. Do not add any skills or experience that are NOT already in the base resume."""


class ResumeTailor:
    """Tailors a resume to a specific job using GPT."""

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def tailor(
        self,
        base_resume_text: str,
        job: Job,
        keywords: list[str],
    ) -> str:
        """Tailor the resume to match the job description.

        Args:
            base_resume_text: Full text of the base resume.
            job: The Job to tailor for.
            keywords: Extracted keywords from the JD.

        Returns:
            Tailored resume text.
        """
        if not self._client.is_available:
            logger.warning("AI client not available — returning base resume unchanged")
            return base_resume_text

        user_prompt = (
            f"BASE RESUME:\n{base_resume_text}\n\n"
            f"JOB TITLE: {job.title}\n"
            f"COMPANY: {job.company}\n"
            f"JOB DESCRIPTION:\n{job.description}\n\n"
            f"EXTRACTED KEYWORDS: {', '.join(keywords)}\n\n"
            f"TASK:\n"
            f"Rewrite the resume so it is laser-targeted at this exact role.\n"
            f"Rules:\n"
            f"1. Keep all dates, companies, job titles EXACTLY the same (do not lie)\n"
            f"2. Reorder bullets in each role so the most relevant appear first\n"
            f"3. Rewrite 2-3 bullets to mirror exact language from the JD\n"
            f"4. Move relevant skills to the top of the skills section\n"
            f"5. If a summary/objective exists, rewrite it for this role in 2 sentences\n"
            f"6. NEVER invent technologies or responsibilities not already there\n"
            f"7. Keep length identical to original (do not add or remove sections)\n"
            f"8. Output ONLY the rewritten resume text, nothing else"
        )

        try:
            return await self._client.chat(
                system_prompt=_TAILOR_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.4,
                max_tokens=2000,
            )
        except Exception:
            logger.exception("Resume tailoring failed — returning base resume")
            return base_resume_text
