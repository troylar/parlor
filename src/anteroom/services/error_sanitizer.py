"""Shared sanitization for provider error messages.

Extracts human-readable text from LLM provider error responses and
strips URLs, API keys, and structured payloads before surfacing to users.
Fails closed: returns a generic fallback if nothing safe remains.
"""

from __future__ import annotations

import re

_URL_PATTERN = re.compile(r"https?://\S+")
_API_KEY_PATTERN = re.compile(r"(sk-|key-|token-|Bearer\s+)[a-zA-Z0-9_\-]{8,}")
_MAX_LENGTH = 200
_GENERIC_FALLBACK = "AI request error"


def sanitize_provider_error(raw: str, *, fallback: str = _GENERIC_FALLBACK) -> str:
    """Sanitize a provider error message for user display.

    Returns a human-readable string safe to show to the end user.
    Falls back to *fallback* if the input is empty, looks like raw
    JSON/HTML, or contains nothing useful after stripping.
    """
    if not raw or not raw.strip():
        return fallback

    text = raw.strip()

    # Reject structured payloads that aren't human-readable
    if text.startswith("{") or text.startswith("["):
        return fallback
    if text.startswith("<"):
        return fallback

    # Strip URLs and API key patterns
    text = _URL_PATTERN.sub("", text)
    text = _API_KEY_PATTERN.sub("[REDACTED]", text)

    # Collapse whitespace left by removals
    text = re.sub(r"\s{2,}", " ", text).strip()

    if not text:
        return fallback

    # Truncate
    if len(text) > _MAX_LENGTH:
        text = text[:_MAX_LENGTH] + "..."

    return text
