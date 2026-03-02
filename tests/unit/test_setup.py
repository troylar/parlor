"""Tests for the Rich setup wizard and config editor."""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from anteroom.cli.setup import (
    PROVIDER_PRESETS,
    ProviderPreset,
    _collect_api_key,
    _collect_app_settings,
    _collect_base_url,
    _collect_model,
    _collect_system_prompt,
    _is_interactive,
    _offer_starter_packs,
    _redact_key,
    _render_summary,
    _test_connection_with_spinner,
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

    def test_connection_success_returns_models(self) -> None:
        available = ["gpt-4o", "gpt-4", "gpt-3.5-turbo"]
        with (
            patch("anteroom.cli.setup.asyncio.run", return_value=(True, "OK", available)),
            patch("anteroom.services.ai_service.create_ai_service"),
            patch("anteroom.config.AIConfig"),
            patch("anteroom.cli.setup.console"),
        ):
            ok, models = _test_connection_with_spinner("https://api.openai.com/v1", "sk-key", "", "gpt-4o")
        assert ok is True
        assert models == available

    def test_connection_exception_returns_failure(self) -> None:
        with (
            patch("anteroom.cli.setup.asyncio.run", side_effect=RuntimeError("timeout")),
            patch("anteroom.services.ai_service.create_ai_service"),
            patch("anteroom.config.AIConfig"),
            patch("anteroom.cli.setup.console"),
        ):
            ok, models = _test_connection_with_spinner("https://api.openai.com/v1", "sk-key", "", "gpt-4o")
        assert ok is False
        assert models == []


class TestCollectBaseUrl:
    def test_azure_template_builds_url(self) -> None:
        azure = PROVIDER_PRESETS[1]
        prompt_responses = iter(["myresource", "mydeployment"])
        with (
            patch("anteroom.cli.setup.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_responses)),
            patch("anteroom.cli.setup.console"),
        ):
            url = _collect_base_url(azure)
        assert url == "https://myresource.openai.azure.com/openai/deployments/mydeployment"

    def test_empty_url_reprompts(self) -> None:
        openai = PROVIDER_PRESETS[0]
        responses = iter(["", "", "https://api.openai.com/v1"])
        with (
            patch("anteroom.cli.setup.Prompt.ask", side_effect=lambda *a, **kw: next(responses)),
            patch("anteroom.cli.setup.console"),
        ):
            url = _collect_base_url(openai)
        assert url == "https://api.openai.com/v1"

    def test_invalid_url_reprompts(self) -> None:
        openai = PROVIDER_PRESETS[0]
        responses = iter(["not-a-url", "https://api.openai.com/v1"])
        with (
            patch("anteroom.cli.setup.Prompt.ask", side_effect=lambda *a, **kw: next(responses)),
            patch("anteroom.cli.setup.console"),
        ):
            url = _collect_base_url(openai)
        assert url == "https://api.openai.com/v1"

    def test_no_key_preset_uses_default_url(self) -> None:
        ollama = PROVIDER_PRESETS[2]
        with (
            patch("anteroom.cli.setup.Prompt.ask", return_value="http://localhost:11434/v1"),
            patch("anteroom.cli.setup.console"),
        ):
            url = _collect_base_url(ollama)
        assert url == "http://localhost:11434/v1"


class TestCollectApiKey:
    def test_no_key_needed(self) -> None:
        ollama = PROVIDER_PRESETS[2]
        with patch("anteroom.cli.setup.console"):
            key, cmd = _collect_api_key(ollama)
        assert key == ""
        assert cmd == ""

    def test_use_command_path(self) -> None:
        openai = PROVIDER_PRESETS[0]
        with (
            patch("anteroom.cli.setup.Confirm.ask", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", return_value="vault read secret/openai"),
            patch("anteroom.cli.setup.console"),
        ):
            key, cmd = _collect_api_key(openai)
        assert key == ""
        assert cmd == "vault read secret/openai"

    def test_direct_key_entry(self) -> None:
        openai = PROVIDER_PRESETS[0]
        with (
            patch("anteroom.cli.setup.Confirm.ask", return_value=False),
            patch("anteroom.cli.setup.getpass.getpass", return_value="sk-direct-key"),
            patch("anteroom.cli.setup.console"),
        ):
            key, cmd = _collect_api_key(openai)
        assert key == "sk-direct-key"
        assert cmd == ""


class TestCollectModel:
    def test_available_models_numeric_choice(self) -> None:
        preset = PROVIDER_PRESETS[0]
        models = ["gpt-4o", "gpt-4", "gpt-3.5-turbo"]
        with (
            patch("anteroom.cli.setup.Prompt.ask", return_value="2"),
            patch("anteroom.cli.setup.console"),
        ):
            result = _collect_model(preset, available_models=models)
        assert result == "gpt-4"

    def test_available_models_string_choice(self) -> None:
        preset = PROVIDER_PRESETS[0]
        models = ["gpt-4o", "gpt-4"]
        with (
            patch("anteroom.cli.setup.Prompt.ask", return_value="my-custom-model"),
            patch("anteroom.cli.setup.console"),
        ):
            result = _collect_model(preset, available_models=models)
        assert result == "my-custom-model"

    def test_available_models_with_star_marks_suggested(self) -> None:
        preset = PROVIDER_PRESETS[0]  # has suggested_models including gpt-4o
        # Mix suggested and non-suggested
        models = ["gpt-4o", "custom-model-xyz"]
        with (
            patch("anteroom.cli.setup.Prompt.ask", return_value="1"),
            patch("anteroom.cli.setup.console"),
        ):
            result = _collect_model(preset, available_models=models)
        assert result == "gpt-4o"

    def test_more_than_15_models_shows_truncation(self) -> None:
        preset = ProviderPreset(name="Test", base_url="", needs_api_key=False)
        models = [f"model-{i}" for i in range(20)]
        console_calls: list[str] = []
        with (
            patch("anteroom.cli.setup.Prompt.ask", return_value="1"),
            patch("anteroom.cli.setup.console") as mock_console,
        ):
            mock_console.print.side_effect = lambda *a, **kw: console_calls.append(str(a[0]) if a else "")
            _collect_model(preset, available_models=models)
        assert any("more" in c for c in console_calls)

    def test_no_models_uses_default_prompt(self) -> None:
        preset = ProviderPreset(name="Test", base_url="", needs_api_key=False)
        with (
            patch("anteroom.cli.setup.Prompt.ask", return_value="gpt-4") as mock_ask,
            patch("anteroom.cli.setup.console"),
        ):
            result = _collect_model(preset, available_models=None)
        assert result == "gpt-4"
        # Should have asked for model name (not number)
        call_args = mock_ask.call_args
        assert "Model name" in call_args[0][0]

    def test_preset_with_suggestions_no_available_models(self) -> None:
        preset = PROVIDER_PRESETS[0]  # OpenAI with suggested models
        with (
            patch("anteroom.cli.setup.Prompt.ask", return_value="1"),
            patch("anteroom.cli.setup.console"),
        ):
            result = _collect_model(preset, available_models=None)
        assert result == "gpt-4o"


class TestCollectSystemPrompt:
    def test_no_custom_prompt_returns_empty(self) -> None:
        with patch("anteroom.cli.setup.Confirm.ask", return_value=False):
            result = _collect_system_prompt()
        assert result == ""

    def test_custom_prompt_returned(self) -> None:
        with (
            patch("anteroom.cli.setup.Confirm.ask", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", return_value="You are a helpful assistant."),
        ):
            result = _collect_system_prompt()
        assert result == "You are a helpful assistant."


class TestCollectAppSettings:
    def test_default_values(self) -> None:
        responses = iter(["127.0.0.1", "8080"])
        with (
            patch("anteroom.cli.setup.Prompt.ask", side_effect=lambda *a, **kw: next(responses)),
            patch("anteroom.cli.setup.Confirm.ask", return_value=False),
        ):
            result = _collect_app_settings()
        assert result == {"host": "127.0.0.1", "port": 8080, "tls": False}

    def test_custom_values(self) -> None:
        responses = iter(["0.0.0.0", "9090"])
        with (
            patch("anteroom.cli.setup.Prompt.ask", side_effect=lambda *a, **kw: next(responses)),
            patch("anteroom.cli.setup.Confirm.ask", return_value=True),
        ):
            result = _collect_app_settings()
        assert result == {"host": "0.0.0.0", "port": 9090, "tls": True}

    def test_uses_current_values_as_defaults(self) -> None:
        current = {"host": "10.0.0.1", "port": 9000, "tls": True}
        asked_defaults: list[str] = []

        def _capture_default(*a: object, **kw: object) -> object:
            asked_defaults.append(str(kw.get("default", "")))
            return kw.get("default", "")

        with (
            patch("anteroom.cli.setup.Prompt.ask", side_effect=_capture_default),
            patch("anteroom.cli.setup.Confirm.ask", return_value=True),
        ):
            _collect_app_settings(current)
        assert "10.0.0.1" in asked_defaults
        assert "9000" in asked_defaults


class TestRenderSummaryEdgeCases:
    def test_long_system_prompt_truncated(self) -> None:
        long_prompt = "A" * 80
        config_data = {
            "ai": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "system_prompt": long_prompt,
            }
        }
        # Should not raise; just verifies the truncation branch runs
        _render_summary(config_data, Path("/tmp/config.yaml"))

    def test_app_section_with_tls(self) -> None:
        config_data = {
            "ai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
            "app": {"host": "0.0.0.0", "port": 443, "tls": True},
        }
        _render_summary(config_data, Path("/tmp/config.yaml"))

    def test_api_key_command_shown(self) -> None:
        config_data = {
            "ai": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "api_key_command": "vault read secret/key",
            }
        }
        _render_summary(config_data, Path("/tmp/config.yaml"))

    def test_app_section_without_tls(self) -> None:
        config_data = {
            "ai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
            "app": {"host": "127.0.0.1", "port": 8080, "tls": False},
        }
        _render_summary(config_data, Path("/tmp/config.yaml"))


class TestWizardConnectionTestPaths:
    def _base_patches(self, config_path: Path, confirm_overrides: dict | None = None):
        confirm_overrides = confirm_overrides or {}

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            if "Provider" in prompt:
                return "1"  # OpenAI
            if "Base URL" in prompt:
                return "https://api.openai.com/v1"
            if "Model" in prompt:
                return "1"
            if "Display name" in prompt:
                return "TestUser"
            if "Retry" in prompt:
                return kwargs.get("default", "continue")
            return str(kwargs.get("default", "1"))

        default_confirms = {
            "Test connection now?": True,
            "Use a command to fetch": False,
            "Set a custom system prompt?": False,
            "Write configuration?": True,
        }
        default_confirms.update(confirm_overrides)

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            for key, val in default_confirms.items():
                if key in prompt:
                    return val
            return bool(kwargs.get("default", False))

        return mock_prompt_ask, mock_confirm_ask

    def test_connection_success_sets_available_models(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        mock_prompt, mock_confirm = self._base_patches(config_path)

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm),
            patch(
                "anteroom.cli.setup._test_connection_with_spinner",
                return_value=(True, ["gpt-4o", "gpt-4"]),
            ),
            patch("anteroom.cli.setup.getpass.getpass", return_value="sk-test-key"),
        ):
            result = run_init_wizard()

        assert result is True

    def test_connection_failure_then_continue(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        prompt_responses = {
            "Provider": "1",
            "Base URL": "https://api.openai.com/v1",
            "Model (number or name)": "1",
            "Display name": "TestUser",
            "Retry": "continue",
        }
        confirm_responses = {
            "Test connection now?": True,
            "Use a command to fetch": False,
            "Set a custom system prompt?": False,
            "Write configuration?": True,
        }

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            for key, val in prompt_responses.items():
                if key in prompt:
                    return val
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            for key, val in confirm_responses.items():
                if key in prompt:
                    return val
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
            patch(
                "anteroom.cli.setup._test_connection_with_spinner",
                return_value=(False, []),
            ),
            patch("anteroom.cli.setup.getpass.getpass", return_value="sk-test-key"),
        ):
            result = run_init_wizard()

        assert result is True

    def test_connection_failure_then_quit(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        prompt_responses = {
            "Provider": "1",
            "Base URL": "https://api.openai.com/v1",
            "Retry": "quit",
        }
        confirm_responses = {
            "Test connection now?": True,
            "Use a command to fetch": False,
        }

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            for key, val in prompt_responses.items():
                if key in prompt:
                    return val
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            for key, val in confirm_responses.items():
                if key in prompt:
                    return val
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
            patch(
                "anteroom.cli.setup._test_connection_with_spinner",
                return_value=(False, []),
            ),
            patch("anteroom.cli.setup.getpass.getpass", return_value="sk-test-key"),
        ):
            result = run_init_wizard()

        assert result is False

    def test_connection_failure_retry_then_succeed(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        retry_calls = [0]

        prompt_responses = {
            "Provider": "1",
            "Base URL": "https://api.openai.com/v1",
            "Model (number or name)": "1",
            "Display name": "TestUser",
        }
        confirm_responses = {
            "Test connection now?": True,
            "Use a command to fetch": False,
            "Set a custom system prompt?": False,
            "Write configuration?": True,
        }

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            for key, val in prompt_responses.items():
                if key in prompt:
                    return val
            if "Retry" in prompt:
                retry_calls[0] += 1
                return "retry"
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            for key, val in confirm_responses.items():
                if key in prompt:
                    return val
            return bool(kwargs.get("default", False))

        # First call fails, second call succeeds
        connection_results = iter([(False, []), (True, ["gpt-4o"])])

        def mock_connection(*args, **kwargs):
            return next(connection_results)

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
            patch("anteroom.cli.setup._test_connection_with_spinner", side_effect=mock_connection),
            patch("anteroom.cli.setup.getpass.getpass", return_value="sk-test-key"),
        ):
            result = run_init_wizard()

        assert result is True
        assert retry_calls[0] == 1

    def test_decline_write_returns_false(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        prompt_responses = {
            "Provider": "3",  # Ollama (no API key needed)
            "Base URL": "http://localhost:11434/v1",
            "Model (number or name)": "1",
            "Display name": "TestUser",
        }
        confirm_responses = {
            "Test connection now?": False,
            "Set a custom system prompt?": False,
            "Write configuration?": False,
        }

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            for key, val in prompt_responses.items():
                if key in prompt:
                    return val
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            for key, val in confirm_responses.items():
                if key in prompt:
                    return val
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_init_wizard()

        assert result is False
        assert not config_path.exists()

    def test_eoferror_cancels_wizard(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=EOFError),
        ):
            result = run_init_wizard()

        assert result is False

    def test_force_flag_skips_overwrite_prompt(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"ai": {"base_url": "http://old"}}))

        prompt_responses = {
            "Provider": "3",
            "Base URL": "http://localhost:11434/v1",
            "Model (number or name)": "1",
            "Display name": "TestUser",
        }
        confirm_responses = {
            "Test connection now?": False,
            "Set a custom system prompt?": False,
            "Write configuration?": True,
        }

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            for key, val in prompt_responses.items():
                if key in prompt:
                    return val
            return str(kwargs.get("default", "1"))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            # Overwrite should NOT be asked when force=True
            assert "Overwrite" not in prompt
            for key, val in confirm_responses.items():
                if key in prompt:
                    return val
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_init_wizard(force=True)

        assert result is True


