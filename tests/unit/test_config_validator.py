"""Tests for the config validator."""

from __future__ import annotations

from anteroom.services.config_validator import ConfigError, ValidationResult, validate_config


class TestValidationResult:
    def test_empty_is_valid(self) -> None:
        r = ValidationResult()
        assert r.is_valid
        assert not r.has_warnings
        assert r.format_errors() == ""

    def test_error_makes_invalid(self) -> None:
        r = ValidationResult(errors=[ConfigError(path="ai.port", message="bad")])
        assert not r.is_valid

    def test_warning_keeps_valid(self) -> None:
        r = ValidationResult(errors=[ConfigError(path="x", message="unknown", severity="warning")])
        assert r.is_valid
        assert r.has_warnings

    def test_format_errors(self) -> None:
        r = ValidationResult(
            errors=[
                ConfigError(path="ai.timeout", message="out of range"),
                ConfigError(path="foo", message="unknown key", severity="warning"),
            ]
        )
        text = r.format_errors()
        assert "1 config error(s)" in text
        assert "ai.timeout" in text
        assert "1 config warning(s)" in text
        assert "foo" in text


class TestConfigErrorStr:
    def test_error_str(self) -> None:
        e = ConfigError(path="ai.model", message="required")
        assert "[error]" in str(e)
        assert "ai.model" in str(e)

    def test_warning_str(self) -> None:
        e = ConfigError(path="x", message="unknown", severity="warning")
        assert "[warning]" in str(e)


class TestValidateConfigBasic:
    def test_empty_dict_valid(self) -> None:
        result = validate_config({})
        assert result.is_valid

    def test_non_dict_invalid(self) -> None:
        result = validate_config("not a dict")  # type: ignore[arg-type]
        assert not result.is_valid
        assert "<root>" in result.errors[0].path

    def test_valid_minimal_config(self) -> None:
        raw = {
            "ai": {"base_url": "http://localhost:8080", "api_key": "sk-test"},
        }
        result = validate_config(raw)
        assert result.is_valid


class TestUnknownKeys:
    def test_unknown_top_level_key(self) -> None:
        result = validate_config({"bogus_key": "value"})
        assert result.is_valid  # warnings don't invalidate
        assert result.has_warnings
        assert any("bogus_key" in e.path for e in result.errors)

    def test_unknown_key_in_ai_section(self) -> None:
        result = validate_config({"ai": {"base_url": "http://x", "mystery": True}})
        assert result.has_warnings
        assert any("ai.mystery" in e.path for e in result.errors)

    def test_unknown_key_in_safety_subagent(self) -> None:
        result = validate_config({"safety": {"subagent": {"unknown_limit": 99}}})
        assert result.has_warnings

    def test_known_keys_no_warnings(self) -> None:
        result = validate_config(
            {
                "ai": {"base_url": "http://x", "api_key": "k", "model": "gpt-4"},
            }
        )
        assert not result.has_warnings


class TestIntFields:
    def test_valid_int(self) -> None:
        result = validate_config({"ai": {"request_timeout": 60}})
        assert result.is_valid

    def test_string_int_invalid(self) -> None:
        result = validate_config({"ai": {"request_timeout": "not_a_number"}})
        assert result.is_valid  # warnings only — parser falls back to default
        assert result.has_warnings

    def test_out_of_range_warning(self) -> None:
        result = validate_config({"ai": {"request_timeout": 9999}})
        assert result.has_warnings
        assert any("out of range" in e.message for e in result.errors)

    def test_port_out_of_range(self) -> None:
        result = validate_config({"app": {"port": 99999}})
        assert result.has_warnings

    def test_string_number_accepted(self) -> None:
        result = validate_config({"ai": {"request_timeout": "60"}})
        assert result.is_valid

    def test_bool_not_numeric(self) -> None:
        result = validate_config({"ai": {"request_timeout": True}})
        assert result.is_valid  # warning only — parser falls back
        assert result.has_warnings


class TestFloatFields:
    def test_valid_float(self) -> None:
        result = validate_config({"ai": {"retry_backoff_base": 2.5}})
        assert result.is_valid

    def test_string_float_invalid(self) -> None:
        result = validate_config({"ai": {"retry_backoff_base": "abc"}})
        assert result.is_valid  # warning only
        assert result.has_warnings

    def test_out_of_range(self) -> None:
        result = validate_config({"ai": {"retry_backoff_base": 999.0}})
        assert result.has_warnings


class TestEnumFields:
    def test_valid_approval_mode(self) -> None:
        result = validate_config({"safety": {"approval_mode": "auto"}})
        assert result.is_valid

    def test_invalid_approval_mode(self) -> None:
        result = validate_config({"safety": {"approval_mode": "yolo"}})
        assert result.is_valid  # warning only — parser falls back
        assert result.has_warnings
        assert any("must be one of" in e.message for e in result.errors)

    def test_valid_planning_auto_mode(self) -> None:
        result = validate_config({"cli": {"planning": {"auto_mode": "suggest"}}})
        assert result.is_valid

    def test_invalid_planning_auto_mode(self) -> None:
        result = validate_config({"cli": {"planning": {"auto_mode": "magic"}}})
        assert result.is_valid  # warning only
        assert result.has_warnings

    def test_valid_embeddings_provider(self) -> None:
        result = validate_config({"embeddings": {"provider": "local"}})
        assert result.is_valid

    def test_invalid_embeddings_provider(self) -> None:
        result = validate_config({"embeddings": {"provider": "azure"}})
        assert result.is_valid  # warning only
        assert result.has_warnings


