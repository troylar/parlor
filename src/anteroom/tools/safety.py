"""Safety detection logic for destructive operations.

Pure functions — no I/O, no side effects. Easily testable.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass
class SafetyVerdict:
    needs_approval: bool
    reason: str
    tool_name: str
    details: dict[str, str] = field(default_factory=dict)
    hard_denied: bool = False
    is_hard_blocked: bool = False
    hard_block_description: str = ""


_DEFAULT_DESTRUCTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\b"),
    re.compile(r"\brmdir\b"),
    re.compile(r"\bgit\s+push\s+--force\b"),
    re.compile(r"\bgit\s+push\s+-f\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+clean\b"),
    re.compile(r"\bgit\s+checkout\s+\.\s*$"),
    re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+database\b", re.IGNORECASE),
    re.compile(r"\btruncate\b", re.IGNORECASE),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bkill\s+-9\b"),
]

_DEFAULT_SENSITIVE_PATHS: list[str] = [
    ".env",
    ".ssh",
    ".gnupg",
    ".aws/credentials",
    ".config/gcloud",
]


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def check_bash_command(
    command: str,
    custom_patterns: list[str] | None = None,
) -> SafetyVerdict:
    if not command or not command.strip():
        return SafetyVerdict(needs_approval=False, reason="", tool_name="bash")

    normalized = _normalize_whitespace(command)

    for pattern in _DEFAULT_DESTRUCTIVE_PATTERNS:
        if pattern.search(normalized):
            return SafetyVerdict(
                needs_approval=True,
                reason=f"Destructive command detected: {command}",
                tool_name="bash",
                details={"command": command, "matched_pattern": pattern.pattern},
            )

    if custom_patterns:
        for raw_pattern in custom_patterns:
            try:
                compiled = re.compile(raw_pattern, re.IGNORECASE)
                if compiled.search(normalized):
                    return SafetyVerdict(
                        needs_approval=True,
                        reason=f"Custom pattern matched: {command}",
                        tool_name="bash",
                        details={"command": command, "matched_pattern": raw_pattern},
                    )
            except re.error:
                if raw_pattern.lower() in normalized.lower():
                    return SafetyVerdict(
                        needs_approval=True,
                        reason=f"Custom pattern matched: {command}",
                        tool_name="bash",
                        details={"command": command, "matched_pattern": raw_pattern},
                    )

    return SafetyVerdict(needs_approval=False, reason="", tool_name="bash")


def check_write_path(
    path: str,
    working_dir: str,
    sensitive_paths: list[str] | None = None,
) -> SafetyVerdict:
    if not path:
        return SafetyVerdict(needs_approval=False, reason="", tool_name="write_file")

    all_sensitive = list(_DEFAULT_SENSITIVE_PATHS)
    if sensitive_paths:
        all_sensitive.extend(sensitive_paths)

    if os.path.isabs(path):
        resolved = os.path.normpath(path)
    else:
        resolved = os.path.normpath(os.path.join(working_dir, path))

    home = os.path.expanduser("~")

    # Normalize path for component matching (works regardless of actual home dir)
    path_normalized = os.path.normpath(path)
    path_parts = path_normalized.replace("\\", "/").split("/")

    for sensitive in all_sensitive:
        expanded = os.path.expanduser(sensitive) if sensitive.startswith("~") else sensitive

        if os.path.isabs(expanded):
            sensitive_resolved = os.path.normpath(expanded)
        else:
            sensitive_resolved = os.path.normpath(os.path.join(home, expanded))

        if resolved == sensitive_resolved or resolved.startswith(sensitive_resolved + os.sep):
            return SafetyVerdict(
                needs_approval=True,
                reason=f"Write to sensitive path: {path}",
                tool_name="write_file",
                details={"path": path, "matched_sensitive": sensitive},
            )

        # Component matching: handles cases where the input path is relative (e.g.
        # .ssh/id_rsa) but the resolved absolute path doesn't match because the
        # working_dir differs from the user's home directory. Strip ~/  prefix from
        # sensitive patterns so "~/.ssh" matches ".ssh" in any path context.
        sensitive_stripped = sensitive.lstrip("~").lstrip("/").lstrip("\\")
        sensitive_normalized = os.path.normpath(sensitive_stripped).replace("\\", "/")
        sensitive_parts = sensitive_normalized.split("/")
        for i in range(len(path_parts)):
            segment = path_parts[i : i + len(sensitive_parts)]
            if segment == sensitive_parts:
                return SafetyVerdict(
                    needs_approval=True,
                    reason=f"Write to sensitive path: {path}",
                    tool_name="write_file",
                    details={"path": path, "matched_sensitive": sensitive},
                )

    return SafetyVerdict(needs_approval=False, reason="", tool_name="write_file")
