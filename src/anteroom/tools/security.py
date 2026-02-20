"""Security utilities for built-in tools.

Implements path validation and input sanitization per OWASP ASVS V5.

Hard-block patterns (catastrophic commands like rm -rf, fork bombs, disk
wipes) are checked BEFORE the approval prompt via check_hard_block().
In interactive mode, the user sees an escalated warning and can choose
to proceed. In auto mode (no approval channel), hard-blocked commands
are silently blocked as a safety net. sanitize_command() remains as the
last line of defense at the handler level for any code path that doesn't
go through call_tool().
"""

from __future__ import annotations

import logging
import os
import re

from .path_utils import safe_resolve

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
        resolved = safe_resolve(path)
    else:
        resolved = safe_resolve(os.path.join(working_dir, path))

    # Check blocked paths (also check the resolved form of blocked entries for symlinks)
    for blocked in _BLOCKED_PATHS:
        blocked_real = safe_resolve(blocked)
        if resolved == blocked or resolved == blocked_real:
            logger.warning("Blocked access to sensitive path: %s", resolved)
            return "", f"Access denied: {path}"

    for prefix in _BLOCKED_PREFIXES:
        prefix_real = safe_resolve(prefix)
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
    # Python/perl/ruby one-liner evasion (covers both -e and -c flags)
    (
        re.compile(r"\b(python|python3|perl|ruby)\s+-[a-zA-Z]*[ec]\s+.*\bos\.(system|popen|exec)\b"),
        "scripted shell escape",
    ),
    (
        re.compile(r"\b(python|python3|perl|ruby)\s+-[a-zA-Z]*[ec]\s+.*\b(subprocess|__import__)\b"),
        "scripted shell escape",
    ),
    # Secure-erase commands (shred/srm are always destructive; wipe requires flags to reduce false positives)
    (re.compile(r"\b(shred|srm)\b", re.IGNORECASE), "secure file erasure"),
    (re.compile(r"\bwipe\s+-", re.IGNORECASE), "secure file erasure (wipe)"),
    # File zeroing via truncate (-s 0, --size=0, --size 0)
    (
        re.compile(r"\btruncate\s+(-s\s*0|--size[= ]0)\b", re.IGNORECASE),
        "file zeroing (truncate -s 0)",
    ),
    # sudo rm
    (re.compile(r"\bsudo\s+rm\b"), "sudo rm"),
]


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace to single spaces for pattern matching."""
    return re.sub(r"\s+", " ", text.strip())


def check_hard_block(command: str) -> str | None:
    """Check whether a command matches a hard-block pattern.

    Returns the pattern description if matched, None otherwise.
    This is a pure check — no side effects, no logging.
    """
    if not command or not command.strip():
        return None

    normalized = _normalize_whitespace(command)

    for pattern, description in _HARD_BLOCK_PATTERNS:
        if pattern.search(normalized):
            return description

    return None


def sanitize_command(command: str) -> tuple[str, str | None]:
    """Hard-block validation for shell commands.

    Returns (command, error_message).
    This is the last line of defense — it runs at the handler level AFTER
    all approval checks. It cannot be bypassed by any configuration
    unless the caller explicitly opted out via the _bypass_hard_block flag
    (only set by call_tool() after explicit user approval).
    Only blocks catastrophic patterns; less dangerous commands are gated
    by the approval system in safety.py/tiers.py.
    """
    # Reject null bytes
    if "\x00" in command:
        return "", "Command contains null bytes"

    description = check_hard_block(command)
    if description:
        logger.info("Hard-block pattern matched (%s): %s", description, command[:100])
        return "", f"Blocked: {description}"

    return command, None
