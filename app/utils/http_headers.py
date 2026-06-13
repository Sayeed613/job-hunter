"""Realistic HTTP request headers that mimic a real Chrome browser on Windows.

All HTTP-based job providers should use these headers to avoid 403 blocks.
Using bare "User-Agent: Mozilla/5.0" is the fastest way to get detected.
"""

from __future__ import annotations

# ── Realistic Chrome 124 on Windows headers ──────────────────
_CHROME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Dnt": "1",
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
}

# ── API-specific headers (for JSON endpoints) ────────────────
_API_HEADERS = {
    "User-Agent": _CHROME_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": _CHROME_HEADERS["Sec-Ch-Ua"],
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Dnt": "1",
    "Connection": "keep-alive",
}


def browser_headers(referer: str | None = None) -> dict[str, str]:
    """Return realistic Chrome browser headers.

    Args:
        referer: Optional Referer header value (some sites check this).

    Returns:
        Dict of HTTP headers.
    """
    headers = dict(_CHROME_HEADERS)
    if referer:
        headers["Referer"] = referer
    return headers


def api_headers(referer: str | None = None) -> dict[str, str]:
    """Return realistic Chrome API/JSON headers.

    Args:
        referer: Optional Referer header value.

    Returns:
        Dict of HTTP headers.
    """
    headers = dict(_API_HEADERS)
    if referer:
        headers["Referer"] = referer
    return headers
