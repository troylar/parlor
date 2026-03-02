"""Tests for team config bootstrap in the init wizard."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from anteroom.cli.setup import _load_team_ai_settings, bootstrap_team_config, run_init_wizard


def _r(p: str | Path) -> Path:
    return Path(p).resolve()


class TestBootstrapTeamConfig:
    def _make_team(self, tmpdir: Path, data: dict | None = None) -> Path:
        team = tmpdir / "team.yaml"
        team.write_text(yaml.dump(data or {"ai": {"base_url": "http://team:8080"}}))
        return team

    @patch("anteroom.cli.setup.console")
    def test_saves_team_path_in_config_data(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(base)
            cfg = base / "config.yaml"
            config_data: dict = {"ai": {"model": "gpt-4"}}

            result = bootstrap_team_config(str(team), config_data, cfg)

            assert result is True
            assert config_data["team_config_path"] == str(team.resolve())

    @patch("anteroom.cli.setup.console")
    def test_trusts_team_config(self, _console) -> None:
        from anteroom.services.trust import check_trust, compute_content_hash

        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(base)
            cfg = base / "config.yaml"

            bootstrap_team_config(str(team), {}, cfg)

            content = team.read_text(encoding="utf-8")
            content_hash = compute_content_hash(content)
            status = check_trust(str(team.resolve()), content_hash, data_dir=base)
            assert status == "trusted"

    @patch("anteroom.cli.setup.console")
    def test_missing_file_returns_false(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            config_data: dict = {}

            result = bootstrap_team_config(str(base / "nope.yaml"), config_data, cfg)

            assert result is False
            assert "team_config_path" not in config_data

    @patch("anteroom.cli.setup.console")
    def test_prompts_for_required_keys(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(
                base,
                {
                    "ai": {"base_url": "http://team:8080"},
                    "required": [
                        {"path": "custom.setting", "description": "A custom value"},
                    ],
                },
            )
            cfg = base / "config.yaml"

            with patch("anteroom.services.required_keys.prompt_for_missing_keys") as mock_prompt:
                mock_prompt.return_value = True
                bootstrap_team_config(str(team), {}, cfg)

                mock_prompt.assert_called_once()
                missing = mock_prompt.call_args[0][0]
                assert len(missing) == 1
                assert missing[0]["path"] == "custom.setting"

    @patch("anteroom.cli.setup.console")
    def test_skips_prompt_when_keys_satisfied(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(
                base,
                {
                    "required": [{"path": "ai.model", "description": "Model"}],
                },
            )
            cfg = base / "config.yaml"
            config_data = {"ai": {"model": "gpt-4"}}

            with patch("anteroom.services.required_keys.prompt_for_missing_keys") as mock_prompt:
                bootstrap_team_config(str(team), config_data, cfg)
                mock_prompt.assert_not_called()

    @patch("anteroom.cli.setup.console")
    def test_no_required_section(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(base, {"ai": {"model": "llama3"}})
            cfg = base / "config.yaml"

            result = bootstrap_team_config(str(team), {}, cfg)
            assert result is True

    @patch("anteroom.cli.setup.console")
    def test_invalid_required_entries_filtered(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(
                base,
                {
                    "required": [
                        {"path": "ai.api_key", "description": "Valid"},
                        "just a string",
                        {"no_path": True},
                    ],
                },
            )
            cfg = base / "config.yaml"

            with patch("anteroom.services.required_keys.prompt_for_missing_keys") as mock_prompt:
                mock_prompt.return_value = True
                bootstrap_team_config(str(team), {}, cfg)

                missing = mock_prompt.call_args[0][0]
                assert len(missing) == 1
                assert missing[0]["path"] == "ai.api_key"


class TestLoadTeamAiSettings:
    def test_returns_ai_section(self, tmp_path: Path) -> None:
        team = tmp_path / "team.yaml"
        team.write_text(yaml.dump({"ai": {"base_url": "http://team:8080", "model": "llama3"}}))
        result = _load_team_ai_settings(str(team))
        assert result == {"base_url": "http://team:8080", "model": "llama3"}

    def test_returns_empty_when_no_path(self) -> None:
        assert _load_team_ai_settings(None) == {}

    def test_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        assert _load_team_ai_settings(str(tmp_path / "nope.yaml")) == {}

    def test_returns_empty_when_no_ai_section(self, tmp_path: Path) -> None:
        team = tmp_path / "team.yaml"
        team.write_text(yaml.dump({"required": []}))
        assert _load_team_ai_settings(str(team)) == {}

    def test_returns_empty_on_invalid_yaml(self, tmp_path: Path) -> None:
        team = tmp_path / "team.yaml"
        team.write_text(": invalid: yaml: [")
        assert _load_team_ai_settings(str(team)) == {}


class TestWizardSkipsTeamProvidedSettings:
    """Test that run_init_wizard skips prompts when team config provides AI settings."""

    def _make_team(self, tmpdir: Path, data: dict) -> Path:
        team = tmpdir / "team.yaml"
        team.write_text(yaml.dump(data))
        return team

    def test_skips_provider_and_model_when_team_provides_all(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        team = self._make_team(
            tmp_path,
            {"ai": {"base_url": "http://team:8080", "api_key": "sk-team-key", "model": "team-model"}},
        )

        prompts_asked: list[str] = []

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            prompts_asked.append(prompt)
            if "Display name" in prompt:
                return "TestUser"
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Test connection now?" in prompt:
                return False
            if "Set a custom system prompt?" in prompt:
                return False
            if "Write configuration?" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_init_wizard(team_config_path=str(team))

        assert result is True
        # Provider, Base URL, and Model prompts should NOT have been asked
        for prompt_text in prompts_asked:
            assert "Provider" not in prompt_text
            assert "Base URL" not in prompt_text
            assert "Model" not in prompt_text

        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["base_url"] == "http://team:8080"
        assert data["ai"]["model"] == "team-model"
        assert data["ai"]["api_key"] == "sk-team-key"
        assert data["team_config_path"] == str(team.resolve())

    def test_prompts_for_model_when_team_provides_only_base_url(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        team = self._make_team(tmp_path, {"ai": {"base_url": "http://team:8080"}})

        prompts_asked: list[str] = []

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            prompts_asked.append(prompt)
            if "Display name" in prompt:
                return "TestUser"
            if "Model" in prompt:
                return "my-model"
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Use a command to fetch" in prompt:
                return False
            if "Test connection now?" in prompt:
                return False
            if "Set a custom system prompt?" in prompt:
                return False
            if "Write configuration?" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
            patch("anteroom.cli.setup.getpass.getpass", return_value="sk-user-key"),
        ):
            result = run_init_wizard(team_config_path=str(team))

        assert result is True
        # Should have prompted for model since team didn't provide it
        assert any("Model" in p for p in prompts_asked)
        # Should NOT have prompted for provider or base URL
        assert not any("Provider" in p for p in prompts_asked)

        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["base_url"] == "http://team:8080"
        assert data["ai"]["model"] == "my-model"

    def test_skips_api_key_when_team_provides_api_key_command(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        team = self._make_team(
            tmp_path,
            {"ai": {"base_url": "http://team:8080", "api_key_command": "vault read secret/key", "model": "gpt-4"}},
        )

        confirm_prompts: list[str] = []

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            if "Display name" in prompt:
                return "TestUser"
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            confirm_prompts.append(prompt)
            if "Test connection now?" in prompt:
                return False
            if "Write configuration?" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_init_wizard(team_config_path=str(team))

        assert result is True
        # Should NOT have prompted for API key method
        assert not any("command to fetch" in p for p in confirm_prompts)

        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["api_key_command"] == "vault read secret/key"
        assert "api_key" not in data["ai"]

    def test_uses_team_system_prompt(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        team = self._make_team(
            tmp_path,
            {
                "ai": {
                    "base_url": "http://team:8080",
                    "api_key": "sk-team-key",
                    "model": "gpt-4",
                    "system_prompt": "You are a team assistant.",
                }
            },
        )

        confirm_prompts: list[str] = []

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            if "Display name" in prompt:
                return "TestUser"
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            confirm_prompts.append(prompt)
            if "Test connection now?" in prompt:
                return False
            if "Write configuration?" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_init_wizard(team_config_path=str(team))

        assert result is True
        # Should NOT have prompted for system prompt
        assert not any("custom system prompt" in p for p in confirm_prompts)

        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["system_prompt"] == "You are a team assistant."

    def test_missing_team_file_falls_through_to_normal_flow(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        prompts_asked: list[str] = []

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            prompts_asked.append(prompt)
            if "Provider" in prompt:
                return "4"  # Ollama
            if "Model" in prompt:
                return "1"
            if "Display name" in prompt:
                return "TestUser"
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Test connection now?" in prompt:
                return False
            if "Set a custom system prompt?" in prompt:
                return False
            if "Write configuration?" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_init_wizard(team_config_path=str(tmp_path / "nonexistent.yaml"))

        assert result is True
        # Should have asked for provider since team file is missing
        assert any("Provider" in p for p in prompts_asked)
