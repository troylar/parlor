"""Integration tests: CLI /config command flows.

Exercises the _handle_config_command() dispatch with real config state,
real YAML persistence, and real Rich Console output to verify the end-to-end
flow that users experience with /config get, /config set, /config list,
and /config reset.

Addresses the CLI UX coverage requirement from .claude/rules/ux-testing.md
for cli/repl.py changes in PR #941.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anteroom.cli import renderer
from anteroom.cli.repl import _handle_config_command

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class _AIStub:
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = "sk-test"
    api_key_command: str = ""
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    max_output_tokens: int = 4096
    max_tools: int = 128
    system_prompt: str = ""
    user_system_prompt: str = ""
    provider: str = "openai"


@dataclass
class _SafetyStub:
    approval_mode: str = "ask_for_writes"
    read_only: bool = False


@dataclass
class _EmbeddingsStub:
    enabled: bool | None = None
    api_key: str = ""
    api_key_command: str = ""


@dataclass
class _ConfigStub:
    ai: _AIStub = field(default_factory=_AIStub)
    safety: _SafetyStub = field(default_factory=_SafetyStub)
    embeddings: _EmbeddingsStub = field(default_factory=_EmbeddingsStub)


def _capture_config_command(
    user_input: str,
    *,
    config: Any = None,
    tmp_path: Path | None = None,
) -> str:
    """Run _handle_config_command and capture Rich Console output."""
    if config is None:
        config = _ConfigStub()

    buf = io.StringIO()
    console = __import__("rich.console", fromlist=["Console"]).Console(
        file=buf, width=100, force_terminal=True, color_system="truecolor"
    )
    original = renderer.console
    renderer.console = console
    try:
        _handle_config_command(
            user_input,
            config=config,
            db=MagicMock(),
            active_space=None,
            working_dir=str(tmp_path) if tmp_path else os.getcwd(),
            ai_service=None,
            toolbar_refresh=lambda: None,
        )
    finally:
        renderer.console = original
    return _ANSI_RE.sub("", buf.getvalue())


# ---------------------------------------------------------------------------
# /config list
# ---------------------------------------------------------------------------


class TestConfigList:
    def test_list_shows_fields(self) -> None:
        output = _capture_config_command("/config list")
        assert "ai.model" in output
        assert "Field" in output  # table header

    def test_list_with_filter(self) -> None:
        output = _capture_config_command("/config list safety")
        assert "safety" in output
        # Should NOT show ai fields
        assert "ai.model" not in output

    def test_list_shows_field_count(self) -> None:
        output = _capture_config_command("/config list")
        assert "fields" in output

    def test_list_shows_source_layer(self) -> None:
        output = _capture_config_command("/config list")
        assert "default" in output


# ---------------------------------------------------------------------------
# /config get
# ---------------------------------------------------------------------------


class TestConfigGet:
    def test_get_shows_field_detail(self) -> None:
        output = _capture_config_command("/config get ai.model")
        assert "ai.model" in output
        assert "gpt-4o-mini" in output
        assert "Value:" in output

    def test_get_shows_source(self) -> None:
        output = _capture_config_command("/config get ai.model")
        # Source layer should be shown
        assert "Source:" in output

    def test_get_unknown_field_shows_error(self) -> None:
        output = _capture_config_command("/config get nonexistent.field")
        # Should show an error, not crash
        assert len(output.strip()) > 0

    def test_get_no_field_shows_usage(self) -> None:
        output = _capture_config_command("/config get")
        assert "Usage:" in output

    def test_get_sensitive_field_blocked(self) -> None:
        output = _capture_config_command("/config get ai.api_key")
        assert "sensitive" in output.lower()

    def test_get_sensitive_embeddings_key_blocked(self) -> None:
        output = _capture_config_command("/config get embeddings.api_key")
        assert "sensitive" in output.lower()


# ---------------------------------------------------------------------------
# /config set
# ---------------------------------------------------------------------------


class TestConfigSet:
    def test_set_no_args_shows_usage(self) -> None:
        output = _capture_config_command("/config set")
        assert "Usage:" in output

    def test_set_sensitive_field_blocked(self) -> None:
        output = _capture_config_command("/config set ai.api_key sk-secret")
        assert "sensitive" in output.lower() or "cannot" in output.lower()

    def test_set_enforced_field_blocked(self) -> None:
        """Team-enforced fields should be rejected."""
        with patch(
            "anteroom.services.config_editor.check_write_allowed",
            return_value=(False, "Field 'ai.model' is enforced by team config"),
        ):
            output = _capture_config_command("/config set ai.model gpt-4o")
            assert "enforced" in output.lower() or "not allowed" in output.lower()

    def test_set_invalid_scope_rejected(self) -> None:
        output = _capture_config_command("/config set ai.model gpt-4o --scope invalid")
        assert "Invalid scope" in output

    def test_set_space_scope_without_space_rejected(self) -> None:
        output = _capture_config_command("/config set ai.model gpt-4o --scope space")
        assert "space" in output.lower()

    def test_set_personal_writes_yaml(self, tmp_path: Path) -> None:
        """Successful personal set should write to YAML and show confirmation."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  model: gpt-4o-mini\n")

        with patch("anteroom.config._get_config_path", return_value=config_file):
            output = _capture_config_command(
                "/config set ai.model gpt-4o",
                tmp_path=tmp_path,
            )
        assert "gpt-4o" in output
        assert "personal" in output.lower() or "Saved" in output


