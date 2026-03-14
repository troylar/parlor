"""Contract tests for the /new-skill built-in skill.

These tests pin the structural requirements of new-skill.yaml so that
if someone edits the instructions and removes safeguards, CI catches
it immediately.  Follows the test_excalidraw_skill.py pattern.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from anteroom.cli.skills import SkillRegistry

_SKILL_PATH = Path(__file__).resolve().parents[2] / "src" / "anteroom" / "cli" / "default_skills" / "new-skill.yaml"


def _load_prompt() -> str:
    data = yaml.safe_load(_SKILL_PATH.read_text())
    return data["prompt"]


class TestNewSkillContract:
    """Pin /new-skill prompt requirements so regressions are caught by CI."""

    def test_yaml_file_exists(self) -> None:
        assert _SKILL_PATH.exists(), f"new-skill.yaml not found at {_SKILL_PATH}"

    def test_yaml_parses_correctly(self) -> None:
        data = yaml.safe_load(_SKILL_PATH.read_text())
        assert isinstance(data, dict)

    def test_has_required_fields(self) -> None:
        data = yaml.safe_load(_SKILL_PATH.read_text())
        assert data.get("name") == "new-skill"
        assert "description" in data
        assert "prompt" in data
        assert isinstance(data["prompt"], str)
        assert len(data["prompt"]) > 100

    def test_forbids_content_field(self) -> None:
        """Prompt must explicitly tell the model NOT to use 'content:' for local CLI skills."""
        prompt = _load_prompt()
        prompt_lower = prompt.lower()
        assert "content:" in prompt_lower, "Prompt must mention 'content:' to warn against it"
        assert "not" in prompt_lower or "do not" in prompt_lower, "Prompt must explicitly prohibit using 'content:'"

    def test_requires_block_scalar_for_backslash(self) -> None:
        """Prompt must require block scalar (prompt: |) for backslash/regex content."""
        prompt = _load_prompt()
        prompt_lower = prompt.lower()
        assert "backslash" in prompt_lower, "Prompt must warn about backslash escaping"
        assert "block scalar" in prompt_lower or "prompt: |" in prompt, (
            "Prompt must recommend block scalar for backslash-heavy content"
        )

    def test_requires_post_write_validation(self) -> None:
        """Prompt must require reading the file back and validating: valid YAML, prompt key, non-empty."""
        prompt = _load_prompt()
        prompt_lower = prompt.lower()
        assert "read" in prompt_lower and ("back" in prompt_lower or "file" in prompt_lower), (
            "Prompt must instruct to read the file back after writing"
        )
        has_prompt_check = "prompt" in prompt_lower and (
            "key" in prompt_lower or "contains" in prompt_lower or "mapping" in prompt_lower
        )
        assert has_prompt_check, "Prompt must instruct to verify the 'prompt' key exists"
        assert "non-empty" in prompt_lower or "non empty" in prompt_lower, (
            "Prompt must instruct to verify the prompt value is non-empty"
        )

    def test_loaded_by_skill_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("new-skill")
            skill = reg.get("new-skill")
            assert skill is not None
            assert skill.source == "default"
