"""Security utilities for built-in tools.

Implements path validation and input sanitization per OWASP ASVS V5.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Paths that should never be accessible via tools
_BLOCKED_PATHS = {
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
}

_BLOCKED_PREFIXES = (
    "/proc/",
    "/sys/",
    "/dev/",
)


def validate_path(path: str, working_dir: str) -> tuple[str, str | None]:
    """Validate and resolve a file path.

    Returns (resolved_path, error_message).
    If error_message is not None, the path is invalid.
    """
    # Reject null bytes (path traversal via null byte injection)
    if "\x00" in path:
        return "", "Path contains null bytes"

    # Resolve relative to working dir
    if os.path.isabs(path):
        resolved = os.path.realpath(path)
    else:
        resolved = os.path.realpath(os.path.join(working_dir, path))

    # Check blocked paths (also check the realpath of blocked entries for symlinks)
    for blocked in _BLOCKED_PATHS:
        blocked_real = os.path.realpath(blocked)
        if resolved == blocked or resolved == blocked_real:
            logger.warning("Blocked access to sensitive path: %s", resolved)
            return "", f"Access denied: {path}"

    for prefix in _BLOCKED_PREFIXES:
        prefix_real = os.path.realpath(prefix)
        if resolved.startswith(prefix) or resolved.startswith(prefix_real):
            logger.warning("Blocked access to system path: %s", resolved)
            return "", f"Access denied: {path}"

    return resolved, None


def sanitize_command(command: str) -> tuple[str, str | None]:
    """Basic validation for shell commands.

    Returns (command, error_message).
    We don't heavily restrict commands since this is an agentic coding tool,
    but we block the most dangerous patterns.
    """
    # Reject null bytes
    if "\x00" in command:
        return "", "Command contains null bytes"

    # Block destructive system-level commands
    stripped = command.strip().split()[0] if command.strip() else ""
    blocked_commands = {"rm -rf /", "mkfs", "dd if=/dev/zero", ":(){:|:&};:"}
    for blocked in blocked_commands:
        if command.strip().startswith(blocked):
            logger.warning("Blocked dangerous command: %s", command[:50])
            return "", f"Blocked: {stripped} is not allowed"

    return command, None