# ---------------------------------------------------------------------------
# /config reset
# ---------------------------------------------------------------------------


class TestConfigReset:
    def test_reset_no_args_shows_usage(self) -> None:
        output = _capture_config_command("/config reset")
        assert "Usage:" in output

    def test_reset_sensitive_field_blocked(self) -> None:
        output = _capture_config_command("/config reset ai.api_key")
        assert "sensitive" in output.lower()

    def test_reset_invalid_scope_rejected(self) -> None:
        output = _capture_config_command("/config reset ai.model --scope invalid")
        assert "Invalid scope" in output

    def test_reset_space_scope_without_space_rejected(self) -> None:
        output = _capture_config_command("/config reset ai.model --scope space")
        assert "space" in output.lower()

    def test_reset_personal_field(self, tmp_path: Path) -> None:
        """Reset should remove the field from personal config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  model: gpt-4o\n  base_url: https://api.openai.com/v1\n")

        with patch("anteroom.config._get_config_path", return_value=config_file):
            output = _capture_config_command(
                "/config reset ai.model",
                tmp_path=tmp_path,
            )
        # Should confirm removal or say not set
        assert "ai.model" in output


# ---------------------------------------------------------------------------
# /config (bare / help)
# ---------------------------------------------------------------------------


class TestConfigHelp:
    def test_bare_config_shows_help(self) -> None:
        output = _capture_config_command("/config")
        assert "list" in output
        assert "get" in output
        assert "set" in output
        assert "reset" in output

    def test_unknown_subcommand_shows_help(self) -> None:
        output = _capture_config_command("/config unknown")
        assert "list" in output
        assert "get" in output

    def test_help_shows_available_scopes(self) -> None:
        output = _capture_config_command("/config")
        assert "personal" in output


# ---------------------------------------------------------------------------
# Sensitive field boundary
# ---------------------------------------------------------------------------


class TestSensitiveFieldBoundary:
    """Verify all known sensitive fields are blocked in all commands."""

    SENSITIVE = [
        "ai.api_key",
        "ai.api_key_command",
        "embeddings.api_key",
        "embeddings.api_key_command",
        "identity.private_key",
        "identity.public_key",
        "identity.user_id",
        "storage.encryption_kdf",
    ]

    @pytest.mark.parametrize("field", SENSITIVE)
    def test_get_blocks_sensitive(self, field: str) -> None:
        output = _capture_config_command(f"/config get {field}")
        assert "sensitive" in output.lower()

    @pytest.mark.parametrize("field", SENSITIVE)
    def test_set_blocks_sensitive(self, field: str) -> None:
        output = _capture_config_command(f"/config set {field} test-value")
        assert "sensitive" in output.lower() or "cannot" in output.lower()

    @pytest.mark.parametrize("field", SENSITIVE)
    def test_reset_blocks_sensitive(self, field: str) -> None:
        output = _capture_config_command(f"/config reset {field}")
        assert "sensitive" in output.lower()
