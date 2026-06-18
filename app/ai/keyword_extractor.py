"""Keyword extraction from job descriptions."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ai.client import AIClient

logger = logging.getLogger("job_automation_bot")

_EXTRACTOR_SYSTEM_PROMPT = """You are an ATS keyword extractor. Extract all technical keywords,
tools, frameworks, methodologies, and soft skills from the job description.
Return JSON only in this exact format:
{"hard_skills": [...], "soft_skills": [...], "years_required": N}"""


class KeywordExtractor:
    """Extract structured keywords from job descriptions using AI."""

    def __init__(self, client: AIClient) -> None:
        self._client = client

    @staticmethod
    def _preview(text: str, limit: int = 500) -> str:
        compact = " ".join(text.split())
        return compact[:limit]

    async def extract(self, job_description: str) -> dict[str, Any]:
        """Extract skills and requirements from a job description."""
        if not self._client.is_available:
            logger.warning("AI client not available - returning empty keywords")
            return {"hard_skills": [], "soft_skills": [], "years_required": 0}

        logger.info("JD length: %d", len(job_description or ""))
        logger.info("JD preview: %s", self._preview(job_description or ""))

        try:
            raw_response = await self._client.chat(
                system_prompt=_EXTRACTOR_SYSTEM_PROMPT,
                user_prompt=f"JD: {job_description}",
                temperature=0.1,
                max_tokens=1000,
            )
            logger.info("Raw skills response: %s", self._preview(raw_response, limit=1200))

            cleaned = raw_response.strip()
            if cleaned.startswith("```"):
                first_nl = cleaned.find("\n")
                if first_nl != -1:
                    cleaned = cleaned[first_nl + 1 :]
                else:
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3].strip()
                elif "```" in cleaned:
                    cleaned = cleaned[: cleaned.rindex("```")].strip()

            result = json.loads(cleaned)
            result.setdefault("hard_skills", [])
            result.setdefault("soft_skills", [])
            result.setdefault("years_required", 0)
            logger.info("Parsed skills: %s", result.get("hard_skills", []))
            return result
        except Exception:
            logger.exception("Keyword extraction failed")
            return {"hard_skills": [], "soft_skills": [], "years_required": 0}
