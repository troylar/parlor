"""Tests for the Rich setup wizard and config editor."""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from anteroom.cli.setup import (
    PROVIDER_PRESETS,
    _is_interactive,
    _redact_key,
    _render_summary,
    _validate_url,
    _write_config,
    run_config_editor,
    run_init_wizard,
)


class TestProviderPresets:
    def test_all_presets_have_names(self) -> None:
        for preset in PROVIDER_PRESETS:
            assert preset.name

    def test_presets_with_urls_are_valid(self) -> None:
        for preset in PROVIDER_PRESETS:
            if preset.base_url and not preset.url_template:
                assert _validate_url(preset.base_url), f"{preset.name} has invalid URL: {preset.base_url}"

    def test_preset_count(self) -> None:
        assert len(PROVIDER_PRESETS) == 6

    def test_openai_preset(self) -> None:
        openai = PROVIDER_PRESETS[0]
        assert openai.name == "OpenAI"
        assert openai.needs_api_key is True
        assert "gpt-4o" in openai.suggested_models

    def test_ollama_no_key(self) -> None:
        ollama = PROVIDER_PRESETS[2]
        assert ollama.name == "Ollama"
        assert ollama.needs_api_key is False

    def test_azure_is_template(self) -> None:
        azure = PROVIDER_PRESETS[1]
        assert azure.url_template is True
        assert "{resource}" in azure.base_url


class TestRedactKey:
    def test_empty_key(self) -> None:
        assert _redact_key("") == "(not set)"

    def test_short_key(self) -> None:
        assert _redact_key("abc") == "****"

    def test_exactly_8_chars(self) -> None:
        assert _redact_key("12345678") == "****"

    def test_normal_key(self) -> None:
        result = _redact_key("sk-abcdefghijklmnop")
        assert result == "sk-...mnop"
        assert "abcdefgh" not in result

    def test_long_key(self) -> None:
        key = "sk-" + "x" * 48
        result = _redact_key(key)
        assert result.startswith("sk-")
        assert result.endswith("xxxx")
        assert "..." in result


class TestValidateUrl:
    def test_valid_https(self) -> None:
        assert _validate_url("https://api.openai.com/v1") is True

    def test_valid_http(self) -> None:
        assert _validate_url("http://localhost:11434/v1") is True

    def test_no_scheme(self) -> None:
        assert _validate_url("api.openai.com/v1") is False

    def test_empty(self) -> None:
        assert _validate_url("") is False


class TestIsInteractive:
    def test_returns_false_when_not_tty(self) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            assert _is_interactive() is False

    def test_returns_true_when_tty(self) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            assert _is_interactive() is True


class TestWriteConfig:
    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        config_path = tmp_path / "subdir" / "config.yaml"
        _write_config({"ai": {"base_url": "http://test"}}, config_path)
        assert config_path.exists()

    def test_sets_permissions(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_config({"ai": {"base_url": "http://test"}}, config_path)
        mode = config_path.stat().st_mode
        assert mode & stat.S_IROTH == 0
        assert mode & stat.S_IWOTH == 0
        assert mode & stat.S_IRUSR != 0

    def test_writes_valid_yaml(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        data = {"ai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"}}
        _write_config(data, config_path)
        loaded = yaml.safe_load(config_path.read_text())
        assert loaded == data


class TestRenderSummary:
    def test_redacts_api_key(self) -> None:
        config_data = {
            "ai": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-supersecretkey1234567890",
                "model": "gpt-4o",
            }
        }
        _render_summary(config_data, Path("/tmp/config.yaml"))


class TestRunInitWizard:
    def test_non_interactive_returns_false(self) -> None:
        with patch("anteroom.cli.setup._is_interactive", return_value=False):
            result = run_init_wizard()
        assert result is False

    def test_existing_config_decline_overwrite(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("ai:\n  base_url: http://old\n")

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Confirm.ask", return_value=False),
        ):
            result = run_init_wizard()
        assert result is False

    def test_full_wizard_flow(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        prompt_responses = {
            "Provider": "3",  # Ollama
            "Base URL": "http://localhost:11434/v1",
            "Model (number or name)": "1",  # first model
            "Choice": "8",  # Done (shifted from 7 to 8 with identity option)
            "Display name": "TestUser",
        }
        confirm_responses = {
            "Use a command to fetch": False,
            "Test connection now?": False,
            "Set a custom system prompt?": False,
            "Write configuration?": True,
        }

        def mock_prompt_ask(prompt, **kwargs):
            for key, val in prompt_responses.items():
                if key in prompt:
                    return val
            return kwargs.get("default", "1")

        def mock_confirm_ask(prompt, **kwargs):
            for key, val in confirm_responses.items():
                if key in prompt:
                    return val
            return kwargs.get("default", False)

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_init_wizard()

        assert result is True
        assert config_path.exists()

        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["base_url"] == "http://localhost:11434/v1"
        assert data["ai"]["model"] == "llama3"
        assert "api_key" not in data["ai"]
        assert "identity" in data
        assert data["identity"]["display_name"] == "TestUser"
        assert data["identity"]["user_id"]
        assert data["identity"]["public_key"]
        assert data["identity"]["private_key"]

    def test_keyboard_interrupt_cancels(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=KeyboardInterrupt),
        ):
            result = run_init_wizard()
        assert result is False
        assert not config_path.exists()


