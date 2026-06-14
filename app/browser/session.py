"""Per-platform login session manager.

Manages Playwright storage_state files for platforms that require login:
LinkedIn, Wellfound, Work at a Startup (YC), Naukri.

Each platform gets its own storage_state JSON file saved to disk.
The first run prompts manual login via --relogin. Subsequent runs
load the saved session automatically.

Usage:
    session = LoginSession()
    if not session.has_session("linkedin"):
        # Run --relogin flow
        ...
    context_kwargs["storage_state"] = session.get_path("linkedin")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.config.settings import Settings

logger = logging.getLogger("job_automation_bot")

# ── Platform → Settings field mapping ───────────────────────
_PLATFORM_SESSION_FIELDS: dict[str, str] = {
    "linkedin": "linkedin_session_path",
    "wellfound": "wellfound_session_path",
    "workatastartup": "workatastartup_session_path",
    "naukri": "naukri_session_path",
}

# ── Platform credentials mapping ────────────────────────────
# Maps platform to (email_setting_field, password_setting_field)
# All platforms use the same LinkedIn credentials (email + password)
# The user signs up/in on all these platforms with the same email.
_PLATFORM_CREDENTIALS: dict[str, tuple[str, str]] = {
    "linkedin": ("linkedin_email", "linkedin_password"),
    "wellfound": ("linkedin_email", "linkedin_password"),
    "workatastartup": ("linkedin_email", "linkedin_password"),
    "naukri": ("linkedin_email", "linkedin_password"),
}

# ── Platform login URLs (for --relogin) ─────────────────────
_PLATFORM_LOGIN_URLS: dict[str, str] = {
    "linkedin": "https://www.linkedin.com/login",
    "wellfound": "https://wellfound.com/login",
    "workatastartup": "https://www.workatastartup.com/users",
    "naukri": "https://www.naukri.com/nlogin/login",
}

# ── Post-login success indicators ───────────────────────────
_PLATFORM_SUCCESS_URL_PATTERNS: dict[str, list[str]] = {
    "linkedin": ["/feed", "/mynetwork", "/jobs", "/checkpoint/challenge"],
    "wellfound": ["/jobs", "/dashboard", "/companies"],
    "workatastartup": ["/jobs", "/companies", "/startups"],
    "naukri": ["/mnjuser/homepage", "/jobs", "/profile"],
}


class LoginSession:
    """Manages per-platform Playwright storage_state sessions.

    Each platform's session is saved to a separate JSON file in the
    ``storage/`` directory (gitignored). The session manager provides
    utility methods for checking, loading, and saving sessions.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or Settings()

    # ── Session path helpers ─────────────────────────────────

    def get_path(self, platform: str) -> Path:
        """Return the Path to the session file for *platform*.

        Args:
            platform: One of "linkedin", "wellfound", "workatastartup",
                      "naukri".

        Returns:
            Path to the storage_state JSON file.
        """
        field = _PLATFORM_SESSION_FIELDS.get(platform)
        if not field:
            raise ValueError(f"Unknown platform: {platform}. Valid: {list(_PLATFORM_SESSION_FIELDS)}")
        path_str = getattr(self._settings, field, "")
        if not path_str:
            path_str = f"storage/{platform}_state.json"
        return Path(path_str)

    def has_session(self, platform: str) -> bool:
        """Check if a saved session exists for *platform*."""
        return self.get_path(platform).exists()

    def get_login_url(self, platform: str) -> str:
        """Return the login URL for *platform*.

        Args:
            platform: Platform name.

        Returns:
            Login URL string.
        """
        return _PLATFORM_LOGIN_URLS.get(platform, "")

    def get_success_patterns(self, platform: str) -> list[str]:
        """Return URL patterns that indicate successful login for *platform*."""
        return _PLATFORM_SUCCESS_URL_PATTERNS.get(platform, [])

    def get_credentials(self, platform: str) -> tuple[str, str]:
        """Return (email, password) for *platform* if configured.

        Some platforms (LinkedIn) support auto-login with stored credentials.
        Others (Wellfound, WorkAtAStartup, Naukri) require manual login.

        Returns:
            Tuple of (email, password). Both empty strings if not configured.
        """
        fields = _PLATFORM_CREDENTIALS.get(platform, ("", ""))
        if not fields[0]:
            return ("", "")
        email = getattr(self._settings, fields[0], "")
        password = getattr(self._settings, fields[1], "")
        return (email, password)

    def save_session(self, platform: str, storage_state: dict) -> None:
        """Save a storage_state dict to the platform's session file.

        Args:
            platform: Platform name.
            storage_state: The Playwright context.storage_state() output.
        """
        path = self.get_path(platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text(json.dumps(storage_state, indent=2), encoding="utf-8")
        logger.info("Login session saved for %s → %s", platform, path)

    def load_session(self, platform: str) -> Optional[dict]:
        """Load the storage_state dict for *platform*.

        Returns:
            The parsed storage_state dict, or None if the file doesn't exist.
        """
        path = self.get_path(platform)
        if not path.exists():
            return None
        import json
        return json.loads(path.read_text(encoding="utf-8"))

    def delete_session(self, platform: str) -> None:
        """Delete the saved session for *platform* (e.g. after expiry)."""
        path = self.get_path(platform)
        if path.exists():
            path.unlink()
            logger.info("Deleted stale session for %s → %s", platform, path)

    def list_platforms(self) -> list[str]:
        """Return all supported platforms."""
        return list(_PLATFORM_SESSION_FIELDS.keys())

    def list_available_sessions(self) -> list[str]:
        """Return platforms that have saved sessions."""
        return [p for p in self.list_platforms() if self.has_session(p)]

    @classmethod
    async def save_context_state(cls, context, platform: str) -> Path:
        """Save the current browser context's storage_state for *platform*.

        Convenience method — call this after a successful manual login.

        Args:
            context: Playwright BrowserContext.
            platform: Platform name.

        Returns:
            Path to the saved session file.
        """
        mgr = cls()
        state = await context.storage_state()
        mgr.save_session(platform, state)
        return mgr.get_path(platform)
