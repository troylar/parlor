"""Tests for the compliance rules engine (services/compliance.py).

Covers:
- _resolve_config_path: traversal, missing segments, security (dunders, unsafe chars)
- _is_empty: all emptiness definitions (None, _MISSING, whitespace, empty collections)
- _values_equal: same-type, cross-type, bool↔str coercion, edge cases
- _evaluate_rule: every operator (must_be, must_not_be, must_match, must_not_be_empty,
  must_contain), pass/fail for each, missing fields, multi-operator ordering, custom
  messages, default messages, type coercion, unsupported types
- validate_compliance: AppConfig guard, empty rules, mixed pass/fail, violation ordering
- ComplianceResult: is_compliant, format_report (redaction, MISSING sentinel, multiple)
- ComplianceRule: defaults, YAML parsing, invalid rules, all operator fields
- Security hardening: path injection, credential redaction, regex subject cap, ReDoS
- Full integration: real AppConfig round-trips via load_config + validate_compliance
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from anteroom.config import _UNSET, ComplianceConfig, ComplianceRule
from anteroom.services.compliance import (
    ComplianceResult,
    ComplianceViolation,
    _evaluate_rule,
    _is_empty,
    _resolve_config_path,
    _values_equal,
    validate_compliance,
)

# ---------------------------------------------------------------------------
# Helpers — lightweight fake configs to avoid needing a full AppConfig
# ---------------------------------------------------------------------------


@dataclass
class _FakeSafety:
    enabled: bool = True
    approval_mode: str = "ask_for_writes"
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    read_only: bool = False


@dataclass
class _FakeAudit:
    enabled: bool = False
    log_path: str = ""
    tamper_protection: str = "hmac"


@dataclass
class _FakeApp:
    tls: bool = False
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class _FakeAI:
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "test"
    model: str = "gpt-4"
    verify_ssl: bool = True


@dataclass
class _FakeBash:
    timeout: int = 30
    blocked_commands: list[str] = field(default_factory=list)
    allow_network: bool = False


@dataclass
class _FakeSafetyNested:
    enabled: bool = True
    approval_mode: str = "ask_for_writes"
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    read_only: bool = False
    bash: _FakeBash = field(default_factory=_FakeBash)
    metadata: dict = field(default_factory=dict)


@dataclass
class _FakeConfig:
    """Minimal fake config for testing rule evaluation without full AppConfig."""

    ai: _FakeAI = field(default_factory=_FakeAI)
    app: _FakeApp = field(default_factory=_FakeApp)
    safety: _FakeSafety = field(default_factory=_FakeSafety)
    audit: _FakeAudit = field(default_factory=_FakeAudit)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)


@dataclass
class _FakeConfigNested:
    """Config with deeper nesting for path traversal tests."""

    safety: _FakeSafetyNested = field(default_factory=_FakeSafetyNested)


# ---------------------------------------------------------------------------
# _resolve_config_path
# ---------------------------------------------------------------------------


class TestResolveConfigPath:
    def test_single_level(self) -> None:
        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "app") is cfg.app

    def test_nested_path(self) -> None:
        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "safety.approval_mode") == "ask_for_writes"

    def test_deep_nested_path(self) -> None:
        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "audit.tamper_protection") == "hmac"

    def test_missing_path_returns_missing(self) -> None:
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "nonexistent") is _MISSING

    def test_missing_nested_returns_missing(self) -> None:
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "safety.nonexistent") is _MISSING

    def test_bool_field(self) -> None:
        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "safety.enabled") is True

    def test_list_field(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(allowed_tools=["bash"]))
        assert _resolve_config_path(cfg, "safety.allowed_tools") == ["bash"]

    def test_three_level_path(self) -> None:
        cfg = _FakeConfigNested(safety=_FakeSafetyNested(bash=_FakeBash(timeout=60)))
        assert _resolve_config_path(cfg, "safety.bash.timeout") == 60

    def test_three_level_list_field(self) -> None:
        cfg = _FakeConfigNested(safety=_FakeSafetyNested(bash=_FakeBash(blocked_commands=["rm", "shutdown"])))
        assert _resolve_config_path(cfg, "safety.bash.blocked_commands") == ["rm", "shutdown"]

    def test_int_field(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=9090))
        assert _resolve_config_path(cfg, "app.port") == 9090

    def test_middle_segment_missing(self) -> None:
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "nonexistent.nested.deep") is _MISSING

    def test_empty_string_field(self) -> None:
        cfg = _FakeConfig(audit=_FakeAudit(log_path=""))
        assert _resolve_config_path(cfg, "audit.log_path") == ""

    def test_dict_field(self) -> None:
        cfg = _FakeConfigNested(safety=_FakeSafetyNested(metadata={"key": "value"}))
        assert _resolve_config_path(cfg, "safety.metadata") == {"key": "value"}


# ---------------------------------------------------------------------------
# _is_empty
# ---------------------------------------------------------------------------


class TestIsEmpty:
    def test_none(self) -> None:
        assert _is_empty(None) is True

    def test_empty_string(self) -> None:
        assert _is_empty("") is True

    def test_whitespace_string(self) -> None:
        assert _is_empty("   ") is True

    def test_empty_list(self) -> None:
        assert _is_empty([]) is True

    def test_empty_dict(self) -> None:
        assert _is_empty({}) is True

    def test_non_empty_string(self) -> None:
        assert _is_empty("hello") is False

    def test_non_empty_list(self) -> None:
        assert _is_empty(["a"]) is False

    def test_zero_is_not_empty(self) -> None:
        assert _is_empty(0) is False

    def test_false_is_not_empty(self) -> None:
        assert _is_empty(False) is False

    def test_missing_sentinel(self) -> None:
        from anteroom.services.compliance import _MISSING

        assert _is_empty(_MISSING) is True

    def test_tab_only_string(self) -> None:
        assert _is_empty("\t\t") is True

    def test_newline_only_string(self) -> None:
        assert _is_empty("\n") is True

    def test_mixed_whitespace_string(self) -> None:
        assert _is_empty(" \t\n ") is True

    def test_string_with_content_and_spaces(self) -> None:
        assert _is_empty("  hello  ") is False

    def test_non_empty_dict(self) -> None:
        assert _is_empty({"key": "val"}) is False

    def test_single_element_list(self) -> None:
        assert _is_empty([""]) is False

    def test_negative_number(self) -> None:
        assert _is_empty(-1) is False

    def test_float_zero(self) -> None:
        assert _is_empty(0.0) is False

    def test_tuple_empty(self) -> None:
        assert _is_empty(()) is True

    def test_tuple_nonempty_is_not_empty(self) -> None:
        assert _is_empty((1, 2)) is False

    def test_object_is_not_empty(self) -> None:
        assert _is_empty(object()) is False


# ---------------------------------------------------------------------------
# _values_equal
# ---------------------------------------------------------------------------


class TestValuesEqual:
    def test_same_type_equal(self) -> None:
        assert _values_equal("auto", "auto") is True

    def test_same_type_not_equal(self) -> None:
        assert _values_equal("auto", "ask") is False

    def test_bool_to_str(self) -> None:
        assert _values_equal(True, "true") is True
        assert _values_equal(False, "false") is True
        assert _values_equal(True, "false") is False

    def test_str_to_bool(self) -> None:
        assert _values_equal("true", True) is True
        assert _values_equal("false", False) is True

    def test_int_equality(self) -> None:
        assert _values_equal(120, 120) is True
        assert _values_equal(120, 60) is False

    def test_cross_type_str_int(self) -> None:
        assert _values_equal(120, "120") is True

    def test_none_equals_none(self) -> None:
        assert _values_equal(None, None) is True

    def test_none_not_equals_string(self) -> None:
        assert _values_equal(None, "none") is False

    def test_bool_true_vs_string_upper(self) -> None:
        assert _values_equal(True, "TRUE") is True

    def test_bool_false_vs_string_upper(self) -> None:
        assert _values_equal(False, "FALSE") is True

    def test_string_true_capitalized_vs_bool_true(self) -> None:
        assert _values_equal("True", True) is True

    def test_int_zero_vs_string_zero(self) -> None:
        assert _values_equal(0, "0") is True

    def test_float_vs_string(self) -> None:
        assert _values_equal(3.14, "3.14") is True

    def test_float_vs_int(self) -> None:
        assert _values_equal(3.0, 3) is True

    def test_empty_string_vs_empty_string(self) -> None:
        assert _values_equal("", "") is True

    def test_list_equality(self) -> None:
        assert _values_equal([1, 2], [1, 2]) is True

    def test_list_inequality(self) -> None:
        assert _values_equal([1, 2], [2, 1]) is False

    def test_dict_equality(self) -> None:
        assert _values_equal({"a": 1}, {"a": 1}) is True

    def test_dict_inequality(self) -> None:
        assert _values_equal({"a": 1}, {"a": 2}) is False

    def test_bool_true_vs_int_1(self) -> None:
        # In Python, True == 1 is True
        assert _values_equal(True, 1) is True

    def test_bool_false_vs_int_0(self) -> None:
        assert _values_equal(False, 0) is True


# ---------------------------------------------------------------------------
# _evaluate_rule — must_be
# ---------------------------------------------------------------------------


class TestMustBe:
    def test_must_be_match(self) -> None:
        cfg = _FakeConfig(audit=_FakeAudit(enabled=True))
        rule = ComplianceRule(field="audit.enabled", must_be=True)
        assert _evaluate_rule(cfg, rule) is None

    def test_must_be_mismatch(self) -> None:
        cfg = _FakeConfig(audit=_FakeAudit(enabled=False))
        rule = ComplianceRule(field="audit.enabled", must_be=True, message="Audit must be on")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_be"
        assert v.actual is False
        assert v.message == "Audit must be on"

    def test_must_be_string(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="ask_for_writes"))
        rule = ComplianceRule(field="safety.approval_mode", must_be="ask_for_writes")
        assert _evaluate_rule(cfg, rule) is None

    def test_must_be_int(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=8080))
        rule = ComplianceRule(field="app.port", must_be=8080)
        assert _evaluate_rule(cfg, rule) is None

    def test_must_be_int_mismatch(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=9090))
        rule = ComplianceRule(field="app.port", must_be=8080)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.expected == 8080
        assert v.actual == 9090

    def test_must_be_bool_coercion_from_str(self) -> None:
        """YAML might store 'true' as string; must_be True should still match."""
        cfg = _FakeConfig(audit=_FakeAudit(enabled=True))
        rule = ComplianceRule(field="audit.enabled", must_be="true")
        assert _evaluate_rule(cfg, rule) is None

    def test_must_be_false(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(read_only=False))
        rule = ComplianceRule(field="safety.read_only", must_be=False)
        assert _evaluate_rule(cfg, rule) is None

    def test_must_be_false_mismatch(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(read_only=True))
        rule = ComplianceRule(field="safety.read_only", must_be=False)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.actual is True

    def test_must_be_default_message(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=9090))
        rule = ComplianceRule(field="app.port", must_be=8080)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "must be" in v.message
        assert "8080" in v.message

    def test_must_be_violation_has_correct_fields(self) -> None:
        cfg = _FakeConfig(audit=_FakeAudit(enabled=False))
        rule = ComplianceRule(field="audit.enabled", must_be=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.field_path == "audit.enabled"
        assert v.operator == "must_be"
        assert v.expected is True
        assert v.actual is False

    def test_must_be_zero_enforced(self) -> None:
        """must_be: 0 must not silently skip — it should enforce the value."""
        cfg = _FakeConfig(app=_FakeApp(port=8080))
        rule = ComplianceRule(field="app.port", must_be=0)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.actual == 8080

    def test_must_be_zero_passes(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=0))
        rule = ComplianceRule(field="app.port", must_be=0)
        assert _evaluate_rule(cfg, rule) is None

    def test_must_be_none_enforced(self) -> None:
        """must_be: null in YAML (None) should enforce the value, not skip."""
        cfg = _FakeConfig(ai=_FakeAI(api_key="secret"))
        rule = ComplianceRule(field="ai.api_key", must_be=None)
        v = _evaluate_rule(cfg, rule)
        assert v is not None

    def test_must_be_empty_string_enforced(self) -> None:
        """must_be: '' should enforce empty string."""
        cfg = _FakeConfig(ai=_FakeAI(base_url="http://localhost"))
        rule = ComplianceRule(field="ai.base_url", must_be="")
        v = _evaluate_rule(cfg, rule)
        assert v is not None


# ---------------------------------------------------------------------------
# _evaluate_rule — must_not_be
# ---------------------------------------------------------------------------


class TestMustNotBe:
    def test_must_not_be_passes(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="ask_for_writes"))
        rule = ComplianceRule(field="safety.approval_mode", must_not_be="auto")
        assert _evaluate_rule(cfg, rule) is None

    def test_must_not_be_fails(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="auto"))
        rule = ComplianceRule(field="safety.approval_mode", must_not_be="auto", message="Auto is prohibited")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_not_be"
        assert v.message == "Auto is prohibited"

    def test_must_not_be_bool(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(read_only=False))
        rule = ComplianceRule(field="safety.read_only", must_not_be=True)
        assert _evaluate_rule(cfg, rule) is None

    def test_must_not_be_bool_violation(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(read_only=True))
        rule = ComplianceRule(field="safety.read_only", must_not_be=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_not_be"
        assert v.actual is True

    def test_must_not_be_int(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=8080))
        rule = ComplianceRule(field="app.port", must_not_be=0)
        assert _evaluate_rule(cfg, rule) is None

    def test_must_not_be_int_violation(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=0))
        rule = ComplianceRule(field="app.port", must_not_be=0)
        v = _evaluate_rule(cfg, rule)
        assert v is not None

    def test_must_not_be_cross_type_str_bool(self) -> None:
        """must_not_be 'true' should catch actual=True via coercion."""
        cfg = _FakeConfig(audit=_FakeAudit(enabled=True))
        rule = ComplianceRule(field="audit.enabled", must_not_be="true")
        v = _evaluate_rule(cfg, rule)
        assert v is not None

    def test_must_not_be_default_message(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="auto"))
        rule = ComplianceRule(field="safety.approval_mode", must_not_be="auto")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "must not be" in v.message
        assert "'auto'" in v.message

    def test_must_not_be_violation_fields(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="auto"))
        rule = ComplianceRule(field="safety.approval_mode", must_not_be="auto")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.field_path == "safety.approval_mode"
        assert v.expected == "auto"
        assert v.actual == "auto"

    def test_must_not_be_none_enforced(self) -> None:
        """must_not_be: null should enforce — reject actual=None."""

        @dataclass
        class _CfgNullable:
            value: Any = None

        cfg = _CfgNullable()
        rule = ComplianceRule(field="value", must_not_be=None)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_not_be"

    def test_must_not_be_false_enforced(self) -> None:
        """must_not_be: false should fire when actual is False."""
        cfg = _FakeConfig(safety=_FakeSafety(read_only=False))
        rule = ComplianceRule(field="safety.read_only", must_not_be=False)
        v = _evaluate_rule(cfg, rule)
        assert v is not None

    def test_must_not_be_zero_enforced(self) -> None:
        """must_not_be: 0 should fire when actual is 0."""
        cfg = _FakeConfig(app=_FakeApp(port=0))
        rule = ComplianceRule(field="app.port", must_not_be=0)
        v = _evaluate_rule(cfg, rule)
        assert v is not None


# ---------------------------------------------------------------------------
# _evaluate_rule — must_match
# ---------------------------------------------------------------------------


class TestMustMatch:
    def test_regex_match(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="ask_for_writes"))
        rule = ComplianceRule(field="safety.approval_mode", must_match=r"^ask")
        assert _evaluate_rule(cfg, rule) is None

    def test_regex_no_match(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="auto"))
        rule = ComplianceRule(field="safety.approval_mode", must_match=r"^ask")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_match"

    def test_invalid_regex(self) -> None:
        cfg = _FakeConfig()
        rule = ComplianceRule(field="safety.approval_mode", must_match=r"[invalid")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_match"
        assert "invalid" in v.message.lower() or "regex" in v.message.lower()

    def test_regex_full_match(self) -> None:
        cfg = _FakeConfig(ai=_FakeAI(base_url="https://api.corp.com/v1"))
        rule = ComplianceRule(field="ai.base_url", must_match=r"^https://")
        assert _evaluate_rule(cfg, rule) is None

    def test_regex_partial_match(self) -> None:
        """must_match uses search(), so partial match is sufficient."""
        cfg = _FakeConfig(ai=_FakeAI(base_url="http://proxy.corp.com/api/v1"))
        rule = ComplianceRule(field="ai.base_url", must_match=r"corp\.com")
        assert _evaluate_rule(cfg, rule) is None

    def test_regex_no_match_violation_fields(self) -> None:
        cfg = _FakeConfig(ai=_FakeAI(base_url="http://localhost"))
        rule = ComplianceRule(field="ai.base_url", must_match=r"^https://")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.expected == r"^https://"
        assert v.actual == "http://localhost"

    def test_regex_default_message(self) -> None:
        cfg = _FakeConfig(ai=_FakeAI(base_url="http://localhost"))
        rule = ComplianceRule(field="ai.base_url", must_match=r"^https://")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "must match pattern" in v.message

    def test_regex_on_non_string_value(self) -> None:
        """must_match converts value to string before matching."""
        cfg = _FakeConfig(app=_FakeApp(port=8080))
        rule = ComplianceRule(field="app.port", must_match=r"^8080$")
        assert _evaluate_rule(cfg, rule) is None

    def test_regex_on_bool_value(self) -> None:
        cfg = _FakeConfig(audit=_FakeAudit(enabled=True))
        rule = ComplianceRule(field="audit.enabled", must_match=r"^True$")
        assert _evaluate_rule(cfg, rule) is None

    def test_regex_custom_message_on_failure(self) -> None:
        cfg = _FakeConfig(ai=_FakeAI(base_url="http://evil.com"))
        rule = ComplianceRule(
            field="ai.base_url", must_match=r"^https://.*\.corp\.com", message="Must use corp HTTPS endpoint"
        )
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.message == "Must use corp HTTPS endpoint"

    def test_regex_custom_message_on_invalid_pattern(self) -> None:
        cfg = _FakeConfig()
        rule = ComplianceRule(field="safety.approval_mode", must_match=r"(unclosed", message="Bad pattern")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.message == "Bad pattern"

    def test_empty_must_match_skipped(self) -> None:
        """Empty string must_match is falsy, so the check is skipped."""
        cfg = _FakeConfig()
        rule = ComplianceRule(field="safety.approval_mode", must_match="")
        assert _evaluate_rule(cfg, rule) is None

    def test_precompiled_pattern_used(self) -> None:
        """Pre-compiled pattern from config parsing avoids re-compile on each eval."""
        import re as re_mod

        compiled = re_mod.compile(r"^ask")
        cfg = _FakeConfig()
        rule = ComplianceRule(field="safety.approval_mode", must_match=r"^ask", _compiled_pattern=compiled)
        assert _evaluate_rule(cfg, rule) is None

    def test_invalid_precompiled_falls_back(self) -> None:
        """When _compiled_pattern is None (invalid regex), falls back to runtime compile."""
        cfg = _FakeConfig()
        rule = ComplianceRule(field="safety.approval_mode", must_match=r"(unclosed", _compiled_pattern=None)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_match"


# ---------------------------------------------------------------------------
# _evaluate_rule — must_not_be_empty
# ---------------------------------------------------------------------------


class TestMustNotBeEmpty:
    def test_non_empty_passes(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="ask"))
        rule = ComplianceRule(field="safety.approval_mode", must_not_be_empty=True)
        assert _evaluate_rule(cfg, rule) is None

    def test_empty_string_fails(self) -> None:
        cfg = _FakeConfig(audit=_FakeAudit(log_path=""))
        rule = ComplianceRule(field="audit.log_path", must_not_be_empty=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_not_be_empty"

    def test_empty_list_fails(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(allowed_tools=[]))
        rule = ComplianceRule(field="safety.allowed_tools", must_not_be_empty=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None

    def test_non_empty_list_passes(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(allowed_tools=["bash"]))
        rule = ComplianceRule(field="safety.allowed_tools", must_not_be_empty=True)
        assert _evaluate_rule(cfg, rule) is None

    def test_missing_field_fails(self) -> None:
        cfg = _FakeConfig()
        rule = ComplianceRule(field="nonexistent.field", must_not_be_empty=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_not_be_empty"

    def test_whitespace_only_fails(self) -> None:
        cfg = _FakeConfig(audit=_FakeAudit(log_path="   "))
        rule = ComplianceRule(field="audit.log_path", must_not_be_empty=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_not_be_empty"

    def test_empty_dict_fails(self) -> None:
        cfg = _FakeConfigNested(safety=_FakeSafetyNested(metadata={}))
        rule = ComplianceRule(field="safety.metadata", must_not_be_empty=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None

    def test_non_empty_dict_passes(self) -> None:
        cfg = _FakeConfigNested(safety=_FakeSafetyNested(metadata={"key": "val"}))
        rule = ComplianceRule(field="safety.metadata", must_not_be_empty=True)
        assert _evaluate_rule(cfg, rule) is None

    def test_must_not_be_empty_false_is_noop(self) -> None:
        """must_not_be_empty=False means the check is skipped."""
        cfg = _FakeConfig(audit=_FakeAudit(log_path=""))
        rule = ComplianceRule(field="audit.log_path", must_not_be_empty=False)
        assert _evaluate_rule(cfg, rule) is None

    def test_default_message(self) -> None:
        cfg = _FakeConfig(audit=_FakeAudit(log_path=""))
        rule = ComplianceRule(field="audit.log_path", must_not_be_empty=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "must not be empty" in v.message

    def test_int_zero_is_not_empty(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=0))
        rule = ComplianceRule(field="app.port", must_not_be_empty=True)
        assert _evaluate_rule(cfg, rule) is None

    def test_bool_false_is_not_empty(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(read_only=False))
        rule = ComplianceRule(field="safety.read_only", must_not_be_empty=True)
        assert _evaluate_rule(cfg, rule) is None

    def test_empty_tuple_is_empty(self) -> None:
        """Empty tuples should now be caught by must_not_be_empty."""

        @dataclass
        class _CfgTuple:
            items: tuple = ()

        cfg = _CfgTuple()
        rule = ComplianceRule(field="items", must_not_be_empty=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_not_be_empty"


# ---------------------------------------------------------------------------
# _evaluate_rule — must_contain
# ---------------------------------------------------------------------------


class TestMustContain:
    def test_list_contains(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(denied_tools=["bash", "rm_tool"]))
        rule = ComplianceRule(field="safety.denied_tools", must_contain="bash")
        assert _evaluate_rule(cfg, rule) is None

    def test_list_missing(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(denied_tools=["rm_tool"]))
        rule = ComplianceRule(field="safety.denied_tools", must_contain="bash")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_contain"

    def test_string_contains(self) -> None:
        cfg = _FakeConfig(ai=_FakeAI(base_url="https://api.example.com/v1"))
        rule = ComplianceRule(field="ai.base_url", must_contain="example.com")
        assert _evaluate_rule(cfg, rule) is None

    def test_string_missing(self) -> None:
        cfg = _FakeConfig(ai=_FakeAI(base_url="https://api.other.com/v1"))
        rule = ComplianceRule(field="ai.base_url", must_contain="example.com")
        v = _evaluate_rule(cfg, rule)
        assert v is not None

    def test_dict_contains_key(self) -> None:
        @dataclass
        class _CfgWithDict:
            data: dict = field(default_factory=lambda: {"enabled": True, "mode": "strict"})

        cfg = _CfgWithDict()
        rule = ComplianceRule(field="data", must_contain="enabled")
        assert _evaluate_rule(cfg, rule) is None

    def test_dict_missing_key(self) -> None:
        @dataclass
        class _CfgWithDict:
            data: dict = field(default_factory=lambda: {"mode": "strict"})

        cfg = _CfgWithDict()
        rule = ComplianceRule(field="data", must_contain="enabled")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_contain"

    def test_tuple_contains(self) -> None:
        @dataclass
        class _CfgTuple:
            items: tuple = (1, 2, 3)

        cfg = _CfgTuple()
        rule = ComplianceRule(field="items", must_contain=2)
        assert _evaluate_rule(cfg, rule) is None

    def test_tuple_missing(self) -> None:
        @dataclass
        class _CfgTuple:
            items: tuple = (1, 2, 3)

        cfg = _CfgTuple()
        rule = ComplianceRule(field="items", must_contain=99)
        v = _evaluate_rule(cfg, rule)
        assert v is not None

    def test_list_with_multiple_items(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(denied_tools=["bash", "write_file", "edit_file"]))
        rule = ComplianceRule(field="safety.denied_tools", must_contain="write_file")
        assert _evaluate_rule(cfg, rule) is None

    def test_string_substring_match(self) -> None:
        cfg = _FakeConfig(ai=_FakeAI(base_url="https://api.corp.internal.com/v1"))
        rule = ComplianceRule(field="ai.base_url", must_contain="internal")
        assert _evaluate_rule(cfg, rule) is None

    def test_string_must_contain_int(self) -> None:
        """must_contain converts to string for string actual values."""
        cfg = _FakeConfig(ai=_FakeAI(base_url="http://host:8080/v1"))
        rule = ComplianceRule(field="ai.base_url", must_contain=8080)
        assert _evaluate_rule(cfg, rule) is None

    def test_dict_with_nested_value(self) -> None:
        @dataclass
        class _CfgDict:
            data: dict = field(default_factory=lambda: {"enabled": True, "mode": "strict", "level": 2})

        cfg = _CfgDict()
        rule = ComplianceRule(field="data", must_contain="level")
        assert _evaluate_rule(cfg, rule) is None

    def test_empty_list_must_contain_fails(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(denied_tools=[]))
        rule = ComplianceRule(field="safety.denied_tools", must_contain="bash")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_contain"

    def test_must_contain_default_message_list(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(denied_tools=[]))
        rule = ComplianceRule(field="safety.denied_tools", must_contain="bash")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "must contain" in v.message

    def test_must_contain_default_message_dict(self) -> None:
        @dataclass
        class _CfgDict:
            data: dict = field(default_factory=dict)

        cfg = _CfgDict()
        rule = ComplianceRule(field="data", must_contain="key")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "must contain key" in v.message

    def test_unsupported_type(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=8080))
        rule = ComplianceRule(field="app.port", must_contain=80)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "does not support" in v.message

    def test_unsupported_type_bool(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(enabled=True))
        rule = ComplianceRule(field="safety.enabled", must_contain="x")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "does not support" in v.message

    def test_must_contain_none_enforced(self) -> None:
        """must_contain: null should check containment of None, not skip."""
        cfg = _FakeConfig(safety=_FakeSafety(denied_tools=["bash"]))
        rule = ComplianceRule(field="safety.denied_tools", must_contain=None)
        v = _evaluate_rule(cfg, rule)
        assert v is not None

    def test_must_contain_false_enforced(self) -> None:
        """must_contain: false should check containment of False."""
        cfg = _FakeConfig(safety=_FakeSafety(denied_tools=["bash"]))
        rule = ComplianceRule(field="safety.denied_tools", must_contain=False)
        v = _evaluate_rule(cfg, rule)
        assert v is not None


# ---------------------------------------------------------------------------
# _evaluate_rule — missing field
# ---------------------------------------------------------------------------


class TestMissingField:
    def test_missing_field_returns_violation(self) -> None:
        cfg = _FakeConfig()
        rule = ComplianceRule(field="nonexistent", must_be=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "field_missing"

    def test_non_compliance_rule_returns_none(self) -> None:
        cfg = _FakeConfig()
        assert _evaluate_rule(cfg, {"field": "safety.enabled", "must_be": True}) is None
        assert _evaluate_rule(cfg, "not a rule") is None

    def test_none_rule_returns_none(self) -> None:
        cfg = _FakeConfig()
        assert _evaluate_rule(cfg, None) is None

    def test_int_rule_returns_none(self) -> None:
        cfg = _FakeConfig()
        assert _evaluate_rule(cfg, 42) is None

    def test_missing_field_default_message(self) -> None:
        cfg = _FakeConfig()
        rule = ComplianceRule(field="nonexistent", must_be=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "does not exist" in v.message

    def test_missing_field_custom_message(self) -> None:
        cfg = _FakeConfig()
        rule = ComplianceRule(field="nonexistent", must_be=True, message="Field is required")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.message == "Field is required"

    def test_missing_nested_field_with_must_not_be(self) -> None:
        cfg = _FakeConfig()
        rule = ComplianceRule(field="safety.nonexistent", must_not_be="auto")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "field_missing"

    def test_missing_field_with_must_match(self) -> None:
        cfg = _FakeConfig()
        rule = ComplianceRule(field="nonexistent.path", must_match=r".*")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "field_missing"

    def test_missing_field_with_must_contain(self) -> None:
        cfg = _FakeConfig()
        rule = ComplianceRule(field="nonexistent", must_contain="x")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "field_missing"

    def test_missing_field_must_not_be_empty_takes_precedence(self) -> None:
        """When field is missing and must_not_be_empty=True, operator is must_not_be_empty."""
        cfg = _FakeConfig()
        rule = ComplianceRule(field="nonexistent", must_not_be_empty=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_not_be_empty"


# ---------------------------------------------------------------------------
# Multi-operator behavior
# ---------------------------------------------------------------------------


class TestMultiOperator:
    def test_must_be_fails_before_must_match(self) -> None:
        """When must_be and must_match are set, must_be is checked first."""
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="auto"))
        rule = ComplianceRule(field="safety.approval_mode", must_be="ask", must_match=r"^a")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_be"

    def test_must_be_passes_then_must_match_fails(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="ask_for_writes"))
        rule = ComplianceRule(field="safety.approval_mode", must_be="ask_for_writes", must_match=r"^NOPE")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_match"

    def test_must_not_be_fails_before_must_not_be_empty(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="auto"))
        rule = ComplianceRule(field="safety.approval_mode", must_not_be="auto", must_not_be_empty=True)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_not_be"

    def test_all_operators_pass(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="ask_for_writes"))
        rule = ComplianceRule(
            field="safety.approval_mode",
            must_not_be="auto",
            must_match=r"^ask",
            must_not_be_empty=True,
            must_contain="writes",
        )
        assert _evaluate_rule(cfg, rule) is None

    def test_no_operators_set_passes(self) -> None:
        """A rule with no operators set just checks the field exists."""
        cfg = _FakeConfig()
        rule = ComplianceRule(field="safety.approval_mode")
        assert _evaluate_rule(cfg, rule) is None


# ---------------------------------------------------------------------------
# _evaluate_rule — custom messages
# ---------------------------------------------------------------------------


class TestCustomMessages:
    def test_custom_message_in_violation(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="auto"))
        rule = ComplianceRule(
            field="safety.approval_mode",
            must_not_be="auto",
            message="Auto approval is prohibited by security policy",
        )
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.message == "Auto approval is prohibited by security policy"

    def test_default_message_when_empty(self) -> None:
        cfg = _FakeConfig(safety=_FakeSafety(approval_mode="auto"))
        rule = ComplianceRule(field="safety.approval_mode", must_not_be="auto")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "must not be" in v.message


# ---------------------------------------------------------------------------
# validate_compliance — integration
# ---------------------------------------------------------------------------


class TestValidateCompliance:
    def test_no_rules_is_compliant(self) -> None:
        cfg = _FakeConfig(compliance=ComplianceConfig(rules=[]))
        # validate_compliance expects an AppConfig, but works on any object
        # with a .compliance attribute. For integration tests we use _FakeConfig.
        result = validate_compliance(cfg)
        # _FakeConfig is not an AppConfig, so it returns early as compliant
        assert result.is_compliant

    def test_non_appconfig_logs_debug(self) -> None:
        """Non-AppConfig types should log a DEBUG message and return compliant."""
        result = validate_compliance("not a config")
        assert result.is_compliant

    def test_non_appconfig_int(self) -> None:
        result = validate_compliance(42)
        assert result.is_compliant

    def test_non_appconfig_none(self) -> None:
        result = validate_compliance(None)
        assert result.is_compliant

    def test_all_rules_pass(self) -> None:
        rules = [
            ComplianceRule(field="audit.enabled", must_be=True),
            ComplianceRule(field="safety.approval_mode", must_not_be="auto"),
        ]
        cfg = _FakeConfig(
            audit=_FakeAudit(enabled=True),
            safety=_FakeSafety(approval_mode="ask_for_writes"),
            compliance=ComplianceConfig(rules=rules),
        )
        # For full integration, test with a real AppConfig
        # Here we test the core logic via _evaluate_rule
        for rule in rules:
            assert _evaluate_rule(cfg, rule) is None

    def test_multiple_violations_collected(self) -> None:
        rules = [
            ComplianceRule(field="audit.enabled", must_be=True, message="Audit required"),
            ComplianceRule(field="safety.approval_mode", must_not_be="auto", message="No auto"),
        ]
        cfg = _FakeConfig(
            audit=_FakeAudit(enabled=False),
            safety=_FakeSafety(approval_mode="auto"),
        )
        violations = []
        for rule in rules:
            v = _evaluate_rule(cfg, rule)
            if v:
                violations.append(v)
        assert len(violations) == 2
        assert violations[0].message == "Audit required"
        assert violations[1].message == "No auto"


# ---------------------------------------------------------------------------
# ComplianceResult
# ---------------------------------------------------------------------------


class TestComplianceResult:
    def test_empty_is_compliant(self) -> None:
        r = ComplianceResult()
        assert r.is_compliant is True

    def test_with_violations_not_compliant(self) -> None:
        r = ComplianceResult(violations=[ComplianceViolation(field_path="x", message="bad", operator="must_be")])
        assert r.is_compliant is False

    def test_format_report_compliant(self) -> None:
        r = ComplianceResult()
        assert "passed" in r.format_report().lower()

    def test_format_report_with_violations(self) -> None:
        from anteroom.services.compliance import _MISSING

        r = ComplianceResult(
            violations=[
                ComplianceViolation(
                    field_path="safety.approval_mode",
                    message="No auto",
                    operator="must_not_be",
                    actual="auto",
                ),
                ComplianceViolation(
                    field_path="audit.enabled",
                    message="Audit required",
                    operator="must_be",
                    actual=_MISSING,
                ),
            ]
        )
        report = r.format_report()
        assert "2 violation" in report
        assert "No auto" in report
        assert "Audit required" in report

    def test_format_report_single_violation(self) -> None:
        r = ComplianceResult(
            violations=[
                ComplianceViolation(field_path="x", message="bad", operator="must_be", actual=42),
            ]
        )
        report = r.format_report()
        assert "1 violation" in report
        assert "bad" in report
        assert "42" in report

    def test_format_report_missing_actual_no_parenthetical(self) -> None:
        """When actual is _MISSING, the report line has no (actual: ...) suffix."""
        from anteroom.services.compliance import _MISSING

        r = ComplianceResult(
            violations=[
                ComplianceViolation(field_path="x", message="not found", operator="field_missing", actual=_MISSING),
            ]
        )
        report = r.format_report()
        assert "not found" in report
        assert "(actual:" not in report

    def test_format_report_fallback_message(self) -> None:
        """When violation has no message, format_report generates one from field_path and operator."""
        r = ComplianceResult(
            violations=[
                ComplianceViolation(field_path="safety.mode", message="", operator="must_be", actual="auto"),
            ]
        )
        report = r.format_report()
        assert "safety.mode" in report
        assert "must_be" in report

    def test_format_report_many_violations(self) -> None:
        violations = [
            ComplianceViolation(field_path=f"field_{i}", message=f"violation {i}", operator="must_be", actual=i)
            for i in range(10)
        ]
        r = ComplianceResult(violations=violations)
        report = r.format_report()
        assert "10 violation" in report
        for i in range(10):
            assert f"violation {i}" in report

    def test_format_report_redacts_password(self) -> None:
        r = ComplianceResult(
            violations=[
                ComplianceViolation(
                    field_path="db.password", message="Weak password", operator="must_match", actual="hunter2"
                ),
            ]
        )
        report = r.format_report()
        assert "hunter2" not in report
        assert "<redacted>" in report

    def test_format_report_redacts_token(self) -> None:
        r = ComplianceResult(
            violations=[
                ComplianceViolation(
                    field_path="auth.token", message="Bad token", operator="must_match", actual="abc123"
                ),
            ]
        )
        report = r.format_report()
        assert "abc123" not in report
        assert "<redacted>" in report

    def test_format_report_redacts_secret(self) -> None:
        r = ComplianceResult(
            violations=[
                ComplianceViolation(
                    field_path="app.secret", message="Bad secret", operator="must_match", actual="s3cret"
                ),
            ]
        )
        report = r.format_report()
        assert "s3cret" not in report

    def test_format_report_redacts_credential(self) -> None:
        r = ComplianceResult(
            violations=[
                ComplianceViolation(
                    field_path="auth.credential", message="Bad cred", operator="must_match", actual="cred123"
                ),
            ]
        )
        report = r.format_report()
        assert "cred123" not in report

    def test_format_report_redacts_private_key(self) -> None:
        r = ComplianceResult(
            violations=[
                ComplianceViolation(
                    field_path="tls.private_key",
                    message="Bad key",
                    operator="must_match",
                    actual="-----BEGIN PRIVATE KEY-----",
                ),
            ]
        )
        report = r.format_report()
        assert "BEGIN PRIVATE KEY" not in report

    def test_format_report_empty_field_path(self) -> None:
        """Empty field_path should not crash redaction logic."""
        r = ComplianceResult(
            violations=[
                ComplianceViolation(field_path="", message="Root violation", operator="must_be", actual="x"),
            ]
        )
        report = r.format_report()
        assert "Root violation" in report


# ---------------------------------------------------------------------------
# ComplianceRule dataclass
# ---------------------------------------------------------------------------


class TestComplianceRuleDefaults:
    def test_defaults(self) -> None:
        rule = ComplianceRule(field="safety.enabled")
        assert rule.message == ""
        assert rule.must_be is _UNSET
        assert rule.must_not_be is _UNSET
        assert rule.must_match == ""
        assert rule.must_not_be_empty is False
        assert rule.must_contain is _UNSET


# ---------------------------------------------------------------------------
# Config parsing integration — compliance rules from YAML
# ---------------------------------------------------------------------------


class TestComplianceConfigParsing:
    def test_empty_compliance_section(self, tmp_path: Any) -> None:
        """Config with no compliance section parses to empty rules."""
        import yaml

        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                }
            )
        )
        config, _ = load_config(config_file)
        assert config.compliance.rules == []

    def test_compliance_rules_parsed(self, tmp_path: Any) -> None:
        """Compliance rules are parsed from YAML into ComplianceRule objects."""
        import yaml

        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "compliance": {
                        "rules": [
                            {
                                "field": "safety.approval_mode",
                                "must_not_be": "auto",
                                "message": "Auto is prohibited",
                            },
                            {
                                "field": "audit.enabled",
                                "must_be": True,
                                "message": "Audit required",
                            },
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        assert len(config.compliance.rules) == 2
        assert config.compliance.rules[0].field == "safety.approval_mode"
        assert config.compliance.rules[0].must_not_be == "auto"
        assert config.compliance.rules[0].message == "Auto is prohibited"
        assert config.compliance.rules[1].must_be is True

    def test_compliance_rules_with_all_operators(self, tmp_path: Any) -> None:
        """All operator fields are parsed correctly."""
        import yaml

        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "compliance": {
                        "rules": [
                            {
                                "field": "safety.approval_mode",
                                "must_match": "^ask",
                                "must_not_be_empty": True,
                                "must_contain": "ask",
                            },
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        rule = config.compliance.rules[0]
        assert rule.must_match == "^ask"
        assert rule.must_not_be_empty is True
        assert rule.must_contain == "ask"

    def test_invalid_rule_without_field_skipped(self, tmp_path: Any) -> None:
        """Rules without a 'field' key are silently skipped."""
        import yaml

        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "compliance": {
                        "rules": [
                            {"must_be": True},
                            {"field": "audit.enabled", "must_be": True},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        assert len(config.compliance.rules) == 1
        assert config.compliance.rules[0].field == "audit.enabled"

    def test_compliance_with_empty_rules_list(self, tmp_path: Any) -> None:
        """Explicit empty rules list."""
        import yaml

        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "compliance": {"rules": []},
                }
            )
        )
        config, _ = load_config(config_file)
        assert config.compliance.rules == []

    def test_compliance_with_extra_fields_ignored(self, tmp_path: Any) -> None:
        """Unknown fields in a rule are silently ignored."""
        import yaml

        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "compliance": {
                        "rules": [
                            {
                                "field": "audit.enabled",
                                "must_be": True,
                                "unknown_field": "ignored",
                                "severity": "high",
                            },
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        assert len(config.compliance.rules) == 1
        assert config.compliance.rules[0].field == "audit.enabled"

    def test_must_not_be_empty_parsed_as_bool(self, tmp_path: Any) -> None:
        """YAML true/false is parsed as Python bool for must_not_be_empty."""
        import yaml

        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "compliance": {
                        "rules": [
                            {"field": "ai.base_url", "must_not_be_empty": True},
                            {"field": "ai.model", "must_not_be_empty": False},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        assert config.compliance.rules[0].must_not_be_empty is True
        assert config.compliance.rules[1].must_not_be_empty is False

    def test_many_rules_parsed(self, tmp_path: Any) -> None:
        """Config with many rules parses all of them."""
        import yaml

        from anteroom.config import load_config

        rules_raw = [{"field": "ai.base_url", "must_not_be": f"bad_{i}"} for i in range(20)]
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "compliance": {"rules": rules_raw},
                }
            )
        )
        config, _ = load_config(config_file)
        assert len(config.compliance.rules) == 20


# ---------------------------------------------------------------------------
# Full integration: validate_compliance with real AppConfig
# ---------------------------------------------------------------------------


class TestFullIntegration:
    def test_compliant_config(self, tmp_path: Any) -> None:
        """A config that satisfies all rules passes compliance."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "safety": {"approval_mode": "ask_for_writes"},
                    "audit": {"enabled": True},
                    "compliance": {
                        "rules": [
                            {"field": "safety.approval_mode", "must_not_be": "auto", "message": "No auto"},
                            {"field": "audit.enabled", "must_be": True, "message": "Audit required"},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert result.is_compliant

    def test_non_compliant_config(self, tmp_path: Any) -> None:
        """A config violating rules fails compliance with all violations listed."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "safety": {"approval_mode": "auto"},
                    "compliance": {
                        "rules": [
                            {"field": "safety.approval_mode", "must_not_be": "auto", "message": "No auto"},
                            {"field": "audit.enabled", "must_be": True, "message": "Audit required"},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert not result.is_compliant
        assert len(result.violations) == 2

    def test_must_not_be_empty_with_none_value(self, tmp_path: Any) -> None:
        """must_not_be_empty fails on None-valued fields."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "compliance": {
                        "rules": [
                            {"field": "ai.seed", "must_not_be_empty": True, "message": "Seed required"},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert not result.is_compliant
        assert result.violations[0].message == "Seed required"

    def test_nested_path_four_segments(self, tmp_path: Any) -> None:
        """Dot-paths up to 4 segments resolve correctly."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "safety": {"bash": {"timeout": 60}},
                    "compliance": {
                        "rules": [
                            {"field": "safety.bash.timeout", "must_not_be": 0, "message": "Bash timeout required"},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert result.is_compliant

    def test_must_match_integration(self, tmp_path: Any) -> None:
        """must_match works end-to-end with real AppConfig."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "https://api.corp.com/v1", "api_key": "test"},
                    "compliance": {
                        "rules": [
                            {"field": "ai.base_url", "must_match": "^https://", "message": "Must use HTTPS"},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert result.is_compliant

    def test_must_match_violation_integration(self, tmp_path: Any) -> None:
        """must_match violation end-to-end."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://insecure.com/v1", "api_key": "test"},
                    "compliance": {
                        "rules": [
                            {"field": "ai.base_url", "must_match": "^https://", "message": "Must use HTTPS"},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert not result.is_compliant
        assert result.violations[0].message == "Must use HTTPS"

    def test_must_contain_list_integration(self, tmp_path: Any) -> None:
        """must_contain on a list field end-to-end."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "safety": {"denied_tools": ["bash", "rm_tool"]},
                    "compliance": {
                        "rules": [
                            {"field": "safety.denied_tools", "must_contain": "bash", "message": "Must deny bash"},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert result.is_compliant

    def test_no_rules_is_compliant_integration(self, tmp_path: Any) -> None:
        """Config with no compliance section is compliant."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert result.is_compliant

    def test_format_report_integration(self, tmp_path: Any) -> None:
        """format_report produces a readable report end-to-end."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"},
                    "safety": {"approval_mode": "auto"},
                    "compliance": {
                        "rules": [
                            {"field": "safety.approval_mode", "must_not_be": "auto", "message": "No auto mode"},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        report = result.format_report()
        assert "1 violation" in report
        assert "No auto mode" in report
        assert "(actual: 'auto')" in report

    def test_mixed_pass_fail_integration(self, tmp_path: Any) -> None:
        """Some rules pass, some fail — only failures appear."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://localhost:11434/v1", "api_key": "test", "model": "gpt-4"},
                    "safety": {"approval_mode": "ask_for_writes"},
                    "compliance": {
                        "rules": [
                            {"field": "safety.approval_mode", "must_not_be": "auto"},
                            {"field": "ai.model", "must_be": "gpt-4"},
                            {"field": "ai.base_url", "must_match": "^https://", "message": "Must use HTTPS"},
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert not result.is_compliant
        assert len(result.violations) == 1
        assert result.violations[0].message == "Must use HTTPS"

    def test_enterprise_lockdown_scenario(self, tmp_path: Any) -> None:
        """Realistic enterprise scenario: multiple rules enforcing security policy."""
        import yaml

        from anteroom.config import load_config
        from anteroom.services.compliance import validate_compliance

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "https://api.corp.internal/v1", "api_key": "sk-corp-key", "model": "gpt-4"},
                    "safety": {"approval_mode": "ask_for_writes"},
                    "audit": {"enabled": True},
                    "compliance": {
                        "rules": [
                            {"field": "safety.approval_mode", "must_not_be": "auto", "message": "Auto mode prohibited"},
                            {
                                "field": "ai.base_url",
                                "must_match": "^https://.*\\.corp\\.internal",
                                "message": "Must use internal API",
                            },
                            {"field": "audit.enabled", "must_be": True, "message": "Audit logging required"},
                            {
                                "field": "ai.api_key",
                                "must_not_be_empty": True,
                                "message": "API key must be configured",
                            },
                        ],
                    },
                }
            )
        )
        config, _ = load_config(config_file)
        result = validate_compliance(config)
        assert result.is_compliant, f"Expected compliant but got: {result.format_report()}"


# ---------------------------------------------------------------------------
# Security hardening tests
# ---------------------------------------------------------------------------


class TestSecurityHardening:
    def test_unsafe_path_segments_rejected(self) -> None:
        """Paths with dunder or non-identifier segments return _MISSING."""
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "__class__") is _MISSING
        assert _resolve_config_path(cfg, "safety.__dict__") is _MISSING
        assert _resolve_config_path(cfg, "safety.0bad") is _MISSING
        assert _resolve_config_path(cfg, "safety.has-hyphen") is _MISSING

    def test_sensitive_field_redacted_in_report(self) -> None:
        """Violation reports redact actual values for sensitive fields."""
        r = ComplianceResult(
            violations=[
                ComplianceViolation(
                    field_path="ai.api_key",
                    message="API key must match pattern",
                    operator="must_match",
                    actual="sk-secret-12345",
                ),
            ]
        )
        report = r.format_report()
        assert "<redacted>" in report
        assert "sk-secret-12345" not in report

    def test_non_sensitive_field_not_redacted(self) -> None:
        """Non-sensitive fields still show actual values."""
        r = ComplianceResult(
            violations=[
                ComplianceViolation(
                    field_path="safety.approval_mode",
                    message="Bad mode",
                    operator="must_be",
                    actual="auto",
                ),
            ]
        )
        report = r.format_report()
        assert "'auto'" in report

    def test_regex_subject_length_capped(self) -> None:
        """Long config values are truncated before regex matching."""
        cfg = _FakeConfig(ai=_FakeAI(base_url="x" * 20_000))
        rule = ComplianceRule(field="ai.base_url", must_match=r"^x+$")
        assert _evaluate_rule(cfg, rule) is None

    def test_path_with_leading_underscore_rejected(self) -> None:
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "_private") is _MISSING

    def test_path_with_double_underscore_in_middle_rejected(self) -> None:
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "safety.__init__") is _MISSING

    def test_path_with_uppercase_rejected(self) -> None:
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "Safety") is _MISSING
        assert _resolve_config_path(cfg, "safety.ENABLED") is _MISSING

    def test_path_with_space_rejected(self) -> None:
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "safety.approval mode") is _MISSING

    def test_path_with_dot_only_rejected(self) -> None:
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, ".") is _MISSING
        assert _resolve_config_path(cfg, "..") is _MISSING

    def test_path_with_numeric_start_rejected(self) -> None:
        from anteroom.services.compliance import _MISSING

        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "123abc") is _MISSING

    def test_valid_path_with_numbers_accepted(self) -> None:
        """Identifiers like 'v2' should be accepted (starts with letter)."""

        @dataclass
        class _Cfg:
            v2: str = "value"

        cfg = _Cfg()
        assert _resolve_config_path(cfg, "v2") == "value"

    def test_valid_path_with_underscores_accepted(self) -> None:
        cfg = _FakeConfig()
        assert _resolve_config_path(cfg, "safety.approval_mode") == "ask_for_writes"

    def test_all_sensitive_fields_redacted(self) -> None:
        """Every field in _SENSITIVE_SEGMENTS is redacted."""
        from anteroom.services.compliance import _SENSITIVE_SEGMENTS

        for segment in _SENSITIVE_SEGMENTS:
            r = ComplianceResult(
                violations=[
                    ComplianceViolation(
                        field_path=f"section.{segment}",
                        message=f"Bad {segment}",
                        operator="must_match",
                        actual="secret_value",
                    ),
                ]
            )
            report = r.format_report()
            assert "secret_value" not in report, f"Field '{segment}' was not redacted"
            assert "<redacted>" in report, f"Field '{segment}' missing redaction marker"

    def test_regex_cap_prevents_match_beyond_limit(self) -> None:
        """Pattern that only matches at position > 10_000 should fail."""
        cfg = _FakeConfig(ai=_FakeAI(base_url="a" * 10_001 + "MATCH"))
        rule = ComplianceRule(field="ai.base_url", must_match=r"MATCH")
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert v.operator == "must_match"

    def test_regex_cap_at_boundary(self) -> None:
        """Pattern that matches exactly at position 10_000 should still work."""
        cfg = _FakeConfig(ai=_FakeAI(base_url="a" * 9_995 + "MATCH"))
        rule = ComplianceRule(field="ai.base_url", must_match=r"MATCH")
        assert _evaluate_rule(cfg, rule) is None
