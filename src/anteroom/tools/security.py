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
            r"\brm\s+(-{1,2}[a-zA-Z][-a-zA-Z]*\s+)*-[a-zA-Z]*f[a-zA-Z]*\s+(-{1,2}[a-zA-Z][-a-zA-Z]*\s+)*-[a-zA-Z]*r|"
            r"\brm\s+(-{1,2}[a-zA-Z][-a-zA-Z]*\s+)*-[a-zA-Z]*r[a-zA-Z]*\s+(-{1,2}[a-zA-Z][-a-zA-Z]*\s+)*-[a-zA-Z]*f|"
            r"\brm\s+(-{1,2}[a-zA-Z][-a-zA-Z]*\s+)*-[a-zA-Z]*rf[a-zA-Z]*\b|"
            r"\brm\s+(-{1,2}[a-zA-Z][-a-zA-Z]*\s+)*-[a-zA-Z]*fr[a-zA-Z]*\b",
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


# Network command patterns (Unix + PowerShell + cmd.exe)
_NETWORK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(curl|wget|nc|ncat|socat|nmap)\b", re.IGNORECASE), "network tool"),
    (re.compile(r"\b(ssh|scp|rsync|sftp|ftp|telnet)\b", re.IGNORECASE), "remote connection"),
    (re.compile(r"\b(Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b", re.IGNORECASE), "PowerShell network"),
    (re.compile(r"\bStart-BitsTransfer\b", re.IGNORECASE), "PowerShell download"),
    (re.compile(r"\bNet\.WebClient\b", re.IGNORECASE), ".NET web client"),
    (re.compile(r"\b(nslookup|dig)\b", re.IGNORECASE), "DNS lookup"),
]

# Package manager patterns (cross-platform)
_PACKAGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(pip|pip3)\s+install\b", re.IGNORECASE), "pip install"),
    (re.compile(r"\b(npm|yarn|pnpm)\s+(install|add|i)\b", re.IGNORECASE), "Node package install"),
    (re.compile(r"\bgem\s+install\b", re.IGNORECASE), "gem install"),
    (re.compile(r"\bcargo\s+install\b", re.IGNORECASE), "cargo install"),
    (re.compile(r"\bgo\s+install\b", re.IGNORECASE), "go install"),
    (re.compile(r"\b(apt|apt-get|yum|dnf|pacman|zypper)\s+install\b", re.IGNORECASE), "system package install"),
    (re.compile(r"\bbrew\s+install\b", re.IGNORECASE), "brew install"),
    (re.compile(r"\b(choco|winget|scoop)\s+install\b", re.IGNORECASE), "Windows package install"),
    (re.compile(r"\bconda\s+install\b", re.IGNORECASE), "conda install"),
]


def check_network_command(command: str) -> str | None:
    """Check if a command uses network tools. Returns description if matched."""
    if not command or not command.strip():
        return None
    normalized = _normalize_whitespace(command)
    for pattern, description in _NETWORK_PATTERNS:
        if pattern.search(normalized):
            return description
    return None


def check_package_install(command: str) -> str | None:
    """Check if a command installs packages. Returns description if matched."""
    if not command or not command.strip():
        return None
    normalized = _normalize_whitespace(command)
    for pattern, description in _PACKAGE_PATTERNS:
        if pattern.search(normalized):
            return description
    return None


def check_blocked_path(command: str, blocked_paths: list[str]) -> str | None:
    """Check if a command references any blocked path.

    Normalizes both forward and backslashes for cross-platform matching.
    """
    if not command or not blocked_paths:
        return None
    # Normalize the command: replace backslashes and lowercase for case-insensitive path matching
    cmd_lower = command.replace("\\", "/").lower()
    for blocked in blocked_paths:
        normalized_blocked = blocked.replace("\\", "/").lower().rstrip("/")
        if normalized_blocked and normalized_blocked in cmd_lower:
            return f"path restricted: {blocked}"
    return None


def check_custom_patterns(command: str, patterns: list[str]) -> str | None:
    """Check command against user-defined regex patterns. Returns description if matched."""
    if not command or not patterns:
        return None
    normalized = _normalize_whitespace(command)
    for pattern_str in patterns:
        try:
            if re.search(pattern_str, normalized, re.IGNORECASE):
                return f"custom pattern: {pattern_str}"
        except re.error:
            logger.warning("Invalid custom bash pattern, skipping: %s", pattern_str)
    return None


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
        logger.warning("Hard-block fallback triggered (%s): %s", description, command[:100])
        return "", f"Blocked: {description}"

    return command, None
