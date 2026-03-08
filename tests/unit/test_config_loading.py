"""Tests for config.py env var overrides and load_config branches.

Covers missed lines concentrated in:
- Lines 40-41, 66-96: _get_version and build_runtime_context
- Lines 913-968: AI timeout/retry env var loading
- Lines 996-1000: allowed_domains env var
- Lines 1048-1049: port fallback
- Lines 1096-1117: shared_databases / databases keys
- Lines 1130-1181: cli config section
- Lines 1199-1270, 1284-1305: usage / budget config
- Lines 1386-1464: safety / bash sandbox config
- Lines 1473-1826+: load_config branches (proxy, storage, session, audit, etc.)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from anteroom.config import AppConfig, build_runtime_context, load_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(path: Path, data: dict) -> Path:
    cfg_file = path / "config.yaml"
    cfg_file.write_text(yaml.dump(data))
    return cfg_file


_MINIMAL_AI = {"ai": {"base_url": "https://api.example.com", "api_key": "sk-test"}}


def _minimal(tmp_path: Path, extra: dict | None = None) -> Path:
    data: dict = dict(_MINIMAL_AI)
    if extra:
        data = {**data, **extra}
    return _write_config(tmp_path, data)


# ---------------------------------------------------------------------------
# build_runtime_context (lines 44-118)
# ---------------------------------------------------------------------------


class TestBuildRuntimeContext:
    def test_basic_web_interface(self) -> None:
        ctx = build_runtime_context(model="gpt-4")
        assert "<anteroom_context>" in ctx
        assert "Web UI" in ctx
        assert "gpt-4" in ctx
        assert "</anteroom_context>" in ctx

    def test_cli_interface(self) -> None:
        ctx = build_runtime_context(model="gpt-4o", interface="cli")
        assert "CLI REPL" in ctx
        assert "CLI:" in ctx

    def test_builtin_tools_listed(self) -> None:
        ctx = build_runtime_context(model="gpt-4", builtin_tools=["read_file", "bash"])
        assert "Available tools:" in ctx
        assert "read_file:" in ctx
        assert "bash:" in ctx

    def test_unknown_builtin_tool_no_description(self) -> None:
        ctx = build_runtime_context(model="gpt-4", builtin_tools=["mystery_tool"])
        assert "mystery_tool" in ctx
        assert "mystery_tool:" not in ctx  # no colon+space since no description

    def test_mcp_servers_listed(self) -> None:
        ctx = build_runtime_context(
            model="gpt-4",
            mcp_servers={
                "my-server": {"status": "connected", "tool_count": 3, "tools": [{"name": "do_thing"}]},
            },
        )
        assert "MCP servers:" in ctx
        assert "my-server: connected (3 tools)" in ctx
        assert "do_thing" in ctx

    def test_mcp_server_not_connected_tools_not_listed(self) -> None:
        ctx = build_runtime_context(
            model="gpt-4",
            mcp_servers={
                "my-server": {"status": "disconnected", "tool_count": 0},
            },
        )
        assert "disconnected" in ctx
        assert "Available tools:" not in ctx

    def test_mcp_tools_as_string_names(self) -> None:
        ctx = build_runtime_context(
            model="gpt-4",
            mcp_servers={
                "srv": {"status": "connected", "tool_count": 1, "tools": ["plain_tool_name"]},
            },
        )
        assert "plain_tool_name" in ctx

    def test_working_dir_shown_for_cli(self) -> None:
        ctx = build_runtime_context(model="gpt-4", interface="cli", working_dir="/tmp/project")
        assert "Working directory: /tmp/project" in ctx

    def test_working_dir_not_shown_for_web(self) -> None:
        ctx = build_runtime_context(model="gpt-4", interface="web", working_dir="/tmp/project")
        assert "Working directory" not in ctx

    def test_tls_shown_for_web(self) -> None:
        ctx = build_runtime_context(model="gpt-4", interface="web", tls_enabled=True)
        assert "TLS: enabled" in ctx

    def test_tls_disabled_for_web(self) -> None:
        ctx = build_runtime_context(model="gpt-4", interface="web", tls_enabled=False)
        assert "TLS: disabled" in ctx

    def test_tls_not_shown_for_cli(self) -> None:
        ctx = build_runtime_context(model="gpt-4", interface="cli", tls_enabled=True)
        assert "TLS:" not in ctx


# ---------------------------------------------------------------------------
# AI timeout env var overrides (lines 911-968)
# ---------------------------------------------------------------------------


class TestAITimeoutEnvVars:
    def test_request_timeout_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_REQUEST_TIMEOUT", "300")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.request_timeout == 300

    def test_request_timeout_clamped_max(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_REQUEST_TIMEOUT", "9999")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.request_timeout == 600

    def test_request_timeout_clamped_min(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_REQUEST_TIMEOUT", "1")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.request_timeout == 10

    def test_request_timeout_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_REQUEST_TIMEOUT", "notanumber")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.request_timeout == 120

    def test_connect_timeout_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_CONNECT_TIMEOUT", "10")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.connect_timeout == 10

    def test_connect_timeout_clamped_max(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_CONNECT_TIMEOUT", "999")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.connect_timeout == 30

    def test_connect_timeout_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_CONNECT_TIMEOUT", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.connect_timeout == 5

    def test_write_timeout_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_WRITE_TIMEOUT", "60")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.write_timeout == 60

    def test_write_timeout_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_WRITE_TIMEOUT", "x")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.write_timeout == 30

    def test_pool_timeout_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_POOL_TIMEOUT", "30")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.pool_timeout == 30

    def test_pool_timeout_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_POOL_TIMEOUT", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.pool_timeout == 10

    def test_first_token_timeout_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_FIRST_TOKEN_TIMEOUT", "45")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.first_token_timeout == 45

    def test_first_token_timeout_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_FIRST_TOKEN_TIMEOUT", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.first_token_timeout == 30

    def test_chunk_stall_timeout_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_CHUNK_STALL_TIMEOUT", "60")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.chunk_stall_timeout == 60

    def test_chunk_stall_timeout_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_CHUNK_STALL_TIMEOUT", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.chunk_stall_timeout == 30

    def test_retry_max_attempts_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RETRY_MAX_ATTEMPTS", "5")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.retry_max_attempts == 5

    def test_retry_max_attempts_clamped_max(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RETRY_MAX_ATTEMPTS", "999")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.retry_max_attempts == 10

    def test_retry_max_attempts_clamped_min(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RETRY_MAX_ATTEMPTS", "-5")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.retry_max_attempts == 0

    def test_retry_max_attempts_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RETRY_MAX_ATTEMPTS", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.retry_max_attempts == 3

    def test_retry_backoff_base_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RETRY_BACKOFF_BASE", "2.5")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.retry_backoff_base == 2.5

    def test_retry_backoff_base_clamped_min(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RETRY_BACKOFF_BASE", "0.0")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.retry_backoff_base == 0.1

    def test_retry_backoff_base_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "https://api.example.com", "api_key": "sk-test", "retry_backoff_base": "notanumber"}},
        )
        config, _ = load_config(cfg)
        assert config.ai.retry_backoff_base == 1.0

    def test_max_tools_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_MAX_TOOLS", "64")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.max_tools == 64

    def test_max_tools_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_MAX_TOOLS", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.max_tools == 128

    def test_verify_ssl_env_var_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_VERIFY_SSL", "false")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.verify_ssl is False

    def test_verify_ssl_env_var_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_VERIFY_SSL", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.verify_ssl is True

    def test_verify_ssl_env_var_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_VERIFY_SSL", "0")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.verify_ssl is False


# ---------------------------------------------------------------------------
# allowed_domains and block_localhost_api (lines 994-1003)
# ---------------------------------------------------------------------------


class TestAllowedDomainsEnvVar:
    def test_allowed_domains_env_var_comma_separated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_ALLOWED_DOMAINS", "api.example.com,other.example.com")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.allowed_domains == ["api.example.com", "other.example.com"]

    def test_allowed_domains_env_var_overrides_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_ALLOWED_DOMAINS", "env-domain.com")
        cfg = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test",
                    "allowed_domains": ["yaml-domain.com"],
                }
            },
        )
        config, _ = load_config(cfg)
        assert config.ai.allowed_domains == ["env-domain.com"]

    def test_allowed_domains_yaml_when_no_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_CHAT_ALLOWED_DOMAINS", raising=False)
        cfg = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test",
                    "allowed_domains": ["yaml-domain.com"],
                }
            },
        )
        config, _ = load_config(cfg)
        assert config.ai.allowed_domains == ["yaml-domain.com"]

    def test_allowed_domains_non_list_yaml_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_CHAT_ALLOWED_DOMAINS", raising=False)
        cfg = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test",
                    "allowed_domains": "notalist",
                }
            },
        )
        config, _ = load_config(cfg)
        assert config.ai.allowed_domains == []

    def test_block_localhost_api_env_var_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BLOCK_LOCALHOST_API", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.block_localhost_api is True

    def test_block_localhost_api_default_false(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.ai.block_localhost_api is False


# ---------------------------------------------------------------------------
# Port env var and invalid port (lines 1045-1050)
# ---------------------------------------------------------------------------


class TestPortConfig:
    def test_port_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_PORT", "9090")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.app.port == 9090

    def test_port_invalid_env_var_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_PORT", "notaport")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.app.port == 8080

    def test_port_yaml_overrides_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_PORT", "9999")
        cfg = _write_config(
            tmp_path, {"ai": {"base_url": "https://api.example.com", "api_key": "sk-test"}, "app": {"port": 7777}}
        )
        config, _ = load_config(cfg)
        assert config.app.port == 7777

    def test_port_clamped_max(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "https://api.example.com", "api_key": "sk-test"}, "app": {"port": 99999}},
        )
        config, _ = load_config(cfg)
        assert config.app.port == 65535


# ---------------------------------------------------------------------------
# MCP server disabled flag (line 1062)
# ---------------------------------------------------------------------------


class TestMcpServerDisabled:
    def test_disabled_mcp_server_skipped(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://api.example.com", "api_key": "sk-test"},
                "mcp_servers": [
                    {"name": "active-server", "transport": "stdio", "command": "npx"},
                    {"name": "disabled-server", "transport": "stdio", "command": "npx", "enabled": False},
                ],
            },
        )
        config, _ = load_config(cfg)
        names = [s.name for s in config.mcp_servers]
        assert "active-server" in names
        assert "disabled-server" not in names


# ---------------------------------------------------------------------------
# Shared databases (lines 1099-1123)
# ---------------------------------------------------------------------------


class TestSharedDatabasesConfig:
    def test_shared_databases_parsed(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "shared.db")
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://api.example.com", "api_key": "sk-test"},
                "shared_databases": [{"name": "team-db", "path": db_path, "passphrase_hash": "abc123"}],
            },
        )
        config, _ = load_config(cfg)
        assert len(config.shared_databases) == 1
        assert config.shared_databases[0].name == "team-db"
        assert config.shared_databases[0].passphrase_hash == "abc123"

    def test_disabled_shared_database_skipped(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://api.example.com", "api_key": "sk-test"},
                "shared_databases": [
                    {"name": "active-db", "path": "/tmp/active.db"},
                    {"name": "disabled-db", "path": "/tmp/disabled.db", "enabled": False},
                ],
            },
        )
        config, _ = load_config(cfg)
        names = [db.name for db in config.shared_databases]
        assert "active-db" in names
        assert "disabled-db" not in names

    def test_databases_key_skips_personal(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://api.example.com", "api_key": "sk-test"},
                "databases": {
                    "personal": {"path": "/tmp/personal.db"},
                    "team": {"path": "/tmp/team.db"},
                },
            },
        )
        config, _ = load_config(cfg)
        names = [db.name for db in config.shared_databases]
        assert "personal" not in names
        assert "team" in names


# ---------------------------------------------------------------------------
# CLI config section (lines 1133-1177)
# ---------------------------------------------------------------------------


class TestCliConfig:
    def test_context_warn_tokens_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"context_warn_tokens": 50000}},
        )
        config, _ = load_config(cfg)
        assert config.cli.context_warn_tokens == 50000

    def test_context_warn_tokens_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"context_warn_tokens": "bad"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.context_warn_tokens == 80_000

    def test_context_auto_compact_tokens_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"context_auto_compact_tokens": 75000}},
        )
        config, _ = load_config(cfg)
        assert config.cli.context_auto_compact_tokens == 75000

    def test_context_auto_compact_tokens_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"context_auto_compact_tokens": "bad"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.context_auto_compact_tokens == 100_000

    def test_retry_delay_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"retry_delay": 10.0}},
        )
        config, _ = load_config(cfg)
        assert config.cli.retry_delay == 10.0

    def test_retry_delay_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"retry_delay": "bad"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.retry_delay == 5.0

    def test_max_retries_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"max_retries": 5}},
        )
        config, _ = load_config(cfg)
        assert config.cli.max_retries == 5

    def test_max_retries_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"max_retries": "bad"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.max_retries == 3

    def test_esc_hint_delay_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"esc_hint_delay": 1.5}},
        )
        config, _ = load_config(cfg)
        assert config.cli.esc_hint_delay == 1.5

    def test_esc_hint_delay_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"esc_hint_delay": "bad"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.esc_hint_delay == 3.0

    def test_tool_output_max_chars_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"tool_output_max_chars": 500}},
        )
        config, _ = load_config(cfg)
        assert config.cli.tool_output_max_chars == 500

    def test_tool_output_max_chars_clamped_min(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"tool_output_max_chars": 10}},
        )
        config, _ = load_config(cfg)
        assert config.cli.tool_output_max_chars == 100

    def test_tool_output_max_chars_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"tool_output_max_chars": "bad"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.tool_output_max_chars == 2000

    def test_file_reference_max_chars_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"file_reference_max_chars": 50000}},
        )
        config, _ = load_config(cfg)
        assert config.cli.file_reference_max_chars == 50000

    def test_model_context_window_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"model_context_window": 200000}},
        )
        config, _ = load_config(cfg)
        assert config.cli.model_context_window == 200000

    def test_model_context_window_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"model_context_window": "bad"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.model_context_window == 128_000

    def test_stall_display_threshold_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"stall_display_threshold": 10.0}},
        )
        config, _ = load_config(cfg)
        assert config.cli.stall_display_threshold == 10.0

    def test_stall_display_threshold_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"stall_display_threshold": "bad"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.stall_display_threshold == 5.0

    def test_stall_warning_threshold_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"stall_warning_threshold": 20.0}},
        )
        config, _ = load_config(cfg)
        assert config.cli.stall_warning_threshold == 20.0

    def test_stall_warning_threshold_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"stall_warning_threshold": "bad"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.stall_warning_threshold == 15.0

    def test_planning_not_a_dict_falls_back_to_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"planning": "notadict"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.planning.enabled is True

    def test_skills_auto_invoke_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"skills": {"auto_invoke": False}}},
        )
        config, _ = load_config(cfg)
        assert config.cli.skills.auto_invoke is False

    def test_skills_not_a_dict_uses_default(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"skills": "notadict"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.skills.auto_invoke is True


# ---------------------------------------------------------------------------
# Usage config (lines 1199-1224)
# ---------------------------------------------------------------------------


class TestUsageConfig:
    def test_usage_week_days_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"usage": {"week_days": 14}}},
        )
        config, _ = load_config(cfg)
        assert config.cli.usage.week_days == 14

    def test_usage_week_days_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"usage": {"week_days": "bad"}}},
        )
        config, _ = load_config(cfg)
        assert config.cli.usage.week_days == 7

    def test_usage_month_days_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"usage": {"month_days": 60}}},
        )
        config, _ = load_config(cfg)
        assert config.cli.usage.month_days == 60

    def test_usage_month_days_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"usage": {"month_days": "bad"}}},
        )
        config, _ = load_config(cfg)
        assert config.cli.usage.month_days == 30

    def test_usage_model_costs_merged(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "cli": {"usage": {"model_costs": {"my-model": {"input": 0.001, "output": 0.002}}}},
            },
        )
        config, _ = load_config(cfg)
        assert "my-model" in config.cli.usage.model_costs
        assert config.cli.usage.model_costs["my-model"]["input"] == 0.001

    def test_usage_model_costs_not_a_dict_ignored(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"usage": {"model_costs": "notadict"}}},
        )
        config, _ = load_config(cfg)
        assert isinstance(config.cli.usage.model_costs, dict)

    def test_usage_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"usage": "notadict"}},
        )
        config, _ = load_config(cfg)
        assert config.cli.usage.week_days == 7


# ---------------------------------------------------------------------------
# Budget config (lines 1226-1301)
# ---------------------------------------------------------------------------


class TestBudgetConfig:
    def test_budget_enabled_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_ENABLED", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.enabled is True

    def test_budget_disabled_by_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.enabled is False

    def test_budget_max_per_request_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_MAX_TOKENS_PER_REQUEST", "5000")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.max_tokens_per_request == 5000

    def test_budget_max_per_request_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_MAX_TOKENS_PER_REQUEST", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.max_tokens_per_request == 0

    def test_budget_max_per_conversation_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_MAX_TOKENS_PER_CONVERSATION", "50000")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.max_tokens_per_conversation == 50000

    def test_budget_max_per_day_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_MAX_TOKENS_PER_DAY", "1000000")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.max_tokens_per_day == 1000000

    def test_budget_warn_threshold_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_WARN_THRESHOLD_PERCENT", "90")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.warn_threshold_percent == 90

    def test_budget_warn_threshold_clamped_max(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_WARN_THRESHOLD_PERCENT", "200")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.warn_threshold_percent == 100

    def test_budget_warn_threshold_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_WARN_THRESHOLD_PERCENT", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.warn_threshold_percent == 80

    def test_budget_action_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_ACTION_ON_EXCEED", "warn")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.action_on_exceed == "warn"

    def test_budget_action_invalid_falls_back_to_block(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BUDGET_ACTION_ON_EXCEED", "garbage")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.action_on_exceed == "block"

    def test_budget_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "cli": {
                    "usage": {
                        "budgets": {
                            "enabled": True,
                            "max_tokens_per_request": 2000,
                            "max_tokens_per_conversation": 20000,
                            "max_tokens_per_day": 100000,
                            "warn_threshold_percent": 75,
                            "action_on_exceed": "warn",
                        }
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        b = config.cli.usage.budgets
        assert b.enabled is True
        assert b.max_tokens_per_request == 2000
        assert b.max_tokens_per_conversation == 20000
        assert b.max_tokens_per_day == 100000
        assert b.warn_threshold_percent == 75
        assert b.action_on_exceed == "warn"

    def test_budgets_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "cli": {"usage": {"budgets": "notadict"}}},
        )
        config, _ = load_config(cfg)
        assert config.cli.usage.budgets.enabled is False


# ---------------------------------------------------------------------------
# Safety config (lines 1376-1478)
# ---------------------------------------------------------------------------


class TestSafetyConfig:
    def test_safety_enabled_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.enabled is True

    def test_safety_enabled_env_var_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SAFETY_ENABLED", "false")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.enabled is False

    def test_safety_approval_mode_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SAFETY_APPROVAL_MODE", "auto")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.approval_mode == "auto"

    def test_safety_approval_mode_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"approval_mode": "ask"},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.approval_mode == "ask"

    def test_read_only_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_READ_ONLY", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.read_only is True

    def test_read_only_default_false(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.read_only is False

    def test_safety_allowed_tools_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"allowed_tools": ["bash", "read_file"]},
            },
        )
        config, _ = load_config(cfg)
        assert "bash" in config.safety.allowed_tools
        assert "read_file" in config.safety.allowed_tools

    def test_safety_allowed_tools_not_list_ignored(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"allowed_tools": "notalist"},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.allowed_tools == []

    def test_safety_denied_tools_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"denied_tools": ["run_agent"]},
            },
        )
        config, _ = load_config(cfg)
        assert "run_agent" in config.safety.denied_tools

    def test_safety_tool_tiers_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"tool_tiers": {"bash": "READ"}},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.tool_tiers.get("bash") == "READ"

    def test_safety_tool_tiers_not_dict_ignored(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"tool_tiers": "notadict"},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.tool_tiers == {}

    def test_safety_custom_patterns_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"custom_patterns": ["rm -rf", "sudo"]},
            },
        )
        config, _ = load_config(cfg)
        assert "rm -rf" in config.safety.custom_patterns

    def test_safety_sensitive_paths_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"sensitive_paths": ["/etc/passwd"]},
            },
        )
        config, _ = load_config(cfg)
        assert "/etc/passwd" in config.safety.sensitive_paths


# ---------------------------------------------------------------------------
# Bash sandbox config (lines 1384-1453)
# ---------------------------------------------------------------------------


class TestBashSandboxConfig:
    def test_bash_timeout_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_TIMEOUT", "60")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.timeout == 60

    def test_bash_timeout_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_TIMEOUT", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.timeout == 120

    def test_bash_max_output_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_MAX_OUTPUT", "50000")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.max_output_chars == 50000

    def test_bash_blocked_paths_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_BLOCKED_PATHS", "/etc,/root")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert "/etc" in config.safety.bash.blocked_paths
        assert "/root" in config.safety.bash.blocked_paths

    def test_bash_blocked_commands_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_BLOCKED_COMMANDS", "curl,wget")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert "curl" in config.safety.bash.blocked_commands
        assert "wget" in config.safety.bash.blocked_commands

    def test_bash_allowed_paths_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_ALLOWED_PATHS", "/tmp,/home/user")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert "/tmp" in config.safety.bash.allowed_paths

    def test_bash_allow_network_env_var_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_ALLOW_NETWORK", "false")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.allow_network is False

    def test_bash_allow_package_install_env_var_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_ALLOW_PACKAGE_INSTALL", "false")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.allow_package_install is False

    def test_bash_log_all_commands_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_LOG_ALL_COMMANDS", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.log_all_commands is True

    def test_bash_blocked_paths_from_yaml_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_CHAT_BASH_BLOCKED_PATHS", raising=False)
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"bash": {"blocked_paths": ["/etc", "/var"]}},
            },
        )
        config, _ = load_config(cfg)
        assert "/etc" in config.safety.bash.blocked_paths
        assert "/var" in config.safety.bash.blocked_paths

    def test_bash_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"bash": "notadict"},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.bash.timeout == 120

    def test_sandbox_enabled_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_SANDBOX_ENABLED", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.sandbox.enabled is True

    def test_sandbox_enabled_none_when_not_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_CHAT_BASH_SANDBOX_ENABLED", raising=False)
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.sandbox.enabled is None

    def test_sandbox_max_memory_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_SANDBOX_MAX_MEMORY_MB", "1024")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.sandbox.max_memory_mb == 1024

    def test_sandbox_cpu_time_limit_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_SANDBOX_CPU_TIME_LIMIT", "30")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.sandbox.cpu_time_limit == 30

    def test_sandbox_cpu_time_limit_none_when_not_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_CHAT_BASH_SANDBOX_CPU_TIME_LIMIT", raising=False)
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.sandbox.cpu_time_limit is None

    def test_sandbox_cpu_time_limit_invalid_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASH_SANDBOX_CPU_TIME_LIMIT", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.bash.sandbox.cpu_time_limit is None

    def test_sandbox_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"bash": {"sandbox": "notadict"}},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.bash.sandbox.max_memory_mb == 512


# ---------------------------------------------------------------------------
# Subagent config (lines 1480-1499)
# ---------------------------------------------------------------------------


class TestSubagentConfig:
    def test_defaults(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        sa = config.safety.subagent
        assert sa.max_concurrent == 5
        assert sa.max_total == 10
        assert sa.max_depth == 3
        assert sa.max_iterations == 15
        assert sa.timeout == 120

    def test_custom_values(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {
                    "subagent": {
                        "max_concurrent": 3,
                        "max_total": 20,
                        "max_depth": 2,
                        "max_iterations": 10,
                        "timeout": 60,
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        sa = config.safety.subagent
        assert sa.max_concurrent == 3
        assert sa.max_total == 20
        assert sa.max_depth == 2
        assert sa.max_iterations == 10
        assert sa.timeout == 60

    def test_invalid_values_clamped_to_bounds(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"subagent": {"max_concurrent": 999, "max_depth": 0}},
            },
        )
        config, _ = load_config(cfg)
        sa = config.safety.subagent
        assert sa.max_concurrent == 20
        assert sa.max_depth == 1

    def test_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"subagent": "notadict"},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.subagent.max_concurrent == 5


# ---------------------------------------------------------------------------
# Tool rate limit config (lines 1501-1521)
# ---------------------------------------------------------------------------


class TestToolRateLimitConfig:
    def test_defaults(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        trl = config.safety.tool_rate_limit
        assert trl.max_calls_per_minute == 0
        assert trl.max_calls_per_conversation == 0
        assert trl.max_consecutive_failures == 5
        assert trl.action == "block"

    def test_custom_values_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {
                    "tool_rate_limit": {
                        "max_calls_per_minute": 60,
                        "max_calls_per_conversation": 500,
                        "max_consecutive_failures": 3,
                        "action": "warn",
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        trl = config.safety.tool_rate_limit
        assert trl.max_calls_per_minute == 60
        assert trl.max_calls_per_conversation == 500
        assert trl.max_consecutive_failures == 3
        assert trl.action == "warn"

    def test_invalid_action_falls_back_to_block(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"tool_rate_limit": {"action": "garbage"}},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.tool_rate_limit.action == "block"

    def test_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"tool_rate_limit": "notadict"},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.tool_rate_limit.max_calls_per_minute == 0


# ---------------------------------------------------------------------------
# DLP config (lines 1523-1572)
# ---------------------------------------------------------------------------


class TestDlpConfig:
    def test_dlp_disabled_by_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.dlp.enabled is False

    def test_dlp_enabled_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_DLP_ENABLED", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.dlp.enabled is True

    def test_dlp_action_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_DLP_ACTION", "block")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.dlp.action == "block"

    def test_dlp_action_invalid_falls_back_to_redact(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_DLP_ACTION", "garbage")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.dlp.action == "redact"

    def test_dlp_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {
                    "dlp": {
                        "enabled": True,
                        "scan_output": False,
                        "scan_input": True,
                        "action": "warn",
                        "redaction_string": "[BLOCKED]",
                        "log_detections": False,
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        dlp = config.safety.dlp
        assert dlp.enabled is True
        assert dlp.scan_output is False
        assert dlp.scan_input is True
        assert dlp.action == "warn"
        assert dlp.redaction_string == "[BLOCKED]"
        assert dlp.log_detections is False

    def test_dlp_patterns_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {
                    "dlp": {
                        "patterns": [
                            {"name": "ssn", "pattern": r"\d{3}-\d{2}-\d{4}", "description": "Social Security"},
                        ]
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        assert len(config.safety.dlp.patterns) == 1
        assert config.safety.dlp.patterns[0].name == "ssn"

    def test_dlp_invalid_patterns_skipped(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {
                    "dlp": {
                        "patterns": [
                            {"name": "valid", "pattern": r"\d+"},
                            {"name": "", "pattern": r"\d+"},  # missing name
                            {"name": "nopat"},  # missing pattern
                            "notadict",
                        ]
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        assert len(config.safety.dlp.patterns) == 1

    def test_dlp_custom_patterns_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {
                    "dlp": {
                        "custom_patterns": [
                            {"name": "employee-id", "pattern": r"EMP\d{6}"},
                        ]
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        assert len(config.safety.dlp.custom_patterns) == 1
        assert config.safety.dlp.custom_patterns[0].name == "employee-id"

    def test_dlp_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"dlp": "notadict"},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.dlp.enabled is False


# ---------------------------------------------------------------------------
# Output filter config (lines 1574-1614)
# ---------------------------------------------------------------------------


class TestOutputFilterConfig:
    def test_output_filter_disabled_by_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.output_filter.enabled is False

    def test_output_filter_enabled_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_OUTPUT_FILTER_ENABLED", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.output_filter.enabled is True

    def test_output_filter_action_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_OUTPUT_FILTER_ACTION", "block")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.output_filter.action == "block"

    def test_output_filter_action_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_OUTPUT_FILTER_ACTION", "garbage")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.safety.output_filter.action == "warn"

    def test_output_filter_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {
                    "output_filter": {
                        "enabled": True,
                        "system_prompt_leak_detection": False,
                        "leak_threshold": 0.7,
                        "action": "redact",
                        "redaction_string": "[REMOVED]",
                        "log_detections": False,
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        of = config.safety.output_filter
        assert of.enabled is True
        assert of.system_prompt_leak_detection is False
        assert of.leak_threshold == 0.7
        assert of.action == "redact"
        assert of.redaction_string == "[REMOVED]"
        assert of.log_detections is False

    def test_output_filter_leak_threshold_clamped(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"output_filter": {"leak_threshold": 999.0}},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.output_filter.leak_threshold == 1.0

    def test_output_filter_leak_threshold_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"output_filter": {"leak_threshold": "bad"}},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.output_filter.leak_threshold == 0.4

    def test_output_filter_custom_patterns(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {
                    "output_filter": {
                        "custom_patterns": [
                            {"name": "secret-key", "pattern": r"sk-[a-z0-9]{32}"},
                        ]
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        assert len(config.safety.output_filter.custom_patterns) == 1
        assert config.safety.output_filter.custom_patterns[0].name == "secret-key"

    def test_output_filter_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "safety": {"output_filter": "notadict"},
            },
        )
        config, _ = load_config(cfg)
        assert config.safety.output_filter.enabled is False


# ---------------------------------------------------------------------------
# RAG config (lines 1634-1670)
# ---------------------------------------------------------------------------


class TestRagConfig:
    def test_rag_defaults(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.enabled is True
        assert config.rag.max_chunks == 10
        assert config.rag.max_tokens == 2000
        assert config.rag.similarity_threshold == 0.5

    def test_rag_enabled_env_var_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RAG_ENABLED", "false")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.enabled is False

    def test_rag_max_chunks_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RAG_MAX_CHUNKS", "20")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.max_chunks == 20

    def test_rag_max_chunks_clamped_max(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RAG_MAX_CHUNKS", "999")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.max_chunks == 50

    def test_rag_max_chunks_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RAG_MAX_CHUNKS", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.max_chunks == 10

    def test_rag_max_tokens_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RAG_MAX_TOKENS", "5000")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.max_tokens == 5000

    def test_rag_max_tokens_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RAG_MAX_TOKENS", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.max_tokens == 2000

    def test_rag_similarity_threshold_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RAG_SIMILARITY_THRESHOLD", "0.8")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.similarity_threshold == 0.8

    def test_rag_similarity_threshold_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RAG_SIMILARITY_THRESHOLD", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.similarity_threshold == 0.5

    def test_rag_not_a_dict_rejected_by_validator(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "rag": "notadict"},
        )
        with pytest.raises(ValueError, match="expected dict"):
            load_config(cfg)

    def test_rag_include_exclude_flags(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "rag": {"include_sources": False, "include_conversations": False, "exclude_current": False},
            },
        )
        config, _ = load_config(cfg)
        assert config.rag.include_sources is False
        assert config.rag.include_conversations is False
        assert config.rag.exclude_current is False

    def test_rag_retrieval_mode_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.retrieval_mode == "dense"

    def test_rag_retrieval_mode_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "rag": {"retrieval_mode": "hybrid"},
            },
        )
        config, _ = load_config(cfg)
        assert config.rag.retrieval_mode == "hybrid"

    def test_rag_retrieval_mode_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RAG_RETRIEVAL_MODE", "keyword")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rag.retrieval_mode == "keyword"

    def test_rag_retrieval_mode_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "rag": {"retrieval_mode": "bogus"},
            },
        )
        config, _ = load_config(cfg)
        assert config.rag.retrieval_mode == "dense"


# ---------------------------------------------------------------------------
# Proxy config (lines 1672-1694)
# ---------------------------------------------------------------------------


class TestProxyConfig:
    def test_proxy_disabled_by_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.proxy.enabled is False

    def test_proxy_enabled_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_PROXY_ENABLED", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.proxy.enabled is True

    def test_proxy_allowed_origins_valid(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "proxy": {
                    "enabled": True,
                    "allowed_origins": ["https://app.example.com", "https://other.example.com"],
                },
            },
        )
        config, _ = load_config(cfg)
        assert "https://app.example.com" in config.proxy.allowed_origins
        assert "https://other.example.com" in config.proxy.allowed_origins

    def test_proxy_wildcard_origin_rejected(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "proxy": {"enabled": True, "allowed_origins": ["*", "https://valid.example.com"]},
            },
        )
        config, _ = load_config(cfg)
        assert "*" not in config.proxy.allowed_origins
        assert "https://valid.example.com" in config.proxy.allowed_origins

    def test_proxy_non_http_origin_rejected(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "proxy": {"allowed_origins": ["ftp://bad.example.com", "https://good.example.com"]},
            },
        )
        config, _ = load_config(cfg)
        assert "ftp://bad.example.com" not in config.proxy.allowed_origins

    def test_proxy_not_a_dict_rejected_by_validator(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "proxy": "notadict"},
        )
        with pytest.raises(ValueError, match="expected dict"):
            load_config(cfg)

    def test_proxy_origins_not_a_list_ignored(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "proxy": {"allowed_origins": "notalist"},
            },
        )
        config, _ = load_config(cfg)
        assert config.proxy.allowed_origins == []


# ---------------------------------------------------------------------------
# Storage config (lines 1706-1743)
# ---------------------------------------------------------------------------


class TestStorageConfig:
    def test_storage_defaults(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.storage.retention_days == 0
        assert config.storage.encrypt_at_rest is False

    def test_storage_retention_days_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_STORAGE_RETENTION_DAYS", "90")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.storage.retention_days == 90

    def test_storage_retention_days_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_STORAGE_RETENTION_DAYS", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.storage.retention_days == 0

    def test_storage_check_interval_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_STORAGE_CHECK_INTERVAL", "7200")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.storage.retention_check_interval == 7200

    def test_storage_check_interval_clamped_min(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_STORAGE_CHECK_INTERVAL", "10")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.storage.retention_check_interval == 60

    def test_storage_purge_attachments_env_var_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_STORAGE_PURGE_ATTACHMENTS", "false")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.storage.purge_attachments is False

    def test_storage_purge_embeddings_env_var_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_STORAGE_PURGE_EMBEDDINGS", "false")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.storage.purge_embeddings is False

    def test_storage_encrypt_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_STORAGE_ENCRYPT", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.storage.encrypt_at_rest is True

    def test_storage_encryption_kdf_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "storage": {"encryption_kdf": "md5-oops"},
            },
        )
        config, _ = load_config(cfg)
        assert config.storage.encryption_kdf == "hkdf-sha256"

    def test_storage_not_a_dict_rejected_by_validator(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "storage": "notadict"},
        )
        with pytest.raises(ValueError, match="expected dict"):
            load_config(cfg)


# ---------------------------------------------------------------------------
# Session config (lines 1745-1801)
# ---------------------------------------------------------------------------


class TestSessionConfig:
    def test_session_defaults(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.session.store == "memory"
        assert config.session.max_concurrent_sessions == 0
        assert config.session.idle_timeout == 1800
        assert config.session.absolute_timeout == 43200

    def test_session_store_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SESSION_STORE", "sqlite")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.session.store == "sqlite"

    def test_session_store_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SESSION_STORE", "redis")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.session.store == "memory"

    def test_session_max_concurrent_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SESSION_MAX_CONCURRENT", "5")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.session.max_concurrent_sessions == 5

    def test_session_max_concurrent_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SESSION_MAX_CONCURRENT", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.session.max_concurrent_sessions == 0

    def test_session_idle_timeout_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SESSION_IDLE_TIMEOUT", "3600")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.session.idle_timeout == 3600

    def test_session_idle_timeout_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SESSION_IDLE_TIMEOUT", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.session.idle_timeout == 1800

    def test_session_absolute_timeout_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SESSION_ABSOLUTE_TIMEOUT", "86400")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.session.absolute_timeout == 86400

    def test_session_allowed_ips_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_SESSION_ALLOWED_IPS", "192.168.1.0/24,10.0.0.1")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert "192.168.1.0/24" in config.session.allowed_ips
        assert "10.0.0.1" in config.session.allowed_ips

    def test_session_allowed_ips_yaml_takes_precedence_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_CHAT_SESSION_ALLOWED_IPS", "10.0.0.1")
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "session": {"allowed_ips": ["192.168.1.1"]},
            },
        )
        config, _ = load_config(cfg)
        assert "192.168.1.1" in config.session.allowed_ips
        assert "10.0.0.1" not in config.session.allowed_ips

    def test_session_not_a_dict_rejected_by_validator(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "session": "notadict"},
        )
        with pytest.raises(ValueError, match="expected dict"):
            load_config(cfg)


# ---------------------------------------------------------------------------
# Audit config (lines 1803-1846)
# ---------------------------------------------------------------------------


class TestAuditConfig:
    def test_audit_disabled_by_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.audit.enabled is False

    def test_audit_enabled_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_AUDIT_ENABLED", "true")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.audit.enabled is True

    def test_audit_log_path_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_AUDIT_LOG_PATH", "/var/log/anteroom/audit.jsonl")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.audit.log_path == "/var/log/anteroom/audit.jsonl"

    def test_audit_tamper_protection_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_AUDIT_TAMPER_PROTECTION", "none")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.audit.tamper_protection == "none"

    def test_audit_tamper_protection_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_AUDIT_TAMPER_PROTECTION", "sha256")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.audit.tamper_protection == "hmac"

    def test_audit_rotation_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "audit": {"rotation": "weekly"},
            },
        )
        config, _ = load_config(cfg)
        assert config.audit.rotation == "daily"

    def test_audit_rotate_size_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "audit": {"rotate_size_bytes": 20_000_000},
            },
        )
        config, _ = load_config(cfg)
        assert config.audit.rotate_size_bytes == 20_000_000

    def test_audit_rotate_size_clamped_min(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "audit": {"rotate_size_bytes": 100},
            },
        )
        config, _ = load_config(cfg)
        assert config.audit.rotate_size_bytes == 1_048_576

    def test_audit_rotate_size_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "audit": {"rotate_size_bytes": "bad"},
            },
        )
        config, _ = load_config(cfg)
        assert config.audit.rotate_size_bytes == 10_485_760

    def test_audit_retention_days_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_AUDIT_RETENTION_DAYS", "30")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.audit.retention_days == 30

    def test_audit_retention_days_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_AUDIT_RETENTION_DAYS", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.audit.retention_days == 90

    def test_audit_redact_content_env_var_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_AUDIT_REDACT_CONTENT", "false")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.audit.redact_content is False

    def test_audit_events_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "audit": {
                    "events": {
                        "auth": False,
                        "tool_calls": True,
                        "dlp": False,
                        "output_filter": True,
                    }
                },
            },
        )
        config, _ = load_config(cfg)
        assert config.audit.events["auth"] is False
        assert config.audit.events["tool_calls"] is True
        assert config.audit.events["dlp"] is False

    def test_audit_events_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "audit": {"events": "notadict"},
            },
        )
        config, _ = load_config(cfg)
        assert config.audit.events["auth"] is True

    def test_audit_not_a_dict_rejected_by_validator(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "audit": "notadict"},
        )
        with pytest.raises(ValueError, match="expected dict"):
            load_config(cfg)


# ---------------------------------------------------------------------------
# Codebase index config (lines 1848-1864)
# ---------------------------------------------------------------------------


class TestCodebaseIndexConfig:
    def test_ci_defaults(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.codebase_index.enabled is True
        assert config.codebase_index.map_tokens == 1000

    def test_ci_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "codebase_index": {
                    "enabled": False,
                    "map_tokens": 500,
                    "languages": ["python", "javascript"],
                    "exclude_dirs": ["node_modules", ".git"],
                },
            },
        )
        config, _ = load_config(cfg)
        assert config.codebase_index.enabled is False
        assert config.codebase_index.map_tokens == 500
        assert "python" in config.codebase_index.languages
        assert "node_modules" in config.codebase_index.exclude_dirs

    def test_ci_languages_not_a_list_ignored(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "codebase_index": {"languages": "notalist"},
            },
        )
        config, _ = load_config(cfg)
        assert config.codebase_index.languages == []

    def test_ci_not_a_dict_rejected_by_validator(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "codebase_index": "notadict"},
        )
        with pytest.raises(ValueError, match="expected dict"):
            load_config(cfg)


# ---------------------------------------------------------------------------
# Compliance config (lines 1866-1896)
# ---------------------------------------------------------------------------


class TestComplianceConfig:
    def test_compliance_empty_by_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.compliance.rules == []

    def test_compliance_rules_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "compliance": {
                    "rules": [
                        {"field": "safety.approval_mode", "must_be": "ask", "message": "Must use ask mode"},
                    ]
                },
            },
        )
        config, _ = load_config(cfg)
        assert len(config.compliance.rules) == 1
        assert config.compliance.rules[0].field == "safety.approval_mode"

    def test_compliance_rules_skip_invalid_entries(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "compliance": {
                    "rules": [
                        {"field": "valid.field", "must_be": "value"},
                        {"field": "", "must_be": "value"},  # empty field ignored
                        "notadict",  # not a dict, ignored
                    ]
                },
            },
        )
        config, _ = load_config(cfg)
        assert len(config.compliance.rules) == 1

    def test_compliance_must_match_compiled(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "compliance": {
                    "rules": [
                        {"field": "ai.model", "must_match": r"^gpt-4.*", "message": "Must use GPT-4"},
                    ]
                },
            },
        )
        config, _ = load_config(cfg)
        rule = config.compliance.rules[0]
        assert rule.must_match == r"^gpt-4.*"
        assert rule._compiled_pattern is not None

    def test_compliance_invalid_regex_compiled_is_none(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "compliance": {
                    "rules": [
                        {"field": "ai.model", "must_match": r"[invalid", "message": "Bad regex"},
                    ]
                },
            },
        )
        config, _ = load_config(cfg)
        rule = config.compliance.rules[0]
        assert rule._compiled_pattern is None

    def test_compliance_not_a_dict_rejected_by_validator(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "compliance": "notadict"},
        )
        with pytest.raises(ValueError, match="expected dict"):
            load_config(cfg)


# ---------------------------------------------------------------------------
# Pack sources config (lines 1898-1918)
# ---------------------------------------------------------------------------


class TestPackSourcesConfig:
    def test_pack_sources_empty_by_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.pack_sources == []

    def test_pack_sources_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "pack_sources": [
                    {"url": "https://github.com/org/packs.git", "branch": "main", "refresh_interval": 60},
                ],
            },
        )
        config, _ = load_config(cfg)
        assert len(config.pack_sources) == 1
        assert config.pack_sources[0].url == "https://github.com/org/packs.git"
        assert config.pack_sources[0].branch == "main"
        assert config.pack_sources[0].refresh_interval == 60

    def test_pack_sources_skips_entries_without_url(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "pack_sources": [
                    {"url": "https://github.com/org/packs.git"},
                    {"branch": "main"},  # no url
                    "notadict",
                ],
            },
        )
        config, _ = load_config(cfg)
        assert len(config.pack_sources) == 1

    def test_pack_sources_refresh_interval_invalid_falls_back(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "pack_sources": [{"url": "https://github.com/org/packs.git", "refresh_interval": "bad"}],
            },
        )
        config, _ = load_config(cfg)
        assert config.pack_sources[0].refresh_interval == 30

    def test_pack_sources_not_a_list_ignored(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "pack_sources": "notalist",
            },
        )
        config, _ = load_config(cfg)
        assert config.pack_sources == []


# ---------------------------------------------------------------------------
# References config (lines 1696-1704)
# ---------------------------------------------------------------------------


class TestReferencesConfig:
    def test_references_empty_by_default(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.references.instructions == []
        assert config.references.rules == []
        assert config.references.skills == []

    def test_references_from_yaml(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "references": {
                    "instructions": ["/path/to/instructions.md"],
                    "rules": ["/path/to/rules.md"],
                    "skills": ["/path/to/skills.yaml"],
                },
            },
        )
        config, _ = load_config(cfg)
        assert "/path/to/instructions.md" in config.references.instructions
        assert "/path/to/rules.md" in config.references.rules
        assert "/path/to/skills.yaml" in config.references.skills

    def test_references_non_string_entries_ignored(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://t", "api_key": "k"},
                "references": {
                    "instructions": ["valid.md", 123, None, ""],
                },
            },
        )
        config, _ = load_config(cfg)
        assert config.references.instructions == ["valid.md"]

    def test_references_not_a_dict_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            {"ai": {"base_url": "http://t", "api_key": "k"}, "references": "notadict"},
        )
        config, _ = load_config(cfg)
        assert config.references.instructions == []


# ---------------------------------------------------------------------------
# Space config layer in load_config (lines 861-864)
# ---------------------------------------------------------------------------


class TestSpaceConfigLayer:
    def test_space_config_overlays_raw(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        space_config = {"safety": {"approval_mode": "auto"}}
        config, _ = load_config(cfg, space_config=space_config)
        assert config.safety.approval_mode == "auto"

    def test_space_config_none_ignored(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg, space_config=None)
        assert isinstance(config, AppConfig)

    def test_space_config_not_dict_ignored(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg, space_config="notadict")  # type: ignore[arg-type]
        assert isinstance(config, AppConfig)


# ---------------------------------------------------------------------------
# api_key_command fallback (no api_key but has api_key_command)
# ---------------------------------------------------------------------------


class TestApiKeyCommand:
    def test_api_key_command_accepted_without_api_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_CHAT_API_KEY", raising=False)
        cfg = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key_command": "echo sk-from-cmd",
                }
            },
        )
        config, _ = load_config(cfg)
        assert config.ai.api_key_command == "echo sk-from-cmd"
        assert config.ai.api_key == ""

    def test_api_key_command_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_API_KEY_COMMAND", "vault read secret/key")
        monkeypatch.delenv("AI_CHAT_API_KEY", raising=False)
        cfg = _write_config(tmp_path, {"ai": {"base_url": "https://api.example.com"}})
        config, _ = load_config(cfg)
        assert config.ai.api_key_command == "vault read secret/key"


class TestRateLimitConfig:
    def test_rate_limit_defaults(self, tmp_path: Path) -> None:
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rate_limit.max_requests == 120
        assert config.rate_limit.window_seconds == 60
        assert config.rate_limit.exempt_paths == ["/api/events"]
        assert config.rate_limit.sse_retry_ms == 5000

    def test_rate_limit_from_yaml(self, tmp_path: Path) -> None:
        cfg = _minimal(
            tmp_path,
            extra={
                "rate_limit": {
                    "max_requests": 60,
                    "window_seconds": 30,
                    "exempt_paths": ["/api/events", "/health"],
                    "sse_retry_ms": 10000,
                }
            },
        )
        config, _ = load_config(cfg)
        assert config.rate_limit.max_requests == 60
        assert config.rate_limit.window_seconds == 30
        assert config.rate_limit.exempt_paths == ["/api/events", "/health"]
        assert config.rate_limit.sse_retry_ms == 10000

    def test_rate_limit_env_vars(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RATE_LIMIT_MAX_REQUESTS", "200")
        monkeypatch.setenv("AI_CHAT_RATE_LIMIT_WINDOW_SECONDS", "120")
        monkeypatch.setenv("AI_CHAT_RATE_LIMIT_SSE_RETRY_MS", "8000")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rate_limit.max_requests == 200
        assert config.rate_limit.window_seconds == 120
        assert config.rate_limit.sse_retry_ms == 8000

    def test_rate_limit_exempt_paths_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RATE_LIMIT_EXEMPT_PATHS", "/api/events,/health")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rate_limit.exempt_paths == ["/api/events", "/health"]

    def test_rate_limit_invalid_values_fall_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_RATE_LIMIT_MAX_REQUESTS", "bad")
        monkeypatch.setenv("AI_CHAT_RATE_LIMIT_WINDOW_SECONDS", "bad")
        monkeypatch.setenv("AI_CHAT_RATE_LIMIT_SSE_RETRY_MS", "bad")
        cfg = _minimal(tmp_path)
        config, _ = load_config(cfg)
        assert config.rate_limit.max_requests == 120
        assert config.rate_limit.window_seconds == 60
        assert config.rate_limit.sse_retry_ms == 5000

    def test_rate_limit_clamped_to_minimum(self, tmp_path: Path) -> None:
        cfg = _minimal(
            tmp_path,
            extra={
                "rate_limit": {
                    "max_requests": 0,
                    "window_seconds": -5,
                    "sse_retry_ms": 10,
                }
            },
        )
        config, _ = load_config(cfg)
        assert config.rate_limit.max_requests >= 1
        assert config.rate_limit.window_seconds >= 1
        assert config.rate_limit.sse_retry_ms >= 100