class TestBoolFields:
    def test_python_bool(self) -> None:
        result = validate_config({"safety": {"enabled": True}})
        assert result.is_valid

    def test_string_bool(self) -> None:
        result = validate_config({"safety": {"enabled": "false"}})
        assert result.is_valid

    def test_int_bool(self) -> None:
        result = validate_config({"safety": {"enabled": 0}})
        assert result.is_valid

    def test_invalid_bool(self) -> None:
        result = validate_config({"safety": {"enabled": "maybe"}})
        assert result.is_valid  # warning only
        assert result.has_warnings
        assert any("expected boolean" in e.message for e in result.errors)


class TestListFields:
    def test_valid_list(self) -> None:
        result = validate_config({"safety": {"allowed_tools": ["bash", "read_file"]}})
        assert result.is_valid

    def test_non_list(self) -> None:
        result = validate_config({"safety": {"allowed_tools": "bash"}})
        assert result.is_valid  # warning only — parser defaults to empty list
        assert result.has_warnings
        assert any("expected list" in e.message for e in result.errors)


class TestSectionTypes:
    def test_ai_not_dict(self) -> None:
        result = validate_config({"ai": "string"})
        assert not result.is_valid
        assert any("expected dict" in e.message for e in result.errors)

    def test_mcp_servers_not_list(self) -> None:
        result = validate_config({"mcp_servers": {"bad": True}})
        assert not result.is_valid
        assert any("expected list" in e.message for e in result.errors)


class TestMcpServerValidation:
    def test_valid_stdio_server(self) -> None:
        result = validate_config(
            {
                "mcp_servers": [
                    {"name": "test", "transport": "stdio", "command": "npx", "args": ["-y", "test"]},
                ]
            }
        )
        assert result.is_valid

    def test_valid_sse_server(self) -> None:
        result = validate_config(
            {
                "mcp_servers": [
                    {"name": "test", "transport": "sse", "url": "http://localhost:3000"},
                ]
            }
        )
        assert result.is_valid

    def test_missing_name(self) -> None:
        result = validate_config({"mcp_servers": [{"transport": "stdio", "command": "test"}]})
        assert not result.is_valid
        assert any("missing required 'name'" in e.message for e in result.errors)

    def test_invalid_transport(self) -> None:
        result = validate_config(
            {
                "mcp_servers": [
                    {"name": "test", "transport": "websocket"},
                ]
            }
        )
        assert not result.is_valid

    def test_stdio_missing_command(self) -> None:
        result = validate_config(
            {
                "mcp_servers": [
                    {"name": "test", "transport": "stdio"},
                ]
            }
        )
        assert not result.is_valid
        assert any("command" in e.message for e in result.errors)

    def test_sse_missing_url(self) -> None:
        result = validate_config(
            {
                "mcp_servers": [
                    {"name": "test", "transport": "sse"},
                ]
            }
        )
        assert not result.is_valid

    def test_both_include_exclude_warning(self) -> None:
        result = validate_config(
            {
                "mcp_servers": [
                    {
                        "name": "test",
                        "transport": "stdio",
                        "command": "x",
                        "tools_include": ["a"],
                        "tools_exclude": ["b"],
                    },
                ]
            }
        )
        assert result.has_warnings

    def test_unknown_mcp_key(self) -> None:
        result = validate_config(
            {
                "mcp_servers": [
                    {"name": "test", "transport": "stdio", "command": "x", "custom_field": True},
                ]
            }
        )
        assert result.has_warnings

    def test_non_dict_entry(self) -> None:
        result = validate_config({"mcp_servers": ["not-a-dict"]})
        assert not result.is_valid


class TestProxyOriginValidation:
    def test_valid_origins(self) -> None:
        result = validate_config({"proxy": {"allowed_origins": ["http://localhost:3000"]}})
        assert result.is_valid

    def test_wildcard_warning(self) -> None:
        result = validate_config({"proxy": {"allowed_origins": ["*"]}})
        assert result.is_valid  # warning only — parser ignores invalid origins
        assert result.has_warnings

    def test_no_scheme_warning(self) -> None:
        result = validate_config({"proxy": {"allowed_origins": ["localhost:3000"]}})
        assert result.is_valid  # warning only
        assert result.has_warnings


class TestComplexConfig:
    def test_full_valid_config(self) -> None:
        raw = {
            "ai": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "model": "llama3",
                "request_timeout": 300,
                "connect_timeout": 10,
            },
            "app": {"port": 9090, "tls": False},
            "cli": {
                "max_tool_iterations": 30,
                "planning": {"auto_mode": "suggest", "auto_threshold_tools": 10},
                "usage": {"week_days": 7, "month_days": 30},
            },
            "safety": {
                "approval_mode": "ask_for_writes",
                "allowed_tools": ["bash"],
                "subagent": {"max_concurrent": 3, "timeout": 60},
            },
            "embeddings": {"provider": "local", "enabled": True},
            "mcp_servers": [
                {"name": "fs", "transport": "stdio", "command": "npx", "args": ["-y", "fs-server"]},
            ],
        }
        result = validate_config(raw)
        assert result.is_valid
        assert not result.has_warnings

    def test_multiple_warnings_collected(self) -> None:
        raw = {
            "ai": {"request_timeout": "not_a_number", "retry_backoff_base": "bad"},
            "safety": {"approval_mode": "yolo"},
        }
        result = validate_config(raw)
        assert result.is_valid  # all warnings, no errors
        assert len([e for e in result.errors if e.severity == "warning"]) >= 3
