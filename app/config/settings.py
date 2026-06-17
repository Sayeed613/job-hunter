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
        extra="ignore",
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

    # ── Telegram (banned in some regions — optional) ──────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── WhatsApp (Twilio) ────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""
    whatsapp_number: str = ""

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
    max_applications_per_cycle: int = 30
    max_job_age_hours: int = 72
    # Minimum match score to accept a job (lower = more aggressive)
    min_match_score: float = 0.02
    # Minimum AI-matched skills to accept a job
    min_ai_skills: int = 1

    # ── Browser ──────────────────────────────────────────────
    headless: bool = True
    browser_timeout_ms: int = 30_000

    # ── Browser session ──────────────────────────────────────
    session_state_path: str = "storage/browser_session.json"

    # ── Per-platform login sessions (storage_state files) ────
    linkedin_session_path: str = "storage/linkedin_state.json"
    wellfound_session_path: str = "storage/wellfound_state.json"
    workatastartup_session_path: str = "storage/workatastartup_state.json"
    naukri_session_path: str = "storage/naukri_state.json"

    # ── Paths ────────────────────────────────────────────────
    base_resume_path: str = "Sayeed_Frontend_Developer.docx"
