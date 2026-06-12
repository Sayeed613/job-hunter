"""Keyword extractor — extracts skills, tools, and requirements from job descriptions using GPT."""

from __future__ import annotations

import logging
from typing import Any

from app.ai.client import AIClient

logger = logging.getLogger("job_automation_bot")

_EXTRACTOR_SYSTEM_PROMPT = """You are an ATS keyword extractor. Extract all technical keywords, 
tools, frameworks, methodologies, and soft skills from the job description. 
Return JSON only in this exact format:
{"hard_skills": [...], "soft_skills": [...], "years_required": N}"""


class KeywordExtractor:
    """Extracts structured keywords from job descriptions using GPT."""

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def extract(self, job_description: str) -> dict[str, Any]:
        """Extract keywords from a job description.

        Args:
            job_description: Full job description text.

        Returns:
            Dict with keys: hard_skills (list), soft_skills (list), years_required (int).
        """
        if not self._client.is_available:
            logger.warning("AI client not available — returning empty keywords")
            return {"hard_skills": [], "soft_skills": [], "years_required": 0}

        try:
            result = await self._client.chat_json(
                system_prompt=_EXTRACTOR_SYSTEM_PROMPT,
                user_prompt=f"JD: {job_description}",
                temperature=0.1,
                max_tokens=1000,
            )
            # Ensure all keys exist
            result.setdefault("hard_skills", [])
            result.setdefault("soft_skills", [])
            result.setdefault("years_required", 0)
            return result
        except Exception:
            logger.exception("Keyword extraction failed")
            return {"hard_skills": [], "soft_skills": [], "years_required": 0}
