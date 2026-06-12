"""Portfolio scraper — downloads HTML and extracts project cards."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from app.portfolio.models import PortfolioProject

logger = logging.getLogger("headhunter")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class PortfolioScraper:
    """Downloads a portfolio website and extracts project information.

    Uses heuristics to identify project cards / sections in the HTML:
    - Elements with class names containing ``project``, ``card``, ``work``,
      ``portfolio-item``
    - Common heading patterns for project titles
    - Anchor tags pointing to GitHub or external demo URLs
    """

    def __init__(self, timeout: int = 30) -> None:
        """Initialise the scraper.

        Args:
            timeout: HTTP request timeout in seconds (default 30).
        """
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _USER_AGENT})

    # ── Public API ───────────────────────────────────────────

    def scrape(self, url: str) -> list[PortfolioProject]:
        """Download and parse a portfolio website for projects.

        Args:
            url: The full URL of the portfolio website.

        Returns:
            A list of :class:`PortfolioProject` instances extracted from
            the page.  Returns an empty list if the page is unreachable
            or contains no identifiable projects.
        """
        html = self._fetch(url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        projects = self._extract_projects(soup, url)

        logger.info(
            "Portfolio scraped",
            extra={"url": url, "projects_found": len(projects)},
        )

        return projects

    # ── HTTP fetch ───────────────────────────────────────────

    def _fetch(self, url: str) -> str:
        """Download the page content.

        Returns:
            The HTML string, or an empty string on failure.
        """
        try:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            logger.exception("Failed to fetch portfolio URL %s", url)
            return ""

    # ── Project extraction ───────────────────────────────────

    @staticmethod
    def _extract_projects(soup: BeautifulSoup, base_url: str) -> list[PortfolioProject]:
        """Extract project entries from the parsed HTML."""
        projects: list[PortfolioProject] = []
        seen_names: set[str] = set()

        # 1. Look for project-card elements (common CSS class patterns).
        cards = soup.find_all(
            True,  # any tag
            class_=re.compile(
                r"project|portfolio[.]item|card|work[.]item|grid[.]item",
                re.IGNORECASE,
            ),
        )

        for card in cards:
            project = PortfolioScraper._parse_card(card, base_url)
            if project.name and project.name not in seen_names:
                projects.append(project)
                seen_names.add(project.name)

        # 2. Fallback: look for sections with project-related headings.
        if not projects:
            for heading in soup.find_all(["h2", "h3", "h4"]):
                text = heading.get_text(strip=True)
                if re.match(
                    r"project|work|portfolio|my work|featured",
                    text,
                    re.IGNORECASE,
                ):
                    parent = heading.find_parent(["section", "div", "main"])
                    if parent:
                        for sibling in parent.find_all(
                            ["div", "article", "li"],
                            recursive=False,
                        ):
                            project = PortfolioScraper._parse_card(
                                sibling, base_url,
                            )
                            if (
                                project.name
                                and project.name not in seen_names
                            ):
                                projects.append(project)
                                seen_names.add(project.name)

        # 3. Last resort: any heading followed by a paragraph.
        if not projects:
            for heading in soup.find_all(["h2", "h3", "h4"]):
                name = heading.get_text(strip=True)
                if not name or len(name) > 100:
                    continue
                desc_tag = heading.find_next_sibling(["p", "div"])
                desc = desc_tag.get_text(strip=True) if desc_tag else ""
                if name not in seen_names:
                    projects.append(
                        PortfolioProject(
                            name=name,
                            description=desc,
                        ),
                    )
                    seen_names.add(name)

        return projects

    # ── Card parser ──────────────────────────────────────────

    @staticmethod
    def _parse_card(card: Any, base_url: str) -> PortfolioProject:
        """Parse a single HTML element as a project card."""
        # Name: heading within the card, or aria-label, or first strong element.
        name = ""
        heading = card.find(["h2", "h3", "h4", "h5", "strong"])
        if heading:
            name = heading.get_text(strip=True)
        if not name:
            name = card.get("aria-label", "")
        if not name:
            name = card.get_text(strip=True)[:80]

        # Description: paragraph or div after the heading.
        desc = ""
        if heading:
            desc_tag = heading.find_next_sibling(["p", "div", "span"])
            if desc_tag:
                desc = desc_tag.get_text(strip=True)
        if not desc:
            desc = card.get("title", "")

        # Project URL: first anchor in the card.
        project_url = ""
        github_url = ""
        for anchor in card.find_all("a", href=True):
            href = anchor["href"]
            if "github.com" in href and not github_url:
                github_url = href if href.startswith("http") else base_url.rstrip("/") + href
            elif not project_url:
                project_url = href if href.startswith("http") else base_url.rstrip("/") + href

        return PortfolioProject(
            name=name.strip(),
            description=desc[:500],
            project_url=project_url,
            github_url=github_url,
        )
