"""Compliance rules engine: declarative config policy validation.

Evaluates compliance rules against the final merged AppConfig.
Non-compliant configurations fail closed at startup.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_MISSING = object()


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
    if isinstance(value, (list, dict)) and len(value) == 0:
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
    from ..config import ComplianceRule

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
    if rule.must_be is not None:
        if not _values_equal(actual, rule.must_be):
            return ComplianceViolation(
                field_path=rule.field,
                message=rule.message or f"'{rule.field}' must be {rule.must_be!r}",
                operator="must_be",
                expected=rule.must_be,
                actual=actual,
            )

    # must_not_be
    if rule.must_not_be is not None:
        if _values_equal(actual, rule.must_not_be):
            return ComplianceViolation(
                field_path=rule.field,
                message=rule.message or f"'{rule.field}' must not be {rule.must_not_be!r}",
                operator="must_not_be",
                expected=rule.must_not_be,
                actual=actual,
            )

    # must_match (regex)
    if rule.must_match:
        try:
            pattern = re.compile(rule.must_match)
        except re.error as e:
            return ComplianceViolation(
                field_path=rule.field,
                message=rule.message or f"Invalid regex pattern for '{rule.field}': {e}",
                operator="must_match",
                actual=actual,
            )
        if not pattern.search(str(actual)):
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
    if rule.must_contain is not None:
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
        return result

    compliance: ComplianceConfig = config.compliance
    if not compliance.rules:
        return result

    for rule in compliance.rules:
        violation = _evaluate_rule(config, rule)
        if violation is not None:
            result.violations.append(violation)

    return result
