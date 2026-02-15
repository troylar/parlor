"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from parlor.config import AppConfig, load_config


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
        with pytest.raises(ValueError, match="api_key is required"):
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
        assert "Parlor" in config.ai.system_prompt
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
        assert "Parlor" in config.ai.system_prompt
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
        assert "Parlor" in config.ai.system_prompt
        assert "<user_instructions>" not in config.ai.system_prompt

    def test_user_system_prompt_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_CHAT_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("AI_CHAT_API_KEY", "sk-env-key")
        monkeypatch.setenv("AI_CHAT_SYSTEM_PROMPT", "Be very brief.")
        cfg_file = _write_config(tmp_path, {})
        config = load_config(cfg_file)
        assert config.ai.user_system_prompt == "Be very brief."
        assert "Be very brief." in config.ai.system_prompt
        assert "Parlor" in config.ai.system_prompt
