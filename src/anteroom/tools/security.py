"""Security utilities for built-in tools.

Implements path validation and input sanitization per OWASP ASVS V5.

IMPORTANT: sanitize_command() is the LAST LINE OF DEFENSE. It hard-blocks
catastrophic commands at the handler level, regardless of approval mode,
allowed_tools, session permissions, or any other config. It cannot be
bypassed by any user configuration. The safety.py pattern detection and
tier-based approval system are the primary gates; this is the nuclear option.
"""

from __future__ import annotations

import logging
import os
import re

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


# Hard-block patterns: catastrophic commands that should NEVER execute
# regardless of approval mode, allowed_tools, or session permissions.
# These are the "CNN headline" commands — mass destruction, fork bombs,
# disk wipes. Less catastrophic but still dangerous commands (git push --force,
# drop table) are handled by the approval prompt in safety.py.
_HARD_BLOCK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Mass recursive deletion
    (
        re.compile(
            r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-[a-zA-Z]*r|"
            r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?-[a-zA-Z]*f",
            re.IGNORECASE,
        ),
        "recursive forced deletion (rm -rf)",
    ),
    # Disk formatting / wiping
    (re.compile(r"\bmkfs\b", re.IGNORECASE), "disk formatting (mkfs)"),
    (re.compile(r"\bdd\b.*\bif=/dev/(zero|urandom|random)\b", re.IGNORECASE), "disk overwrite (dd)"),
    # Fork bombs
    (re.compile(r":\(\)\s*\{.*\|.*&\s*\}\s*;"), "fork bomb"),
    (re.compile(r"\bfork\s*bomb\b", re.IGNORECASE), "fork bomb"),
    # chmod 777 on root or home
    (re.compile(r"\bchmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+)?777\s+/\s*$"), "recursive chmod 777 /"),
    # Pipe to shell from network (curl | sh, wget | bash, etc.)
    (re.compile(r"\b(curl|wget)\b.*\|\s*(ba)?sh\b"), "pipe from network to shell"),
    (re.compile(r"\b(curl|wget)\b.*\|\s*sudo\b"), "pipe from network to sudo"),
    # Direct eval/exec of base64 (common evasion technique)
    (re.compile(r"\bbase64\b.*\|\s*(ba)?sh\b"), "base64 decode piped to shell"),
    (re.compile(r"\bbase64\b.*\|\s*sudo\b"), "base64 decode piped to sudo"),
    # Python/perl/ruby one-liner evasion
    (
        re.compile(r"\b(python|python3|perl|ruby)\s+-[a-zA-Z]*e\s+.*\bos\.(system|popen|exec)\b"),
        "scripted shell escape",
    ),
    # sudo rm
    (re.compile(r"\bsudo\s+rm\b"), "sudo rm"),
]


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace to single spaces for pattern matching."""
    return re.sub(r"\s+", " ", text.strip())


def sanitize_command(command: str) -> tuple[str, str | None]:
    """Hard-block validation for shell commands.

    Returns (command, error_message).
    This is the last line of defense — it runs at the handler level AFTER
    all approval checks. It cannot be bypassed by any configuration.
    Only blocks catastrophic patterns; less dangerous commands are gated
    by the approval system in safety.py/tiers.py.
    """
    # Reject null bytes
    if "\x00" in command:
        return "", "Command contains null bytes"

    if not command.strip():
        return command, None

    normalized = _normalize_whitespace(command)

    for pattern, description in _HARD_BLOCK_PATTERNS:
        if pattern.search(normalized):
            logger.warning("HARD BLOCKED dangerous command (%s): %s", description, command[:100])
            return "", f"Blocked: {description}"

    return command, None
