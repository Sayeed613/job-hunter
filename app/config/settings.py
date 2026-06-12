"""Central configuration for Project Headhunter.

Loads settings from environment variables with sensible fallback defaults.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Values are sourced automatically from a ``.env`` file (if present) and
    the process environment.  Every field has a safe default so the
    application can start without any configuration file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        frozen=True,
    )

    # ── Application ──────────────────────────────────────────
    app_name: str = "Project Headhunter"
    environment: str = "development"
    log_level: str = "INFO"
    debug: bool = False

    # ── Firebase ─────────────────────────────────────────────
    firebase_credentials_path: str = ""
    firebase_database_url: str = ""
    firebase_project_id: str = ""

    # ── AI / LLM ────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    opencode_api_key: str = ""
    opencode_model: str = "gpt-4o-mini"
    opencode_base_url: str = "https://api.openai.com/v1"

    # ── GitHub ────────────────────────────────────────────────
    github_token: str = ""

    # ── LinkedIn (Browser Automation) ─────────────────────────
    linkedin_email: str = ""
    linkedin_password: str = ""
    linkedin_url: str = ""

    # ── Browser Automation ────────────────────────────────────
    browser_headless: bool = True
    browser_timeout_ms: int = 30_000

    # ── Telegram ──────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Job Applier API Keys ────────────────────────────────────
    greenhouse_api_key: str = ""
    lever_api_key: str = ""
    ashby_api_key: str = ""

    # ── SMTP (Email Applier) ─────────────────────────────────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""

    # ── Auto-Apply Configuration ─────────────────────────────
    auto_apply_enabled: bool = True
    auto_apply_max_per_cycle: int = 5

    # ── Job Search / Scraping ────────────────────────────────
    default_search_location: str = "remote"
    default_search_radius_km: int = 50
    job_fetch_interval_minutes: int = 60