class TestOfferStarterPacks:
    def test_no_packs_available_returns_early(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        with patch("anteroom.services.starter_packs.list_starter_packs", return_value=[]):
            _offer_starter_packs(config_path)

    def test_user_chooses_none(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        packs = [{"name": "python-dev", "description": "Python development rules"}]
        with (
            patch("anteroom.services.starter_packs.list_starter_packs", return_value=packs),
            patch("anteroom.cli.setup.Prompt.ask", return_value="none"),
            patch("anteroom.cli.setup.console"),
        ):
            _offer_starter_packs(config_path)

    def test_user_chooses_all_installs_packs(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        packs = [{"name": "python-dev", "description": "Python development"}]
        mock_db = MagicMock()
        install_results = [{"status": "installed", "namespace": "anteroom", "name": "python-dev"}]
        with (
            patch("anteroom.services.starter_packs.list_starter_packs", return_value=packs),
            patch("anteroom.cli.setup.Prompt.ask", return_value="all"),
            patch("anteroom.db.get_db", return_value=mock_db),
            patch("anteroom.services.starter_packs.install_starter_packs", return_value=install_results),
            patch("anteroom.cli.setup.console"),
        ):
            _offer_starter_packs(config_path)

    def test_user_chooses_specific_pack(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        packs = [
            {"name": "python-dev", "description": "Python development"},
            {"name": "security", "description": "Security rules"},
        ]
        mock_db = MagicMock()
        install_results = [{"status": "installed", "namespace": "anteroom", "name": "python-dev"}]
        install_path = "anteroom.services.starter_packs.install_starter_packs"
        with (
            patch("anteroom.services.starter_packs.list_starter_packs", return_value=packs),
            patch("anteroom.cli.setup.Prompt.ask", return_value="python-dev"),
            patch("anteroom.db.get_db", return_value=mock_db),
            patch(install_path, return_value=install_results) as mock_install,
            patch("anteroom.cli.setup.console"),
        ):
            _offer_starter_packs(config_path)
        mock_install.assert_called_once_with(mock_db, names=["python-dev"])

    def test_exception_is_swallowed(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        with (
            patch("anteroom.services.starter_packs.list_starter_packs", side_effect=ImportError("no module")),
            patch("anteroom.cli.setup.console"),
        ):
            _offer_starter_packs(config_path)

    def test_no_installed_results(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        packs = [{"name": "python-dev", "description": "Python development"}]
        mock_db = MagicMock()
        install_results: list = []
        with (
            patch("anteroom.services.starter_packs.list_starter_packs", return_value=packs),
            patch("anteroom.cli.setup.Prompt.ask", return_value="all"),
            patch("anteroom.db.get_db", return_value=mock_db),
            patch("anteroom.services.starter_packs.install_starter_packs", return_value=install_results),
            patch("anteroom.cli.setup.console"),
        ):
            _offer_starter_packs(config_path)


class TestConfigEditorChoices:
    def _write_config_file(self, tmp_path: Path) -> Path:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "ai": {
                        "base_url": "https://api.openai.com/v1",
                        "api_key": "sk-test",
                        "model": "gpt-4",
                    },
                    "identity": {
                        "user_id": "abc12345-1234-1234-1234-000000000000",
                        "display_name": "TestUser",
                        "public_key": "FAKEPUBKEY",
                    },
                }
            )
        )
        return config_path

    def test_choice_1_change_provider(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "1" if call_count[0] == 1 else "8"
            if "Provider" in prompt:
                return "3"  # Ollama
            if "Base URL" in prompt:
                return "http://localhost:11434/v1"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Test connection" in prompt:
                return False
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_config_editor()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["base_url"] == "http://localhost:11434/v1"

    def test_choice_1_with_connection_test(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "1" if call_count[0] <= 2 else "8"
            if "Provider" in prompt:
                return "1"
            if "Base URL" in prompt:
                return "https://api.openai.com/v1"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Test connection" in prompt:
                return True
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
            patch("anteroom.cli.setup._test_connection_with_spinner", return_value=(True, ["gpt-4o"])),
        ):
            result = run_config_editor()

        assert result is True

    def test_choice_2_change_api_key(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "2" if call_count[0] == 1 else "8"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Use a command to fetch" in prompt:
                return False
            if "Test connection" in prompt:
                return False
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
            patch("anteroom.cli.setup.getpass.getpass", return_value="sk-new-key"),
        ):
            result = run_config_editor()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["api_key"] == "sk-new-key"

    def test_choice_2_set_api_key_command(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "2" if call_count[0] == 1 else "8"
            if "API key command" in prompt:
                return "vault read secret/key"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Use a command to fetch" in prompt:
                return True
            if "Test connection" in prompt:
                return False
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_config_editor()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["api_key_command"] == "vault read secret/key"
        assert "api_key" not in data["ai"]

    def test_choice_2_with_connection_test(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "2" if call_count[0] <= 2 else "8"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Use a command to fetch" in prompt:
                return False
            if "Test connection" in prompt:
                return True
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
            patch("anteroom.cli.setup._test_connection_with_spinner", return_value=(False, [])),
            patch("anteroom.cli.setup.getpass.getpass", return_value="sk-new-key"),
        ):
            result = run_config_editor()

        assert result is True

    def test_choice_3_change_model_with_connection(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "3" if call_count[0] <= 2 else "8"
            if "Model" in prompt:
                return "gpt-4o"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
            patch("anteroom.cli.setup._test_connection_with_spinner", return_value=(True, ["gpt-4o", "gpt-4"])),
        ):
            result = run_config_editor()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["model"] == "gpt-4o"

    def test_choice_4_clear_system_prompt(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "ai": {
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4",
                        "system_prompt": "You are a helpful assistant.",
                    }
                }
            )
        )
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "4" if call_count[0] == 1 else "8"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Clear custom system prompt" in prompt:
                return True
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_config_editor()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        assert "system_prompt" not in data.get("ai", {})

    def test_choice_4_set_new_system_prompt(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "4" if call_count[0] == 1 else "8"
            if "System prompt" in prompt:
                return "Be concise."
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Clear custom system prompt" in prompt:
                return False
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_config_editor()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["system_prompt"] == "Be concise."

    def test_choice_5_app_settings(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "5" if call_count[0] == 1 else "8"
            if "Host" in prompt:
                return "0.0.0.0"
            if "Port" in prompt:
                return "9090"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "TLS" in prompt:
                return False
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_config_editor()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        assert data["app"]["host"] == "0.0.0.0"
        assert data["app"]["port"] == 9090

    def test_choice_6_test_connection(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "6" if call_count[0] == 1 else "8"
            return str(kwargs.get("default", ""))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup._test_connection_with_spinner", return_value=(True, ["gpt-4o"])) as mock_test,
        ):
            result = run_config_editor()

        assert result is False  # No changes made
        mock_test.assert_called()

    def test_choice_7_view_identity(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "7" if call_count[0] == 1 else "8"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Change display name" in prompt:
                return False
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_config_editor()

        assert result is False

    def test_choice_7_change_display_name(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "7" if call_count[0] <= 2 else "8"
            if "New display name" in prompt:
                return "NewName"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Change display name" in prompt:
                return True
            if "Save changes" in prompt:
                return True
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
        ):
            result = run_config_editor()

        assert result is True
        data = yaml.safe_load(config_path.read_text())
        assert data["identity"]["display_name"] == "NewName"

    def test_choice_7_no_identity_configured(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"ai": {"base_url": "http://test", "model": "gpt-4"}}))
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "7" if call_count[0] == 1 else "8"
            return str(kwargs.get("default", ""))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
        ):
            result = run_config_editor()

        assert result is False

    def test_discard_changes(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)
        call_count = [0]

        def mock_prompt_ask(prompt: str, **kwargs: object) -> str:
            call_count[0] += 1
            if "Choice" in prompt:
                return "3" if call_count[0] <= 2 else "8"
            if "Model" in prompt:
                return "gpt-4o"
            return str(kwargs.get("default", ""))

        def mock_confirm_ask(prompt: str, **kwargs: object) -> bool:
            if "Save changes" in prompt:
                return False
            return bool(kwargs.get("default", False))

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=mock_prompt_ask),
            patch("anteroom.cli.setup.Confirm.ask", side_effect=mock_confirm_ask),
            patch("anteroom.cli.setup._test_connection_with_spinner", return_value=(False, [])),
        ):
            result = run_config_editor()

        assert result is False
        # Model should not have been saved
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["model"] == "gpt-4"

    def test_keyboard_interrupt_in_editor(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=KeyboardInterrupt),
        ):
            result = run_config_editor()

        assert result is False

    def test_eoferror_in_editor(self, tmp_path: Path) -> None:
        config_path = self._write_config_file(tmp_path)

        with (
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=EOFError),
        ):
            result = run_config_editor()

        assert result is False
