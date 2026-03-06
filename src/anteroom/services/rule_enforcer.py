"""Hard rule enforcement for pack rules at the tool execution layer.

Rules with ``enforce: hard`` in their metadata are checked before every tool
call.  A matching hard rule returns a ``SafetyVerdict`` with ``hard_denied=True``
so the tool is blocked regardless of user override.

Rule metadata schema (in the artifact's ``metadata`` dict)::

    enforce: hard          # "hard" | "soft" (default "soft")
    matches:
      - tool: bash         # tool name (or "*" for all tools)
        pattern: "git push --force"  # regex matched against stringified args
      - tool: write_file
        pattern: "\\.env$"

Pure functions + one stateful class.  No I/O except reading from the in-memory
artifact registry.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..services.artifacts import Artifact, ArtifactType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuleMatch:
    """A single tool-matching clause inside a rule."""

    tool: str  # tool name or "*"
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class ParsedRule:
    """A parsed hard-enforced rule ready for matching."""

    fqn: str
    reason: str
    matches: tuple[RuleMatch, ...]


def parse_rule(artifact: Artifact) -> ParsedRule | None:
    """Parse a rule artifact into a ``ParsedRule`` if it has hard enforcement.

    Returns ``None`` if the rule is soft-enforced (the default) or if the
    metadata is missing/malformed.
    """
    if artifact.type != ArtifactType.RULE:
        return None

    meta = artifact.metadata or {}
    if not meta:
        logger.warning(
            "Rule %s has empty metadata — cannot determine enforcement level (treating as soft)",
            artifact.fqn,
        )
        return None
    if meta.get("enforce") != "hard":
        return None

    raw_matches = meta.get("matches")
    if not isinstance(raw_matches, list) or not raw_matches:
        return None

    rule_matches: list[RuleMatch] = []
    skipped = 0
    for entry in raw_matches:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        tool = str(entry.get("tool", "*"))
        pattern_str = entry.get("pattern", "")
        if not pattern_str:
            skipped += 1
            continue
        try:
            compiled = re.compile(pattern_str)
        except re.error:
            logger.warning("Invalid regex in rule %s: %r", artifact.fqn, pattern_str)
            skipped += 1
            continue
        rule_matches.append(RuleMatch(tool=tool, pattern=compiled))

    if skipped:
        logger.warning(
            "Rule %s: %d of %d match patterns skipped (invalid or empty)",
            artifact.fqn,
            skipped,
            len(raw_matches),
        )

    if not rule_matches:
        return None

    reason = meta.get("reason", artifact.content.strip().split("\n")[0][:120] if artifact.content else artifact.fqn)
    return ParsedRule(fqn=artifact.fqn, reason=reason, matches=tuple(rule_matches))


def _stringify_arguments(tool_name: str, arguments: dict[str, Any]) -> str:
    """Build a single string from tool arguments for regex matching."""
    if tool_name == "bash":
        return str(arguments.get("command", ""))
    if tool_name in ("write_file", "edit_file", "read_file"):
        return str(arguments.get("path", ""))
    # Generic fallback: join all string values
    parts: list[str] = []
    for v in arguments.values():
        if isinstance(v, str):
            parts.append(v)
    return " ".join(parts)


def check_rule(rule: ParsedRule, tool_name: str, arguments: dict[str, Any]) -> bool:
    """Return True if the rule matches (i.e. the call should be blocked)."""
    target = _stringify_arguments(tool_name, arguments)
    for m in rule.matches:
        if m.tool != "*" and m.tool != tool_name:
            continue
        if m.pattern.search(target):
            return True
    return False


class RuleEnforcer:
    """Checks tool calls against hard-enforced rules from the artifact registry."""

    def __init__(self) -> None:
        self._rules: list[ParsedRule] = []

    def load_rules(self, artifacts: list[Artifact]) -> None:
        """Parse and cache hard-enforced rules from a list of rule artifacts."""
        self._rules = []
        for art in artifacts:
            parsed = parse_rule(art)
            if parsed is not None:
                self._rules.append(parsed)

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def check_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, str, str]:
        """Check a tool call against all hard-enforced rules.

        Returns:
            (blocked, reason, rule_fqn) — blocked is True if the call is denied.
        """
        for rule in self._rules:
            if check_rule(rule, tool_name, arguments):
                return True, rule.reason, rule.fqn
        return False, "", ""
