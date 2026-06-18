"""Helpers for classifying network-related failures."""

from __future__ import annotations


def is_network_restricted_error(error: BaseException) -> bool:
    """Return True when an exception looks like blocked outbound network access."""
    text = str(error).lower()
    markers = (
        "network_access_denied",
        "err_network_access_denied",
        "failed to establish a new connection",
        "cannot connect to host",
        "access is denied",
        "forbidden by its access permissions",
        "permissionerror",
        "connection error",
        "connection refused",
        "max retries exceeded",
    )
    return any(marker in text for marker in markers)


def network_error_summary(error: BaseException) -> str:
    """Return a compact log-friendly summary for a network failure."""
    text = " ".join(str(error).split())
    if not text:
        return error.__class__.__name__
    return text[:240]
