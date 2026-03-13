"""Tests pinning /a-help prompt behavioral contracts and CLI invocation path."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_A_HELP_PATH = Path(__file__).resolve().parents[2] / "src" / "anteroom" / "cli" / "default_skills" / "a-help.yaml"

_EXPECTED_INTROSPECT_SECTIONS = [
    "config",
    "instructions",
    "tools",
    "safety",
    "skills",
    "budget",
    "spaces",
    "package",
    "runtime",
]


@pytest.fixture(scope="module")
def _a_help_data() -> dict:
    return yaml.safe_load(_A_HELP_PATH.read_text())


@pytest.fixture(scope="module")
def _prompt(_a_help_data: dict) -> str:
    return _a_help_data["prompt"]


class TestAHelpPromptContract:
    """Pin that the a-help prompt contains required structural elements."""

    @pytest.fixture()
    def prompt(self) -> str:
        text = _A_HELP_PATH.read_text()
        data = yaml.safe_load(text)
        return data["prompt"]

    def test_docs_first_strategy(self, prompt: str) -> None:
        assert "Check the inline quick reference FIRST" in prompt

    def test_has_source_code_index(self, prompt: str) -> None:
        assert "Source Code Index" in prompt

    def test_has_introspect_guidance(self, prompt: str) -> None:
        assert "introspect section=package" in prompt

    def test_has_scope_guardrail(self, prompt: str) -> None:
        assert "Anteroom's own installed" in prompt or "do not read arbitrary user project files" in prompt

    def test_under_size_budget(self) -> None:
        size = _A_HELP_PATH.stat().st_size
        assert size < 15_000, f"a-help.yaml is {size} bytes, budget is 15,000 bytes"


class TestAHelpCliInvocationPath:
    """Verify /a-help resolves through SkillRegistry.resolve_input() — the CLI path."""

    def test_resolves_via_resolve_input(self) -> None:
        from anteroom.cli.skills import SkillRegistry

        reg = SkillRegistry()
        reg.load()
        is_skill, expanded_prompt = reg.resolve_input("/a-help how does the agent loop work?")
        assert is_skill is True
        assert "how does the agent loop work?" in expanded_prompt
        assert "Source Code Index" in expanded_prompt


class TestAHelpRuntimeIntrospectGuidance:
    """Contract tests pinning the a-help runtime introspection guidance.

    These tests catch unintended drift in the prompt's guidance around
    ``introspect section=runtime`` for session diagnostics.
    """

    def test_prompt_mentions_introspect_runtime(self, _prompt: str) -> None:
        """Strategy step must direct the AI to use introspect section=runtime for session diagnostics."""
        assert "introspect section=runtime" in _prompt

    def test_runtime_described_as_bounded_metadata(self, _prompt: str) -> None:
        """The prompt must clarify that runtime inspection yields bounded session metadata."""
        assert "bounded" in _prompt
        assert "session metadata" in _prompt

    def test_runtime_not_general_db_browsing(self, _prompt: str) -> None:
        """The prompt must explicitly disclaim general DB browsing.

        The YAML block scalar may wrap across lines, so the disclaimer is
        checked as two adjacent tokens rather than a single phrase.
        """
        # The prompt reads "— not general DB\n   browsing." across a line wrap.
        assert "not general DB" in _prompt and "browsing" in _prompt

    def test_mentions_conversation_id(self, _prompt: str) -> None:
        """Key runtime field conversation_id must be referenced."""
        assert "conversation id" in _prompt.lower() or "conversation_id" in _prompt

    def test_mentions_message_count(self, _prompt: str) -> None:
        """Key runtime field message_count must be referenced."""
        assert "message count" in _prompt.lower() or "message_count" in _prompt

    def test_mentions_token_totals(self, _prompt: str) -> None:
        """Key runtime field token_totals must be referenced."""
        assert "token_totals" in _prompt or "token totals" in _prompt.lower()

    def test_mentions_active_space(self, _prompt: str) -> None:
        """Key runtime field active_space must be referenced."""
        assert "active space" in _prompt.lower() or "active_space" in _prompt


@pytest.mark.parametrize("section", _EXPECTED_INTROSPECT_SECTIONS)
def test_introspect_section_present(_prompt: str, section: str) -> None:
    """Every expected introspect section must be referenced in the prompt.

    If a section is intentionally removed, update ``_EXPECTED_INTROSPECT_SECTIONS``
    in this file along with a comment explaining the rationale.
    """
    assert f"section={section}" in _prompt, (
        f"Expected 'section={section}' to appear in the a-help prompt, but it was not found. "
        "If this section was intentionally removed, update _EXPECTED_INTROSPECT_SECTIONS."
    )
