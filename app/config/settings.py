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

    # ── Telegram ──────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Job Search / Scraping ────────────────────────────────
    default_search_location: str = "remote"
    default_search_radius_km: int = 50
    job_fetch_interval_minutes: int = 60
