"""Output content filter with system prompt leak detection.

Scans LLM output for forbidden content patterns and system prompt
fragments (OWASP LLM07). Applies configurable actions: warn, block,
or redact. All functions are pure — no I/O, no side effects.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .dlp import _validate_pattern_safety

if TYPE_CHECKING:
    from ..config import OutputFilterConfig

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("anteroom.security")

# Max text length to scan (bytes). Truncated to prevent excessive processing.
MAX_SCAN_LENGTH = 512_000  # 512 KB

# Max system prompt length (chars) for n-gram extraction.
# Prevents quadratic memory from excessively large system prompts.
MAX_PROMPT_LENGTH = 50_000

# Max redaction string length to prevent output expansion DoS.
MAX_REDACTION_LENGTH = 256

# N-gram window size for system prompt leak detection.
# Smaller = more false positives; larger = misses paraphrased leaks.
NGRAM_WINDOW = 8

# Minimum system prompt length (words) to enable leak detection.
# Short prompts produce too many false positives.
MIN_PROMPT_WORDS = 15


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase word tokens for n-gram comparison."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_ngrams(tokens: list[str], window: int) -> set[tuple[str, ...]]:
    """Build a set of n-gram tuples from a token list."""
    if len(tokens) < window:
        return set()
    return {tuple(tokens[i : i + window]) for i in range(len(tokens) - window + 1)}


@dataclass
class OutputFilterMatch:
    """A single filter match result."""

    rule_name: str
    match_count: int
    description: str


@dataclass
class OutputFilterScanResult:
    """Result of scanning output for forbidden content."""

    matched: bool
    action: str  # "warn", "block", "redact", or "pass"
    matches: list[OutputFilterMatch] = field(default_factory=list)
    filtered_text: str | None = None


@dataclass
class _CompiledRule:
    """Internal: a compiled regex rule ready for matching."""

    name: str
    regex: re.Pattern[str]
    description: str


class OutputContentFilter:
    """Output content filter. Constructed per-request with system prompt context."""

    def __init__(self, config: OutputFilterConfig, system_prompt: str | None = None) -> None:
        self._enabled = config.enabled
        self._action = config.action
        self._redaction_string = config.redaction_string[:MAX_REDACTION_LENGTH]
        self._log_detections = config.log_detections
        self._leak_detection = config.system_prompt_leak_detection
        self._leak_threshold = config.leak_threshold
        self._rules: list[_CompiledRule] = []
        self._prompt_ngrams: set[tuple[str, ...]] = set()

        if not self._enabled:
            return

        # Build system prompt n-grams for leak detection
        if self._leak_detection and system_prompt:
            prompt_tokens = _tokenize(system_prompt[:MAX_PROMPT_LENGTH])
            if len(prompt_tokens) >= MIN_PROMPT_WORDS:
                self._prompt_ngrams = _build_ngrams(prompt_tokens, NGRAM_WINDOW)

        # Compile custom patterns
        for pat in config.custom_patterns:
            if not pat.name or not pat.pattern:
                continue
            try:
                compiled = re.compile(pat.pattern)
            except re.error as e:
                logger.warning("Output filter pattern '%s' has invalid regex, skipping: %s", pat.name, e)
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

        rule_count = len(self._rules) + (1 if self._prompt_ngrams else 0)
        if rule_count:
            security_logger.info(
                "Output content filter initialized with %d rules (leak detection: %s)",
                rule_count,
                "on" if self._prompt_ngrams else "off",
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _check_leak(self, text: str) -> OutputFilterMatch | None:
        """Check for system prompt leakage via n-gram overlap."""
        if not self._prompt_ngrams:
            return None

        output_tokens = _tokenize(text)
        if len(output_tokens) < NGRAM_WINDOW:
            return None

        output_ngrams = _build_ngrams(output_tokens, NGRAM_WINDOW)
        overlap = self._prompt_ngrams & output_ngrams

        if not overlap:
            return None

        ratio = len(overlap) / len(self._prompt_ngrams)
        if ratio < self._leak_threshold:
            return None

        return OutputFilterMatch(
            rule_name="system_prompt_leak",
            match_count=len(overlap),
            description=f"System prompt leak detected ({ratio:.0%} n-gram overlap)",
        )

    def scan(self, text: str) -> OutputFilterScanResult:
        """Scan text for forbidden content. Returns scan result without modifying text."""
        if not self._enabled:
            return OutputFilterScanResult(matched=False, action="pass")

        if not text or not text.strip():
            return OutputFilterScanResult(matched=False, action="pass")

        scan_text = text[:MAX_SCAN_LENGTH] if len(text) > MAX_SCAN_LENGTH else text

        matches: list[OutputFilterMatch] = []

        # Check system prompt leak
        leak_match = self._check_leak(scan_text)
        if leak_match:
            matches.append(leak_match)

        # Check custom patterns
        for rule in self._rules:
            found = rule.regex.findall(scan_text)
            if found:
                matches.append(
                    OutputFilterMatch(
                        rule_name=rule.name,
                        match_count=len(found),
                        description=rule.description,
                    )
                )

        if not matches:
            return OutputFilterScanResult(matched=False, action="pass")

        # Apply action
        filtered_text = None
        if self._action == "redact":
            filtered_text = text
            for rule in self._rules:
                filtered_text = rule.regex.sub(self._redaction_string, filtered_text)
            # For leak detection, we can't selectively redact n-gram overlaps
            # in a meaningful way — the entire response is suspect. Mark it.
            if leak_match:
                filtered_text = f"[SYSTEM PROMPT LEAK DETECTED — RESPONSE FILTERED]\n{self._redaction_string}"

        if self._log_detections:
            rule_names = ", ".join(m.rule_name for m in matches)
            total = sum(m.match_count for m in matches)
            security_logger.warning(
                "Output filter: %d match(es) across rules [%s]",
                total,
                rule_names,
            )

        return OutputFilterScanResult(
            matched=True,
            action=self._action,
            matches=matches,
            filtered_text=filtered_text,
        )

    def apply(self, text: str) -> tuple[str, OutputFilterScanResult]:
        """Scan text and apply the configured action.

        Returns:
            (processed_text, scan_result) where processed_text is:
            - filtered text if action="redact"
            - original text if action="warn"
            - empty string if action="block"
            - original text if no match
        """
        result = self.scan(text)

        if not result.matched:
            return text, result

        if result.action == "redact" and result.filtered_text is not None:
            return result.filtered_text, result
        if result.action == "block":
            return "", result
        # warn: return original text unchanged
        return text, result
