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

    def test_raises_when_api_key_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_CHAT_API_KEY", raising=False)
        monkeypatch.delenv("AI_CHAT_API_KEY_COMMAND", raising=False)
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
        assert "<tool_use>" in config.ai.system_prompt
        assert "<code_modification>" in config.ai.system_prompt
        assert "<git_operations>" in config.ai.system_prompt
        assert "<investigation>" in config.ai.system_prompt
        assert "<communication>" in config.ai.system_prompt
        assert "<safety>" in config.ai.system_prompt

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

    def test_narration_cadence_default(self, tmp_path: Path) -> None:
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
        assert config.ai.narration_cadence == 5
        assert "<narration>" in config.ai.system_prompt
        assert "every 5 tool calls" in config.ai.system_prompt

    def test_narration_cadence_yaml_override(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                    "narration_cadence": 10,
                },
            },
        )
        config = load_config(cfg_file)
        assert config.ai.narration_cadence == 10
        assert "every 10 tool calls" in config.ai.system_prompt

    def test_narration_cadence_disabled(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                    "narration_cadence": 0,
                },
            },
        )
        config = load_config(cfg_file)
        assert config.ai.narration_cadence == 0
        assert "<narration>" not in config.ai.system_prompt

    def test_narration_cadence_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("AI_CHAT_API_KEY", "sk-env-key")
        monkeypatch.setenv("AI_CHAT_NARRATION_CADENCE", "3")
        cfg_file = _write_config(tmp_path, {})
        config = load_config(cfg_file)
        assert config.ai.narration_cadence == 3
        assert "every 3 tool calls" in config.ai.system_prompt

    def test_narration_cadence_negative_clamped_to_zero(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                    "narration_cadence": -1,
                },
            },
        )
        config = load_config(cfg_file)
        assert config.ai.narration_cadence == 0
        assert "<narration>" not in config.ai.system_prompt

    def test_narration_cadence_invalid_type_falls_back_to_default(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                    "narration_cadence": "bad",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.ai.narration_cadence == 5
        assert "<narration>" in config.ai.system_prompt

    def test_narration_cadence_env_var_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("AI_CHAT_API_KEY", "sk-env-key")
        monkeypatch.setenv("AI_CHAT_NARRATION_CADENCE", "0")
        cfg_file = _write_config(tmp_path, {})
        config = load_config(cfg_file)
        assert config.ai.narration_cadence == 0
        assert "<narration>" not in config.ai.system_prompt

    def test_narration_cadence_yaml_takes_precedence_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_CHAT_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("AI_CHAT_API_KEY", "sk-env-key")
        monkeypatch.setenv("AI_CHAT_NARRATION_CADENCE", "99")
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                    "narration_cadence": 7,
                },
            },
        )
        config = load_config(cfg_file)
        assert config.ai.narration_cadence == 7
        assert "every 7 tool calls" in config.ai.system_prompt


class TestEmbeddingsConfig:
    def test_default_embeddings_config(self, tmp_path: Path) -> None:
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
        assert config.embeddings.enabled is True
        assert config.embeddings.provider == "local"
        assert config.embeddings.model == "text-embedding-3-small"
        assert config.embeddings.dimensions == 0  # auto-detect from provider/model
        assert config.embeddings.local_model == "BAAI/bge-small-en-v1.5"
        assert config.embeddings.base_url == ""
        assert config.embeddings.api_key == ""

    def test_embeddings_from_config(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "embeddings": {
                    "enabled": True,
                    "model": "custom-embed",
                    "dimensions": 768,
                    "base_url": "https://embed.example.com",
                    "api_key": "sk-embed-key",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.embeddings.model == "custom-embed"
        assert config.embeddings.dimensions == 768
        assert config.embeddings.base_url == "https://embed.example.com"
        assert config.embeddings.api_key == "sk-embed-key"

    def test_embeddings_disabled(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "embeddings": {
                    "enabled": False,
                },
            },
        )
        config = load_config(cfg_file)
        assert config.embeddings.enabled is False

    def test_embeddings_env_var_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASE_URL", "https://api.example.com")
        monkeypatch.setenv("AI_CHAT_API_KEY", "sk-test-key")
        monkeypatch.setenv("AI_CHAT_EMBEDDINGS_ENABLED", "false")
        monkeypatch.setenv("AI_CHAT_EMBEDDINGS_MODEL", "text-embedding-ada-002")
        monkeypatch.setenv("AI_CHAT_EMBEDDINGS_DIMENSIONS", "512")
        monkeypatch.setenv("AI_CHAT_EMBEDDINGS_BASE_URL", "https://embed.env.com")
        monkeypatch.setenv("AI_CHAT_EMBEDDINGS_API_KEY", "sk-embed-env")
        cfg_file = _write_config(tmp_path, {})
        config = load_config(cfg_file)
        assert config.embeddings.enabled is False
        assert config.embeddings.model == "text-embedding-ada-002"
        assert config.embeddings.dimensions == 512
        assert config.embeddings.base_url == "https://embed.env.com"
        assert config.embeddings.api_key == "sk-embed-env"

    def test_embeddings_config_precedence_over_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_EMBEDDINGS_MODEL", "env-model")
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {
                    "base_url": "https://api.example.com",
                    "api_key": "sk-test-key",
                },
                "embeddings": {
                    "model": "config-model",
                },
            },
        )
        config = load_config(cfg_file)
        assert config.embeddings.model == "config-model"

    def test_embeddings_dimensions_clamped_to_max(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://api.example.com", "api_key": "sk-test-key"},
                "embeddings": {"dimensions": 99999},
            },
        )
        config = load_config(cfg_file)
        assert config.embeddings.dimensions == 4096

    def test_embeddings_dimensions_clamped_to_min(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://api.example.com", "api_key": "sk-test-key"},
                "embeddings": {"dimensions": -5},
            },
        )
        config = load_config(cfg_file)
        assert config.embeddings.dimensions == 1


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