class TestRunConfigEditor:
    def test_no_config_redirects_to_wizard(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.run_init_wizard", return_value=True) as mock_wizard,
        ):
            result = run_config_editor()
        assert result is True
        mock_wizard.assert_called_once()

    def test_non_interactive_returns_false(self) -> None:
        with patch("anteroom.cli.setup._is_interactive", return_value=False):
            result = run_config_editor()
        assert result is False

    def test_no_changes_exits_clean(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"ai": {"base_url": "http://test", "api_key": "sk-test", "model": "gpt-4"}}))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", return_value="8"),
        ):
            result = run_config_editor()
        assert result is False

    def test_change_model_and_save(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        original = {"ai": {"base_url": "http://test", "api_key": "sk-test", "model": "gpt-4"}}
        config_path.write_text(yaml.dump(original))

        call_count = [0]

        def mock_prompt_ask(prompt, **kwargs):
            call_count[0] += 1
            choices = kwargs.get("choices")
            if choices and "8" in choices:
                if call_count[0] <= 2:
                    return "3"
                return "8"
            if "Model" in prompt:
                return "gpt-4o"
            return kwargs.get("default", "")

        confirm_responses = iter([True])  # Save changes

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=lambda *a, **kw: next(confirm_responses, True)),
            patch("anteroom.cli.setup._test_connection_with_spinner", return_value=(False, [])),
        ):
            result = run_config_editor()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["model"] == "gpt-4o"
        assert data["ai"]["base_url"] == "http://test"


class TestWizardIdentity:
    def test_wizard_generates_identity(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        prompt_responses = {
            "Provider": "3",
            "Base URL": "http://localhost:11434/v1",
            "Model (number or name)": "1",
            "Choice": "8",
            "Display name": "IdentityTestUser",
        }
        confirm_responses = {
            "Use a command to fetch": False,
            "Test connection now?": False,
            "Set a custom system prompt?": False,
            "Write configuration?": True,
        }

        def mock_prompt_ask(prompt, **kwargs):
            for key, val in prompt_responses.items():
                if key in prompt:
                    return val
            return kwargs.get("default", "1")

        def mock_confirm_ask(prompt, **kwargs):
            for key, val in confirm_responses.items():
                if key in prompt:
                    return val
            return kwargs.get("default", False)

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_init_wizard()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        identity = data.get("identity", {})
        assert identity["display_name"] == "IdentityTestUser"
        assert identity["user_id"]
        assert "BEGIN PUBLIC KEY" in identity["public_key"]
        assert "BEGIN PRIVATE KEY" in identity["private_key"]

    def test_render_summary_with_identity(self) -> None:
        config_data = {
            "ai": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-supersecretkey1234567890",
                "model": "gpt-4o",
            },
            "identity": {
                "user_id": "abc12345-6789-0000-0000-000000000000",
                "display_name": "TestUser",
            },
        }
        _render_summary(config_data, Path("/tmp/config.yaml"))


class TestAzureUrlTemplate:
    def test_azure_template_filling(self) -> None:
        azure = PROVIDER_PRESETS[1]
        url = azure.base_url.replace("{resource}", "myresource").replace("{deployment}", "mydeployment")
        assert url == "https://myresource.openai.azure.com/openai/deployments/mydeployment"
        assert "{" not in url


class TestConnectionTestFailurePath:
    def test_connection_failure_returns_empty_models(self) -> None:
        mock_service = MagicMock()

        async def mock_validate():
            return False, "Connection refused", []

        mock_service.validate_connection = mock_validate

        with (
            patch("anteroom.services.ai_service.create_ai_service", return_value=mock_service),
            patch("anteroom.cli.setup.asyncio.run", return_value=(False, "Connection refused", [])),
            patch("anteroom.cli.setup.console"),
        ):
            from anteroom.cli.setup import _test_connection_with_spinner

            ok, models = _test_connection_with_spinner("http://localhost:9999", "", "", "gpt-4")
        assert ok is False
        assert models == []
