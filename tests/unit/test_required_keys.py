"""Tests for required keys validation and prompting."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from anteroom.services.required_keys import (
    _is_sensitive,
    check_required_keys,
    format_missing_keys_error,
    prompt_for_missing_keys,
)


class TestIsSensitive:
    def test_api_key(self) -> None:
        assert _is_sensitive("ai.api_key")

    def test_secret(self) -> None:
        assert _is_sensitive("service.client_secret")

    def test_password(self) -> None:
        assert _is_sensitive("db.password")

    def test_token(self) -> None:
        assert _is_sensitive("auth.access_token")

    def test_not_sensitive(self) -> None:
        assert not _is_sensitive("ai.model")
        assert not _is_sensitive("app.port")
        assert not _is_sensitive("ai.base_url")


class TestCheckRequiredKeys:
    def test_all_present(self) -> None:
        required = [
            {"path": "ai.model", "description": "Model"},
            {"path": "ai.base_url", "description": "URL"},
        ]
        raw = {"ai": {"model": "gpt-4", "base_url": "http://localhost:8080"}}
        missing = check_required_keys(required, raw)
        assert len(missing) == 0

    def test_some_missing(self) -> None:
        required = [
            {"path": "ai.model", "description": "Model"},
            {"path": "ai.api_key", "description": "API key"},
        ]
        raw = {"ai": {"model": "gpt-4"}}
        missing = check_required_keys(required, raw)
        assert len(missing) == 1
        assert missing[0]["path"] == "ai.api_key"

    def test_all_missing(self) -> None:
        required = [
            {"path": "ai.model", "description": "Model"},
            {"path": "ai.api_key", "description": "API key"},
        ]
        missing = check_required_keys(required, {})
        assert len(missing) == 2

    def test_env_var_satisfies(self) -> None:
        required = [{"path": "ai.api_key", "description": "Key"}]
        with patch.dict("os.environ", {"AI_CHAT_AI_API_KEY": "from-env"}):
            missing = check_required_keys(required, {})
        assert len(missing) == 0

    def test_nested_path(self) -> None:
        required = [{"path": "safety.subagent.timeout", "description": "Timeout"}]
        raw = {"safety": {"subagent": {"timeout": 60}}}
        missing = check_required_keys(required, raw)
        assert len(missing) == 0

    def test_empty_required(self) -> None:
        missing = check_required_keys([], {"ai": {"model": "gpt-4"}})
        assert len(missing) == 0

    def test_skips_empty_path(self) -> None:
        required = [{"path": "", "description": "Bad"}]
        missing = check_required_keys(required, {})
        assert len(missing) == 0


class TestPromptForMissingKeys:
    def test_non_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "config.yaml"
            missing = [{"path": "ai.api_key", "description": "Key"}]
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                result = prompt_for_missing_keys(missing, cfg)
            assert not result

    def test_interactive_fills_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "config.yaml"
            missing = [{"path": "ai.model", "description": "Model name"}]
            with (
                patch("sys.stdin") as mock_stdin,
                patch("builtins.input", return_value="llama3"),
                patch("builtins.print"),
            ):
                mock_stdin.isatty.return_value = True
                result = prompt_for_missing_keys(missing, cfg)
            assert result
            # Verify written to file
            raw = yaml.safe_load(cfg.read_text())
            assert raw["ai"]["model"] == "llama3"

    def test_sensitive_uses_getpass(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "config.yaml"
            missing = [{"path": "ai.api_key", "description": "Your API key"}]
            with (
                patch("sys.stdin") as mock_stdin,
                patch("getpass.getpass", return_value="sk-secret") as mock_getpass,
                patch("builtins.print"),
            ):
                mock_stdin.isatty.return_value = True
                result = prompt_for_missing_keys(missing, cfg)
            assert result
            mock_getpass.assert_called_once()
            raw = yaml.safe_load(cfg.read_text())
            assert raw["ai"]["api_key"] == "sk-secret"

    def test_preserves_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "config.yaml"
            cfg.write_text(yaml.dump({"ai": {"base_url": "http://existing"}}))
            missing = [{"path": "ai.model", "description": "Model"}]
            with (
                patch("sys.stdin") as mock_stdin,
                patch("builtins.input", return_value="gpt-4"),
                patch("builtins.print"),
            ):
                mock_stdin.isatty.return_value = True
                prompt_for_missing_keys(missing, cfg)
            raw = yaml.safe_load(cfg.read_text())
            assert raw["ai"]["base_url"] == "http://existing"
            assert raw["ai"]["model"] == "gpt-4"

    def test_skips_empty_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "config.yaml"
            missing = [{"path": "ai.model", "description": "Model"}]
            with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value=""), patch("builtins.print"):
                mock_stdin.isatty.return_value = True
                result = prompt_for_missing_keys(missing, cfg)
            assert not result  # no values set


class TestFormatMissingKeysError:
    def test_format(self) -> None:
        missing = [
            {"path": "ai.api_key", "description": "Your OpenAI API key"},
            {"path": "ai.base_url", "description": ""},
        ]
        text = format_missing_keys_error(missing)
        assert "ai.api_key" in text
        assert "Your OpenAI API key" in text
        assert "AI_CHAT_AI_API_KEY" in text
        assert "ai.base_url" in text
        assert "aroom init" in text
