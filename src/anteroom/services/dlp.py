"""Data Loss Prevention scanning pipeline.

Scans text for sensitive data patterns (SSN, credit card, email, etc.)
and applies configurable actions: redact, block, or warn.
All functions are pure — no I/O, no side effects.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import DlpConfig, DlpPatternConfig

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("anteroom.security")

# Max text length to scan (bytes). Texts exceeding this are truncated for
# DLP scanning to prevent excessive regex processing time.
MAX_SCAN_LENGTH = 512_000  # 512 KB

# Built-in patterns for common sensitive data types.
# These are used when the user enables DLP but does not specify custom patterns.
# All patterns are designed to be ReDoS-safe (linear-time matching).
BUILTIN_PATTERNS: list[dict[str, str]] = [
    {
        "name": "ssn",
        "pattern": r"\b\d{3}-\d{2}-\d{4}\b",
        "description": "Social Security Number",
    },
    {
        "name": "credit_card",
        # Linear-time: anchor on first digit, then 12-18 more digits with
        # optional single separator (space or dash) between each.
        "pattern": r"\b\d(?:[ -]?\d){12,18}\b",
        "description": "Credit/debit card number",
    },
    {
        "name": "email",
        "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "description": "Email address",
    },
    {
        "name": "phone_us",
        # Linear-time: fixed structure with optional prefix and separators.
        "pattern": r"\b(?:\+?1[-.\s])?\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "description": "US phone number",
    },
    {
        "name": "iban",
        "pattern": r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b",
        "description": "International Bank Account Number",
    },
]


def _validate_pattern_safety(pattern: str, name: str) -> bool:
    """Static analysis to detect regex patterns vulnerable to catastrophic backtracking.

    Detects nested quantifiers: (a+)+, (a*)+, (a+)*, etc.

    Note: alternation-based ReDoS (e.g., (a|aa)+) is not detected by this
    check. The MAX_SCAN_LENGTH guard provides a secondary defense for those cases.

    Returns True if the pattern appears safe, False if it looks pathological.
    """
    # Detect nested quantifiers: a group with a quantifier inside, followed by
    # another quantifier. This is the most common ReDoS pattern.
    # E.g., (a+)+, (a*)+, (a+)*, (\d[ -]*?){13,19}
    nested_quantifier = re.compile(
        r"\("  # opening paren
        r"[^)]*"  # group contents
        r"[+*]"  # inner quantifier
        r"[^)]*"  # more group contents
        r"\)"  # closing paren
        r"[+*{]"  # outer quantifier
    )
    if nested_quantifier.search(pattern):
        logger.warning(
            "DLP pattern '%s' contains nested quantifiers (ReDoS risk), rejecting",
            name,
        )
        return False
    return True


@dataclass
class DlpMatch:
    """A single pattern match result."""

    rule_name: str
    match_count: int
    description: str


@dataclass
class DlpScanResult:
    """Result of scanning text for sensitive data."""

    matched: bool
    action: str  # "redact", "block", "warn", or "pass"
    matches: list[DlpMatch] = field(default_factory=list)
    redacted_text: str | None = None


@dataclass
class _CompiledRule:
    """Internal: a compiled regex rule ready for matching."""

    name: str
    regex: re.Pattern[str]
    description: str


class DlpScanner:
    """Stateless DLP scanner. Compile once, scan many times."""

    def __init__(self, config: DlpConfig) -> None:
        self._enabled = config.enabled
        self._scan_output = config.scan_output
        self._scan_input = config.scan_input
        self._action = config.action
        self._redaction_string = config.redaction_string
        self._log_detections = config.log_detections
        self._rules: list[_CompiledRule] = []

        if not self._enabled:
            return

        all_patterns: list[DlpPatternConfig] = list(config.patterns) + list(config.custom_patterns)

        # If no patterns configured, use built-ins
        if not all_patterns:
            from ..config import DlpPatternConfig

            all_patterns = [
                DlpPatternConfig(name=p["name"], pattern=p["pattern"], description=p["description"])
                for p in BUILTIN_PATTERNS
            ]

        for pat in all_patterns:
            if not pat.name or not pat.pattern:
                continue
            try:
                compiled = re.compile(pat.pattern)
            except re.error as e:
                logger.warning("DLP pattern '%s' has invalid regex, skipping: %s", pat.name, e)
                continue
            if not _validate_pattern_safety(pat.pattern, pat.name):
                continue
            self._rules.append(
                _CompiledRule(
                    name=pat.name,
                    regex=compiled,
                    description=pat.description,
                )
            )

        if self._rules:
            security_logger.info("DLP scanner initialized with %d rules", len(self._rules))

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def scan_output(self) -> bool:
        return self._scan_output

    @property
    def scan_input(self) -> bool:
        return self._scan_input

    def scan(self, text: str, direction: str = "output") -> DlpScanResult:
        """Scan text for sensitive data. Returns scan result without modifying text.

        Args:
            text: The text to scan.
            direction: "output" (LLM response) or "input" (user message).
        """
        if not self._enabled:
            return DlpScanResult(matched=False, action="pass")

        if direction == "output" and not self._scan_output:
            return DlpScanResult(matched=False, action="pass")
        if direction == "input" and not self._scan_input:
            return DlpScanResult(matched=False, action="pass")

        if not text or not text.strip():
            return DlpScanResult(matched=False, action="pass")

        # Truncate to prevent excessive regex processing time
        scan_text = text[:MAX_SCAN_LENGTH] if len(text) > MAX_SCAN_LENGTH else text

        matches: list[DlpMatch] = []
        for rule in self._rules:
            found = rule.regex.findall(scan_text)
            if found:
                matches.append(
                    DlpMatch(
                        rule_name=rule.name,
                        match_count=len(found),
                        description=rule.description,
                    )
                )

        if not matches:
            return DlpScanResult(matched=False, action="pass")

        # Apply action
        redacted_text = None
        if self._action == "redact":
            redacted_text = text
            for rule in self._rules:
                redacted_text = rule.regex.sub(self._redaction_string, redacted_text)

        if self._log_detections:
            rule_names = ", ".join(m.rule_name for m in matches)
            total = sum(m.match_count for m in matches)
            security_logger.warning(
                "DLP %s scan: %d match(es) across rules [%s]",
                direction,
                total,
                rule_names,
            )

        return DlpScanResult(
            matched=True,
            action=self._action,
            matches=matches,
            redacted_text=redacted_text,
        )

    def apply(self, text: str, direction: str = "output") -> tuple[str, DlpScanResult]:
        """Scan text and apply the configured action.

        Returns:
            (processed_text, scan_result) where processed_text is:
            - redacted text if action="redact"
            - original text if action="warn"
            - empty string if action="block"
            - original text if no match
        """
        result = self.scan(text, direction)

        if not result.matched:
            return text, result

        if result.action == "redact" and result.redacted_text is not None:
            return result.redacted_text, result
        if result.action == "block":
            return "", result
        # warn: return original text unchanged
        return text, result
