"""Token provider: obtain and refresh API keys via external commands."""

from __future__ import annotations

import logging
import shlex
import subprocess

logger = logging.getLogger(__name__)


class TokenProviderError(Exception):
    """Raised when the api_key_command fails."""


class TokenProvider:
    """Runs an external command to obtain an API key, with caching and refresh.

    Usage::

        provider = TokenProvider(command="python get_token.py")
        key = provider.get_token()     # runs command on first call
        key = provider.get_token()     # returns cached value
        key = provider.refresh()       # forces re-run, returns new key
    """

    def __init__(self, command: str) -> None:
        self._command = command
        self._cached_token: str | None = None

    @property
    def command(self) -> str:
        return self._command

    def get_token(self) -> str:
        """Return cached token, or fetch a new one if no cache exists."""
        if self._cached_token:
            return self._cached_token
        return self.refresh()

    def refresh(self) -> str:
        """Execute the command and return a fresh token."""
        logger.info("Running api_key_command to obtain token")
        try:
            result = subprocess.run(
                shlex.split(self._command),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError as e:
            raise TokenProviderError(f"api_key_command not found: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise TokenProviderError("api_key_command timed out after 30s") from e

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise TokenProviderError(
                f"api_key_command exited with code {result.returncode}" + (f": {stderr}" if stderr else "")
            )

        token = result.stdout.strip()
        if not token:
            raise TokenProviderError("api_key_command returned empty output")

        self._cached_token = token
        logger.info("Token obtained successfully")
        return token

    def clear_cache(self) -> None:
        """Clear the cached token."""
        self._cached_token = None