class TestToolDedupConfig:
    """Tests for cli.tool_dedup config field."""

    def test_tool_dedup_default_true(self, tmp_path: Path) -> None:
        cfg_file = _write_config(tmp_path, {"ai": {"base_url": "http://test", "api_key": "sk-test"}})
        config = load_config(cfg_file)
        assert config.cli.tool_dedup is True

    def test_tool_dedup_yaml_false(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path, {"ai": {"base_url": "http://test", "api_key": "sk-test"}, "cli": {"tool_dedup": False}}
        )
        config = load_config(cfg_file)
        assert config.cli.tool_dedup is False

    def test_tool_dedup_yaml_true_explicit(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path, {"ai": {"base_url": "http://test", "api_key": "sk-test"}, "cli": {"tool_dedup": True}}
        )
        config = load_config(cfg_file)
        assert config.cli.tool_dedup is True

    def test_tool_dedup_env_var_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_TOOL_DEDUP", "false")
        cfg_file = _write_config(tmp_path, {"ai": {"base_url": "http://test", "api_key": "sk-test"}})
        config = load_config(cfg_file)
        assert config.cli.tool_dedup is False

    def test_tool_dedup_env_var_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_TOOL_DEDUP", "0")
        cfg_file = _write_config(tmp_path, {"ai": {"base_url": "http://test", "api_key": "sk-test"}})
        config = load_config(cfg_file)
        assert config.cli.tool_dedup is False

    def test_tool_dedup_env_var_takes_precedence_over_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_CHAT_TOOL_DEDUP", "false")
        cfg_file = _write_config(
            tmp_path, {"ai": {"base_url": "http://test", "api_key": "sk-test"}, "cli": {"tool_dedup": True}}
        )
        config = load_config(cfg_file)
        assert config.cli.tool_dedup is False


class TestPlanningConfig:
    def test_defaults(self, tmp_path: Path) -> None:
        cfg_file = _write_config(tmp_path, {"ai": {"base_url": "http://test", "api_key": "sk-test"}})
        config = load_config(cfg_file)
        assert config.cli.planning.enabled is True
        assert config.cli.planning.auto_threshold_tools == 5
        assert config.cli.planning.auto_mode == "suggest"

    def test_custom_values_from_yaml(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://test", "api_key": "sk-test"},
                "cli": {"planning": {"enabled": False, "auto_threshold_tools": 10}},
            },
        )
        config = load_config(cfg_file)
        assert config.cli.planning.enabled is False
        assert config.cli.planning.auto_threshold_tools == 10

    def test_disabled_via_string(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://test", "api_key": "sk-test"},
                "cli": {"planning": {"enabled": "false"}},
            },
        )
        config = load_config(cfg_file)
        assert config.cli.planning.enabled is False

    def test_invalid_threshold_falls_back_to_default(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://test", "api_key": "sk-test"},
                "cli": {"planning": {"auto_threshold_tools": "bad"}},
            },
        )
        config = load_config(cfg_file)
        assert config.cli.planning.auto_threshold_tools == 5

    def test_empty_planning_section(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://test", "api_key": "sk-test"},
                "cli": {"planning": {}},
            },
        )
        config = load_config(cfg_file)
        assert config.cli.planning.enabled is True
        assert config.cli.planning.auto_threshold_tools == 5
        assert config.cli.planning.auto_mode == "suggest"

    def test_auto_mode_off_from_yaml(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://test", "api_key": "sk-test"},
                "cli": {"planning": {"auto_mode": "off"}},
            },
        )
        config = load_config(cfg_file)
        assert config.cli.planning.auto_mode == "off"

    def test_auto_mode_auto_from_yaml(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://test", "api_key": "sk-test"},
                "cli": {"planning": {"auto_mode": "auto"}},
            },
        )
        config = load_config(cfg_file)
        assert config.cli.planning.auto_mode == "auto"

    def test_auto_mode_invalid_falls_back_to_suggest(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://test", "api_key": "sk-test"},
                "cli": {"planning": {"auto_mode": "garbage"}},
            },
        )
        config = load_config(cfg_file)
        assert config.cli.planning.auto_mode == "suggest"

    def test_auto_threshold_zero_disables(self, tmp_path: Path) -> None:
        cfg_file = _write_config(
            tmp_path,
            {
                "ai": {"base_url": "http://test", "api_key": "sk-test"},
                "cli": {"planning": {"auto_threshold_tools": 0}},
            },
        )
        config = load_config(cfg_file)
        assert config.cli.planning.auto_threshold_tools == 0
