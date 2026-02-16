"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from anteroom.config import AppConfig, UserIdentity, load_config


def _write_config(path: Path, data: dict) -> Path:
    config_file = path / "config.yaml"
    config_file.write_text(yaml.dump(data))
    return config_file


class TestLoadConfig:
    def test_load_valid_config(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                    "model": "gpt-3.5-turbo",
                    "system_prompt": "Be concise.",
                },
                "app": {
                    "host": "0.0.0.0",
                    "port": 9090,
                    "data_dir": str(tmp_path / "data"),
                },
            },
        )
        config = load_config(cfg_file)
        assert isinstance(config, AppConfig)
        assert config.ai.base_url == "https://api.example.com"
        assert config.ai.api_key == "sk-test-key"
        assert config.ai.model == "gpt-3.5-turbo"
        assert config.ai.user_system_prompt == "Be concise."
        assert "Be concise." in config.ai.system_prompt
        assert "<user_instructions>" in config.ai.system_prompt
        assert config.app.host == "0.0.0.0"
        assert config.app.port == 9090

    def test_raises_when_api_key_missing(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                },
            },
        )
        with pytest.raises(ValueError, match="api_key or api_key_command is required"):
            load_config(cfg_file)

    def test_raises_when_base_url_missing(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "api_key": "sk-test-key",
                },
            },
        )
        with pytest.raises(ValueError, match="base_url is required"):
            load_config(cfg_file)

    def test_default_model(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.ai.model == "gpt-4"

    def test_default_system_prompt(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
            },
        )
        config = load_config(cfg_file)
        assert "Anteroom" in config.ai.system_prompt
        assert "<agentic_behavior>" in config.ai.system_prompt

    def test_default_app_settings(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.app.host == "127.0.0.1"
        assert config.app.port == 8080

    def test_mcp_servers_empty_by_default(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.mcp_servers == []

    def test_mcp_servers_parsed(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "mcp_servers": [
                    {
                        "name": "my-server",
                        "transport": "stdio",
                        "command": "python",
                        "args": ["-m", "my_server"],
                    }
                ],
            },
        )
        config = load_config(cfg_file)
        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0].name == "my-server"
        assert config.mcp_servers[0].transport == "stdio"
        assert config.mcp_servers[0].command == "python"
        assert config.mcp_servers[0].args == ["-m", "my_server"]
        assert config.mcp_servers[0].timeout == 30.0  # default

    def test_mcp_server_custom_timeout(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "mcp_servers": [
                    {
                        "name": "slow-server",
                        "transport": "stdio",
                        "command": "python",
                        "timeout": 60,
                    }
                ],
            },
        )
        config = load_config(cfg_file)
        assert config.mcp_servers[0].timeout == 60.0

    def test_nonexistent_config_file_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(ValueError):
            load_config(missing)

    def test_data_dir_created(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "custom_data"
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "app": {
                    "data_dir": str(data_dir),
                },
            },
        )
        load_config(cfg_file)
        assert data_dir.exists()

    def test_env_var_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("AI_CHAT_API_KEY", "sk-env-key")
        cfg_file = _write_config(tmp_path, {})
        config = load_config(cfg_file)
        assert config.ai.base_url == "https://env.example.com"
        assert config.ai.api_key == "sk-env-key"

    def test_user_system_prompt_appends_to_default(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                    "system_prompt": "Always respond in French.",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.ai.user_system_prompt == "Always respond in French."
        assert "Anteroom" in config.ai.system_prompt
        assert "<agentic_behavior>" in config.ai.system_prompt
        assert "<user_instructions>" in config.ai.system_prompt
        assert "Always respond in French." in config.ai.system_prompt

    def test_no_user_system_prompt_uses_default(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.ai.user_system_prompt == ""
        assert "Anteroom" in config.ai.system_prompt
        assert "<user_instructions>" not in config.ai.system_prompt

    def test_user_system_prompt_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("AI_CHAT_API_KEY", "sk-env-key")
        monkeypatch.setenv("AI_CHAT_SYSTEM_PROMPT", "Be very brief.")
        cfg_file = _write_config(tmp_path, {})
        config = load_config(cfg_file)
        assert config.ai.user_system_prompt == "Be very brief."
        assert "Be very brief." in config.ai.system_prompt
        assert "Anteroom" in config.ai.system_prompt


class TestIdentityConfig:
    def test_identity_none_when_missing(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.identity is None

    def test_identity_parsed_from_config(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "identity": {
                    "user_id": "abc-123",
                    "display_name": "Alice",
                    "public_key": "-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----",
                    "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.identity is not None
        assert isinstance(config.identity, UserIdentity)
        assert config.identity.user_id == "abc-123"
        assert config.identity.display_name == "Alice"
        assert "PUBLIC KEY" in config.identity.public_key
        assert "PRIVATE KEY" in config.identity.private_key

    def test_identity_none_when_empty_section(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "identity": {},
            },
        )
        config = load_config(cfg_file)
        assert config.identity is None

    def test_identity_none_when_user_id_empty(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "identity": {
                    "user_id": "",
                    "display_name": "Alice",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.identity is None

    def test_identity_env_var_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("AI_CHAT_API_KEY", "sk-env-key")
        monkeypatch.setenv("AI_CHAT_USER_ID", "env-user-id")
        monkeypatch.setenv("AI_CHAT_DISPLAY_NAME", "EnvUser")
        monkeypatch.setenv("AI_CHAT_PUBLIC_KEY", "env-pub-key")
        monkeypatch.setenv("AI_CHAT_PRIVATE_KEY", "env-priv-key")
        cfg_file = _write_config(tmp_path, {})
        config = load_config(cfg_file)
        assert config.identity is not None
        assert config.identity.user_id == "env-user-id"
        assert config.identity.display_name == "EnvUser"
        assert config.identity.public_key == "env-pub-key"
        assert config.identity.private_key == "env-priv-key"

    def test_identity_config_takes_precedence_over_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_USER_ID", "env-user-id")
        monkeypatch.setenv("AI_CHAT_DISPLAY_NAME", "EnvUser")
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "identity": {
                    "user_id": "config-user-id",
                    "display_name": "ConfigUser",
                    "public_key": "config-pub",
                    "private_key": "config-priv",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.identity is not None
        assert config.identity.user_id == "config-user-id"
        assert config.identity.display_name == "ConfigUser"

    def test_identity_partial_env_vars(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("AI_CHAT_API_KEY", "sk-env-key")
        monkeypatch.setenv("AI_CHAT_USER_ID", "env-user-id")
        cfg_file = _write_config(tmp_path, {})
        config = load_config(cfg_file)
        assert config.identity is not None
        assert config.identity.user_id == "env-user-id"
        assert config.identity.display_name == ""


class TestEnsureIdentity:
    def test_generates_identity_when_missing(self, tmp_path: Path) -> None:
        from anteroom.config import ensure_identity

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"ai": {"base_url": "http://test", "api_key": "sk-test"}}))

        identity = ensure_identity(config_path)
        assert identity.user_id
        assert identity.display_name
        assert identity.public_key
        assert identity.private_key

        data = yaml.safe_load(config_path.read_text())
        assert "identity" in data
        assert data["identity"]["user_id"] == identity.user_id

    def test_returns_existing_identity(self, tmp_path: Path) -> None:
        from anteroom.config import ensure_identity

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://test", "api_key": "sk-test"},
                    "identity": {
                        "user_id": "existing-id",
                        "display_name": "Existing",
                        "public_key": "pub",
                        "private_key": "priv",
                    },
                }
            )
        )

        identity = ensure_identity(config_path)
        assert identity.user_id == "existing-id"
        assert identity.display_name == "Existing"

    def test_creates_config_file_if_missing(self, tmp_path: Path) -> None:
        from anteroom.config import ensure_identity

        config_path = tmp_path / "subdir" / "config.yaml"
        identity = ensure_identity(config_path)
        assert config_path.exists()
        assert identity.user_id

    def test_preserves_existing_config_sections(self, tmp_path: Path) -> None:
        from anteroom.config import ensure_identity

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "ai": {"base_url": "http://test", "api_key": "sk-test", "model": "gpt-4o"},
                    "app": {"port": 9090},
                }
            )
        )

        ensure_identity(config_path)
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["model"] == "gpt-4o"
        assert data["app"]["port"] == 9090
        assert "identity" in data

    def test_idempotent(self, tmp_path: Path) -> None:
        from anteroom.config import ensure_identity

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"ai": {"base_url": "http://test", "api_key": "sk-test"}}))

        identity1 = ensure_identity(config_path)
        identity2 = ensure_identity(config_path)
        assert identity1.user_id == identity2.user_id
        assert identity1.public_key == identity2.public_key
