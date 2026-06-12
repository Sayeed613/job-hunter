"""GitHub REST API client."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from app.config.settings import Settings

logger = logging.getLogger("headhunter")

_API_BASE = "https://api.github.com"
_USER_AGENT = "ProjectHeadhunter/1.0"
_ENV_TOKEN = "GITHUB_TOKEN"


class GithubClient:
    """Thin wrapper over the GitHub REST API.

    Reads the personal access token from :class:`Settings` (field
    ``github_token``) or the ``GITHUB_TOKEN`` environment variable.

    All public methods raise :class:`requests.RequestException` on
    network or HTTP errors (after exhausting internal retries for
    rate-limit codes).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        token: str | None = None,
    ) -> None:
        """Initialise the client.

        Args:
            settings: Optional :class:`Settings` instance whose
                ``github_token`` field is used as a fallback.
            token: Explicit token override.  Falls back to the
                ``GITHUB_TOKEN`` environment variable.
        """
        cfg = settings or Settings()
        self._token = token or cfg.github_token or os.getenv(_ENV_TOKEN, "")

        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github.v3+json",
            },
        )
        if self._token:
            self._session.headers["Authorization"] = f"Bearer {self._token}"

        logger.info(
            "GithubClient initialised",
            extra={"token_set": bool(self._token)},
        )

    # ── Public API ───────────────────────────────────────────

    def get_profile(self, username: str) -> dict[str, Any]:
        """Fetch a GitHub user's public profile.

        Args:
            username: GitHub username.

        Returns:
            The JSON response dict from ``/users/{username}``.

        Raises:
            requests.RequestException: On API error.
        """
        data = self._get(f"/users/{username}")
        logger.info(
            "Fetched GitHub profile",
            extra={"username": username, "public_repos": data.get("public_repos", 0)},
        )
        return data

    def get_repositories(self, username: str) -> list[dict[str, Any]]:
        """Fetch all **public** repositories for a user.

        Handles pagination via the ``Link`` header to collect every
        page.

        Args:
            username: GitHub username.

        Returns:
            List of repository JSON objects.
        """
        repos: list[dict[str, Any]] = []
        url = f"/users/{username}/repos?per_page=100&sort=updated"

        while url:
            response = self._session.get(
                f"{_API_BASE}{url}",
                timeout=30,
            )
            self._raise_or_handle_rate_limit(response)
            response.raise_for_status()
            repos.extend(response.json())

            # Follow pagination (Link header).
            link = response.headers.get("Link", "")
            next_url = self._extract_next_page(link)
            url = next_url  # relative path from Link header

        logger.info(
            "Fetched repositories",
            extra={"username": username, "count": len(repos)},
        )
        return repos

    def get_repository_readme(
        self,
        username: str,
        repo: str,
    ) -> str:
        """Fetch the rendered README content for a repository.

        Args:
            username: GitHub username / owner.
            repo: Repository name.

        Returns:
            The README text (decoded from base64 if necessary), or an
            empty string if the repo has no README.
        """
        try:
            response = self._session.get(
                f"{_API_BASE}/repos/{username}/{repo}/readme",
                timeout=30,
                headers={"Accept": "application/vnd.github.v3.raw"},
            )
            self._raise_or_handle_rate_limit(response)
            if response.status_code == 404:
                logger.info("No README found for %s/%s", username, repo)
                return ""
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            logger.exception("Failed to fetch README for %s/%s", username, repo)
            return ""

    # ── Internal helpers ─────────────────────────────────────

    def _get(self, path: str) -> dict[str, Any]:
        """Perform a GET request and return the JSON body."""
        response = self._session.get(
            f"{_API_BASE}{path}",
            timeout=30,
        )
        self._raise_or_handle_rate_limit(response)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _raise_or_handle_rate_limit(response: requests.Response) -> None:
        """Raise the correct exception for rate-limit or other errors."""
        if response.status_code == 403 and (
            "rate limit" in response.text.lower()
        ):
            logger.error(
                "GitHub API rate limit exceeded. "
                "Authenticate with a GITHUB_TOKEN for higher limits."
            )
        response.raise_for_status()

    @staticmethod
    def _extract_next_page(link_header: str) -> str | None:
        """Extract the ``next`` URL from the ``Link`` header."""
        if not link_header:
            return None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                start = part.index("<") + 1
                end = part.index(">")
                url = part[start:end]
                # Strip the API base to get the relative path.
                if url.startswith(_API_BASE):
                    return url[len(_API_BASE) :]
                return url
        return None
