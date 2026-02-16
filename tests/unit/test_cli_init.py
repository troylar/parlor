"""Tests for anteroom init and --version."""

from __future__ import annotations

import stat
import subprocess
import sys
from unittest.mock import patch

import yaml


class TestVersion:
    def test_version_flag(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "anteroom", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "aroom" in result.stdout.lower()


class TestInit:
    def test_init_creates_config(self, tmp_path) -> None:
        config_path = tmp_path / "config.yaml"

        from anteroom.__main__ import _run_init

        prompt_responses = {
            "Provider": "1",  # OpenAI
            "Base URL": "https://api.openai.com/v1",
            "Model": "gpt-4",
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
            patch("anteroom.cli.setup.getpass.getpass", return_value="sk-test-key"),
        ):
            _run_init()

        assert config_path.exists()
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["base_url"] == "https://api.openai.com/v1"
        assert data["ai"]["api_key"] == "sk-test-key"
        assert data["ai"]["model"] == "gpt-4"

        mode = config_path.stat().st_mode
        assert mode & stat.S_IROTH == 0
        assert mode & stat.S_IWOTH == 0

    def test_init_respects_cancel(self, tmp_path) -> None:
        config_path = tmp_path / "config.yaml"

        from anteroom.__main__ import _run_init

        with (
            patch("anteroom.cli.setup._is_interactive", return_value=True),
            patch("anteroom.config._get_config_path", return_value=config_path),
            patch("anteroom.cli.setup.Prompt.ask", side_effect=KeyboardInterrupt),
        ):
            _run_init()

        assert not config_path.exists()
