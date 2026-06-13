"""Central configuration for the Job Automation Bot.

Loads settings from environment variables with sensible fallback defaults.
Uses pydantic-settings which automatically reads from .env file.
"""

from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        frozen=True,
    )

    # ── Application ──────────────────────────────────────────
    app_name: str = "Job Automation Bot"
    environment: str = "development"
    log_level: str = "INFO"
    debug: bool = False

    # ── OpenAI / LLM ─────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_temperature: float = 0.7
    openai_max_tokens: int = 2000

    # ── Telegram ─────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Firebase ─────────────────────────────────────────────
    firebase_credentials_path: str = ""
    firebase_project_id: str = ""

    # ── LinkedIn (Browser Automation) ────────────────────────
    linkedin_email: str = ""
    linkedin_password: str = ""

    # ── Job Search / Filtering ───────────────────────────────
    job_keywords: str = "React,Next.js,Python,FastAPI,Node.js,TypeScript,Full-Stack,Backend"
    locations: str = "Bangalore,Remote India,Remote Global,Hybrid Bangalore"
    excluded_companies: str = ""
    min_experience: int = 0
    max_experience: int = 6
    run_interval_hours: int = 2
    max_applications_per_cycle: int = 15
    max_job_age_hours: int = 48

    # ── Browser ──────────────────────────────────────────────
    headless: bool = True
    browser_timeout_ms: int = 30_000

    # ── Browser session ──────────────────────────────────────
    session_state_path: str = "secrets/browser_session.json"

    # ── Paths ────────────────────────────────────────────────
    base_resume_path: str = "Sayeed_Frontend_Developer.docx"
