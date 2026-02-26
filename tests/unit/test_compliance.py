"""Tests for the compliance rules engine (services/compliance.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from anteroom.config import ComplianceConfig, ComplianceRule
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
class _FakeConfig:
    """Minimal fake config for testing rule evaluation without full AppConfig."""

    ai: _FakeAI = field(default_factory=_FakeAI)
    app: _FakeApp = field(default_factory=_FakeApp)
    safety: _FakeSafety = field(default_factory=_FakeSafety)
    audit: _FakeAudit = field(default_factory=_FakeAudit)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)


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

    def test_unsupported_type(self) -> None:
        cfg = _FakeConfig(app=_FakeApp(port=8080))
        rule = ComplianceRule(field="app.port", must_contain=80)
        v = _evaluate_rule(cfg, rule)
        assert v is not None
        assert "does not support" in v.message


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


# ---------------------------------------------------------------------------
# ComplianceRule dataclass
# ---------------------------------------------------------------------------


class TestComplianceRuleDefaults:
    def test_defaults(self) -> None:
        rule = ComplianceRule(field="safety.enabled")
        assert rule.message == ""
        assert rule.must_be is None
        assert rule.must_not_be is None
        assert rule.must_match == ""
        assert rule.must_not_be_empty is False
        assert rule.must_contain is None


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
