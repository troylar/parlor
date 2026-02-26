"""Compliance rules engine: declarative config policy validation.

Evaluates compliance rules against the final merged AppConfig.
Non-compliant configurations fail closed at startup.

Each rule should use exactly one operator (must_be, must_not_be, must_match,
must_not_be_empty, or must_contain). When multiple operators are set on a
single rule, they are evaluated in order and the first failure wins.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_MISSING = object()

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

_SENSITIVE_SEGMENTS = frozenset({"api_key", "password", "secret", "token", "credential", "private_key"})

_MAX_REGEX_SUBJECT_LEN = 10_000


@dataclass
class ComplianceViolation:
    """A single compliance rule violation."""

    field_path: str
    message: str
    operator: str
    expected: Any = None
    actual: Any = _MISSING


@dataclass
class ComplianceResult:
    """Result of compliance validation."""

    violations: list[ComplianceViolation] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        return len(self.violations) == 0

    def format_report(self) -> str:
        if self.is_compliant:
            return "All compliance rules passed."
        lines = [f"Compliance check failed — {len(self.violations)} violation(s):"]
        for v in self.violations:
            msg = v.message or f"{v.field_path}: {v.operator} check failed"
            if v.actual is not _MISSING:
                tail = v.field_path.rsplit(".", 1)[-1] if v.field_path else ""
                if tail in _SENSITIVE_SEGMENTS:
                    lines.append(f"  - {msg} (actual: <redacted>)")
                else:
                    lines.append(f"  - {msg} (actual: {v.actual!r})")
            else:
                lines.append(f"  - {msg}")
        return "\n".join(lines)


def _resolve_config_path(config: object, dot_path: str) -> Any:
    """Resolve a dot-separated path against a dataclass hierarchy.

    Returns ``_MISSING`` if any segment is not found.
    """
    obj: Any = config
    for part in dot_path.split("."):
        if not _SAFE_IDENTIFIER.match(part):
            return _MISSING
        try:
            obj = getattr(obj, part)
        except AttributeError:
            return _MISSING
    return obj


def _is_empty(value: Any) -> bool:
    """Check if a value is considered empty."""
    if value is None:
        return True
    if value is _MISSING:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict, tuple)) and len(value) == 0:
        return True
    return False


def _values_equal(actual: Any, expected: Any) -> bool:
    """Compare values, handling YAML type coercion (str "true" vs bool True)."""
    if actual == expected:
        return True
    if isinstance(actual, bool) and isinstance(expected, str):
        return str(actual).lower() == expected.lower()
    if isinstance(expected, bool) and isinstance(actual, str):
        return str(expected).lower() == actual.lower()
    try:
        if not isinstance(actual, type(expected)):
            return str(actual) == str(expected)
    except Exception:
        pass
    return False


def _evaluate_rule(config: object, rule: Any) -> ComplianceViolation | None:
    """Evaluate a single compliance rule. Returns a violation or None."""
    # Deferred import to avoid circular dependency: compliance.py is imported by
    # __main__.py which also imports config.py; config.py imports dataclass types
    # at module level. Importing ComplianceRule here breaks the cycle.
    from ..config import _UNSET, ComplianceRule

    if not isinstance(rule, ComplianceRule):
        return None

    actual = _resolve_config_path(config, rule.field)

    if actual is _MISSING:
        if rule.must_not_be_empty:
            return ComplianceViolation(
                field_path=rule.field,
                message=rule.message or f"Field '{rule.field}' does not exist",
                operator="must_not_be_empty",
                actual=_MISSING,
            )
        return ComplianceViolation(
            field_path=rule.field,
            message=rule.message or f"Field '{rule.field}' does not exist in config",
            operator="field_missing",
        )

    # must_be
    if rule.must_be is not _UNSET:
        if not _values_equal(actual, rule.must_be):
            return ComplianceViolation(
                field_path=rule.field,
                message=rule.message or f"'{rule.field}' must be {rule.must_be!r}",
                operator="must_be",
                expected=rule.must_be,
                actual=actual,
            )

    # must_not_be
    if rule.must_not_be is not _UNSET:
        if _values_equal(actual, rule.must_not_be):
            return ComplianceViolation(
                field_path=rule.field,
                message=rule.message or f"'{rule.field}' must not be {rule.must_not_be!r}",
                operator="must_not_be",
                expected=rule.must_not_be,
                actual=actual,
            )

    # must_match (regex) — uses pre-compiled pattern from config parsing when available
    if rule.must_match:
        pattern = rule._compiled_pattern
        if pattern is None:
            try:
                pattern = re.compile(rule.must_match)
            except re.error as e:
                logger.warning("Invalid regex pattern in compliance rule for '%s': %s", rule.field, e)
                return ComplianceViolation(
                    field_path=rule.field,
                    message=rule.message or f"Invalid regex pattern for '{rule.field}': {e}",
                    operator="must_match",
                    actual=actual,
                )
        subject = str(actual)[:_MAX_REGEX_SUBJECT_LEN]
        if not pattern.search(subject):
            return ComplianceViolation(
                field_path=rule.field,
                message=rule.message or f"'{rule.field}' must match pattern '{rule.must_match}'",
                operator="must_match",
                expected=rule.must_match,
                actual=actual,
            )

    # must_not_be_empty
    if rule.must_not_be_empty:
        if _is_empty(actual):
            return ComplianceViolation(
                field_path=rule.field,
                message=rule.message or f"'{rule.field}' must not be empty",
                operator="must_not_be_empty",
                actual=actual,
            )

    # must_contain
    if rule.must_contain is not _UNSET:
        if isinstance(actual, (list, tuple)):
            if rule.must_contain not in actual:
                return ComplianceViolation(
                    field_path=rule.field,
                    message=rule.message or f"'{rule.field}' must contain {rule.must_contain!r}",
                    operator="must_contain",
                    expected=rule.must_contain,
                    actual=actual,
                )
        elif isinstance(actual, str):
            if str(rule.must_contain) not in actual:
                return ComplianceViolation(
                    field_path=rule.field,
                    message=rule.message or f"'{rule.field}' must contain {rule.must_contain!r}",
                    operator="must_contain",
                    expected=rule.must_contain,
                    actual=actual,
                )
        elif isinstance(actual, dict):
            if rule.must_contain not in actual:
                return ComplianceViolation(
                    field_path=rule.field,
                    message=rule.message or f"'{rule.field}' must contain key {rule.must_contain!r}",
                    operator="must_contain",
                    expected=rule.must_contain,
                    actual=actual,
                )
        else:
            return ComplianceViolation(
                field_path=rule.field,
                message=rule.message
                or f"'{rule.field}' does not support 'must_contain' (type: {type(actual).__name__})",
                operator="must_contain",
                actual=actual,
            )

    return None


def validate_compliance(config: object) -> ComplianceResult:
    """Validate all compliance rules against the final merged config.

    Args:
        config: An ``AppConfig`` instance (typed as object to avoid circular imports at module level).

    Returns:
        ``ComplianceResult`` with any violations found.
    """
    from ..config import AppConfig, ComplianceConfig

    result = ComplianceResult()

    if not isinstance(config, AppConfig):
        logger.debug("validate_compliance called with non-AppConfig type: %s", type(config).__name__)
        return result

    compliance: ComplianceConfig = config.compliance
    if not compliance.rules:
        return result

    for rule in compliance.rules:
        violation = _evaluate_rule(config, rule)
        if violation is not None:
            result.violations.append(violation)

    return result
