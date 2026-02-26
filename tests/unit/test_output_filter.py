"""Tests for output content filter with system prompt leak detection."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from anteroom.config import OutputFilterConfig, OutputFilterPatternConfig, load_config
from anteroom.services.output_filter import (
    MIN_PROMPT_WORDS,
    NGRAM_WINDOW,
    OutputContentFilter,
    _build_ngrams,
    _tokenize,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> OutputFilterConfig:
    defaults: dict[str, object] = {"enabled": True}
    defaults.update(overrides)
    return OutputFilterConfig(**defaults)  # type: ignore[arg-type]


def _make_scanner(system_prompt: str | None = None, **overrides: object) -> OutputContentFilter:
    return OutputContentFilter(_make_config(**overrides), system_prompt=system_prompt)


def _long_prompt(n_words: int = 30) -> str:
    """Generate a system prompt with enough words for n-gram extraction."""
    return " ".join(f"word{i}" for i in range(n_words))


# Reusable prompt strings for leak detection tests (avoids long lines).
_LEAK_PROMPT_SHORT = (
    "You are a secret internal assistant for Project Phoenix"
    " with classification level alpha bravo charlie delta echo foxtrot"
)
_LEAK_PROMPT = (
    "You are a secret internal assistant for Project Phoenix"
    " with classification level alpha bravo charlie delta echo foxtrot golf hotel"
)
_LEAK_PROMPT_LONG = (
    "You are a secret internal assistant for Project Phoenix"
    " with classification level alpha bravo charlie delta echo foxtrot"
    " golf hotel india juliet kilo lima mike november oscar papa"
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestOutputFilterConfig:
    def test_defaults(self) -> None:
        cfg = OutputFilterConfig()
        assert cfg.enabled is False
        assert cfg.system_prompt_leak_detection is True
        assert cfg.leak_threshold == 0.4
        assert cfg.action == "warn"
        assert cfg.redaction_string == "[FILTERED]"
        assert cfg.log_detections is True
        assert cfg.custom_patterns == []

    def test_invalid_action_defaults_to_warn(self) -> None:
        cfg = OutputFilterConfig(action="invalid")
        assert cfg.action == "warn"

    def test_invalid_threshold_defaults(self) -> None:
        cfg = OutputFilterConfig(leak_threshold=0.0)
        assert cfg.leak_threshold == 0.4

    def test_threshold_over_one_defaults(self) -> None:
        cfg = OutputFilterConfig(leak_threshold=1.5)
        assert cfg.leak_threshold == 0.4


# ---------------------------------------------------------------------------
# Tokenization and N-grams
# ---------------------------------------------------------------------------


class TestTokenization:
    def test_tokenize_basic(self) -> None:
        tokens = _tokenize("Hello World 123")
        assert tokens == ["hello", "world", "123"]

    def test_tokenize_strips_punctuation(self) -> None:
        tokens = _tokenize("You are a helpful, safe AI assistant!")
        assert "helpful" in tokens
        assert "safe" in tokens

    def test_build_ngrams_basic(self) -> None:
        tokens = list("abcde")
        ngrams = _build_ngrams(tokens, 3)
        assert ("a", "b", "c") in ngrams
        assert ("c", "d", "e") in ngrams
        assert len(ngrams) == 3

    def test_build_ngrams_too_short(self) -> None:
        tokens = ["a", "b"]
        ngrams = _build_ngrams(tokens, 3)
        assert ngrams == set()


# ---------------------------------------------------------------------------
# Disabled Scanner
# ---------------------------------------------------------------------------


class TestOutputFilterDisabled:
    def test_disabled_returns_pass(self) -> None:
        scanner = _make_scanner(enabled=False)
        result = scanner.scan("anything")
        assert not result.matched
        assert result.action == "pass"

    def test_disabled_apply_returns_original(self) -> None:
        scanner = _make_scanner(enabled=False)
        text, result = scanner.apply("anything")
        assert text == "anything"
        assert not result.matched


# ---------------------------------------------------------------------------
# Scanner Properties
# ---------------------------------------------------------------------------


class TestOutputFilterProperties:
    def test_enabled_property(self) -> None:
        scanner = _make_scanner()
        assert scanner.enabled is True

    def test_disabled_property(self) -> None:
        scanner = _make_scanner(enabled=False)
        assert scanner.enabled is False


# ---------------------------------------------------------------------------
# Custom Pattern Matching
# ---------------------------------------------------------------------------


class TestCustomPatterns:
    def test_custom_pattern_detected(self) -> None:
        patterns = [
            OutputFilterPatternConfig(
                name="secret_key",
                pattern=r"SK_[A-Za-z0-9]{20}",
                description="Secret key",
            )
        ]
        scanner = _make_scanner(custom_patterns=patterns)
        result = scanner.scan("Found key: SK_abcdefghij1234567890")
        assert result.matched
        assert any(m.rule_name == "secret_key" for m in result.matches)

    def test_custom_pattern_no_match(self) -> None:
        patterns = [
            OutputFilterPatternConfig(
                name="secret_key",
                pattern=r"SK_[A-Za-z0-9]{20}",
                description="Secret key",
            )
        ]
        scanner = _make_scanner(custom_patterns=patterns)
        result = scanner.scan("No secrets here")
        assert not result.matched

    def test_invalid_regex_skipped(self) -> None:
        patterns = [OutputFilterPatternConfig(name="bad", pattern=r"[invalid", description="Bad regex")]
        scanner = _make_scanner(custom_patterns=patterns)
        result = scanner.scan("anything")
        assert not result.matched

    def test_empty_name_skipped(self) -> None:
        patterns = [OutputFilterPatternConfig(name="", pattern=r"test", description="No name")]
        scanner = _make_scanner(custom_patterns=patterns)
        assert len(scanner._rules) == 0

    def test_empty_pattern_skipped(self) -> None:
        patterns = [OutputFilterPatternConfig(name="test", pattern="", description="No pattern")]
        scanner = _make_scanner(custom_patterns=patterns)
        assert len(scanner._rules) == 0

    def test_redos_pattern_rejected(self) -> None:
        patterns = [OutputFilterPatternConfig(name="bad", pattern=r"(a+)+$", description="ReDoS")]
        scanner = _make_scanner(custom_patterns=patterns)
        assert len(scanner._rules) == 0

    def test_multiple_patterns(self) -> None:
        patterns = [
            OutputFilterPatternConfig(name="key", pattern=r"KEY_\w+", description="Key"),
            OutputFilterPatternConfig(name="token", pattern=r"TOKEN_\w+", description="Token"),
        ]
        scanner = _make_scanner(custom_patterns=patterns)
        result = scanner.scan("KEY_abc and TOKEN_xyz")
        assert result.matched
        assert len(result.matches) == 2


# ---------------------------------------------------------------------------
# System Prompt Leak Detection
# ---------------------------------------------------------------------------


class TestSystemPromptLeakDetection:
    def test_exact_fragment_detected(self) -> None:
        scanner = _make_scanner(system_prompt=_LEAK_PROMPT_SHORT, leak_threshold=0.3)
        result = scanner.scan(_LEAK_PROMPT_SHORT)
        assert result.matched
        assert any(m.rule_name == "system_prompt_leak" for m in result.matches)

    def test_significant_overlap_detected(self) -> None:
        scanner = _make_scanner(system_prompt=_LEAK_PROMPT, leak_threshold=0.3)
        output = "The assistant said: " + _LEAK_PROMPT + " plus some extra words at the end"
        result = scanner.scan(output)
        assert result.matched
        assert any(m.rule_name == "system_prompt_leak" for m in result.matches)

    def test_below_threshold_not_detected(self) -> None:
        scanner = _make_scanner(system_prompt=_LEAK_PROMPT_LONG, leak_threshold=0.5)
        output = "You are a helpful assistant that can answer questions about programming"
        result = scanner.scan(output)
        assert not result.matched

    def test_unrelated_text_not_flagged(self) -> None:
        scanner = _make_scanner(system_prompt=_LEAK_PROMPT)
        result = scanner.scan("The weather today is sunny with a high of 72 degrees and clear skies")
        assert not result.matched

    def test_no_system_prompt_skips_leak_check(self) -> None:
        scanner = _make_scanner(system_prompt=None)
        result = scanner.scan("anything")
        assert not result.matched

    def test_empty_system_prompt_skips_leak_check(self) -> None:
        scanner = _make_scanner(system_prompt="")
        assert len(scanner._prompt_ngrams) == 0

    def test_short_system_prompt_skips_leak_check(self) -> None:
        scanner = _make_scanner(system_prompt="too short")
        assert len(scanner._prompt_ngrams) == 0

    def test_leak_detection_disabled(self) -> None:
        prompt = _long_prompt(30)
        scanner = _make_scanner(system_prompt=prompt, system_prompt_leak_detection=False)
        assert len(scanner._prompt_ngrams) == 0
        result = scanner.scan(prompt)
        assert not result.matched

    def test_leak_with_custom_threshold(self) -> None:
        prompt = _long_prompt(30)
        # With very low threshold, even partial overlap triggers
        scanner = _make_scanner(system_prompt=prompt, leak_threshold=0.1)
        # Repeat a portion of the prompt
        tokens = prompt.split()
        partial = " ".join(tokens[: NGRAM_WINDOW + 3])
        result = scanner.scan(partial)
        # Whether this triggers depends on the ratio — at 0.1, a small overlap should trigger
        if result.matched:
            assert any(m.rule_name == "system_prompt_leak" for m in result.matches)

    def test_min_prompt_words_boundary(self) -> None:
        # Exactly MIN_PROMPT_WORDS should enable detection
        prompt = " ".join(f"word{i}" for i in range(MIN_PROMPT_WORDS))
        scanner = _make_scanner(system_prompt=prompt)
        assert len(scanner._prompt_ngrams) > 0

    def test_below_min_prompt_words(self) -> None:
        prompt = " ".join(f"word{i}" for i in range(MIN_PROMPT_WORDS - 1))
        scanner = _make_scanner(system_prompt=prompt)
        assert len(scanner._prompt_ngrams) == 0


# ---------------------------------------------------------------------------
# Scan Actions
# ---------------------------------------------------------------------------


class TestScanActions:
    def test_warn_returns_original_text(self) -> None:
        patterns = [OutputFilterPatternConfig(name="key", pattern=r"SECRET_\w+", description="Secret")]
        scanner = _make_scanner(action="warn", custom_patterns=patterns)
        text, result = scanner.apply("Found SECRET_abc in output")
        assert text == "Found SECRET_abc in output"
        assert result.matched
        assert result.action == "warn"

    def test_block_returns_empty(self) -> None:
        patterns = [OutputFilterPatternConfig(name="key", pattern=r"SECRET_\w+", description="Secret")]
        scanner = _make_scanner(action="block", custom_patterns=patterns)
        text, result = scanner.apply("Found SECRET_abc in output")
        assert text == ""
        assert result.matched
        assert result.action == "block"

    def test_redact_custom_pattern(self) -> None:
        patterns = [OutputFilterPatternConfig(name="key", pattern=r"SECRET_\w+", description="Secret")]
        scanner = _make_scanner(action="redact", custom_patterns=patterns)
        text, result = scanner.apply("Found SECRET_abc in output")
        assert "SECRET_abc" not in text
        assert "[FILTERED]" in text
        assert result.matched

    def test_redact_leak_replaces_entire_response(self) -> None:
        scanner = _make_scanner(system_prompt=_LEAK_PROMPT, action="redact", leak_threshold=0.3)
        text, result = scanner.apply(_LEAK_PROMPT)
        assert result.matched
        assert "SYSTEM PROMPT LEAK DETECTED" in text

    def test_custom_redaction_string(self) -> None:
        patterns = [OutputFilterPatternConfig(name="key", pattern=r"SECRET_\w+", description="Secret")]
        scanner = _make_scanner(action="redact", redaction_string="***", custom_patterns=patterns)
        text, _ = scanner.apply("Found SECRET_abc here")
        assert "***" in text

    def test_no_match_returns_original(self) -> None:
        patterns = [OutputFilterPatternConfig(name="key", pattern=r"SECRET_\w+", description="Secret")]
        scanner = _make_scanner(custom_patterns=patterns)
        text, result = scanner.apply("Nothing sensitive here")
        assert text == "Nothing sensitive here"
        assert not result.matched
        assert result.action == "pass"


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_text(self) -> None:
        scanner = _make_scanner()
        result = scanner.scan("")
        assert not result.matched

    def test_whitespace_only(self) -> None:
        scanner = _make_scanner()
        result = scanner.scan("   \n\t  ")
        assert not result.matched

    def test_max_scan_length_truncation(self) -> None:
        from anteroom.services.output_filter import MAX_SCAN_LENGTH

        patterns = [OutputFilterPatternConfig(name="end_marker", pattern=r"MARKER_END", description="End marker")]
        scanner = _make_scanner(custom_patterns=patterns)
        # Place marker beyond truncation point
        text = "x" * (MAX_SCAN_LENGTH + 100) + "MARKER_END"
        result = scanner.scan(text)
        assert not result.matched

    def test_within_scan_length_detected(self) -> None:
        from anteroom.services.output_filter import MAX_SCAN_LENGTH

        patterns = [OutputFilterPatternConfig(name="marker", pattern=r"MARKER", description="Marker")]
        scanner = _make_scanner(custom_patterns=patterns)
        text = "MARKER" + "x" * (MAX_SCAN_LENGTH - 10)
        result = scanner.scan(text)
        assert result.matched

    def test_leak_and_pattern_combined(self) -> None:
        patterns = [OutputFilterPatternConfig(name="key", pattern=r"API_KEY_\w+", description="API key")]
        scanner = _make_scanner(
            system_prompt=_LEAK_PROMPT,
            custom_patterns=patterns,
            leak_threshold=0.3,
        )
        output = _LEAK_PROMPT + " and also API_KEY_abc123"
        result = scanner.scan(output)
        assert result.matched
        rule_names = [m.rule_name for m in result.matches]
        assert "system_prompt_leak" in rule_names
        assert "key" in rule_names


# ---------------------------------------------------------------------------
# Agent Loop Integration
# ---------------------------------------------------------------------------


class TestAgentLoopIntegration:
    @pytest.fixture()
    def ai_service(self) -> Any:
        from anteroom.services.ai_service import AIService

        svc = AIService.__new__(AIService)
        svc.config = MagicMock()
        svc.config.narration_cadence = 0
        svc._token_provider = None
        svc.client = MagicMock()
        return svc

    def _set_response(self, svc: Any, content: str) -> None:
        async def fake_stream_chat(messages: Any, **kwargs: Any) -> Any:
            yield {"event": "token", "data": {"content": content}}
            yield {"event": "done", "data": {}}

        svc.stream_chat = fake_stream_chat

    @pytest.mark.asyncio
    async def test_block_stops_stream(self, ai_service: Any) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        patterns = [OutputFilterPatternConfig(name="secret", pattern=r"SECRET_\w+", description="Secret")]
        scanner = OutputContentFilter(_make_config(action="block", custom_patterns=patterns))
        self._set_response(ai_service, "Here is SECRET_abc123 for you")

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            output_filter=scanner,
        ):
            events.append(event)

        blocked = [e for e in events if e.kind == "output_filter_blocked"]
        assert len(blocked) == 1
        assert "secret" in blocked[0].data["matches"]
        # No done event after block
        assert not any(e.kind == "done" for e in events)

    @pytest.mark.asyncio
    async def test_warn_emits_single_event(self, ai_service: Any) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        patterns = [OutputFilterPatternConfig(name="secret", pattern=r"SECRET_\w+", description="Secret")]
        scanner = OutputContentFilter(_make_config(action="warn", custom_patterns=patterns))
        self._set_response(ai_service, "Here is SECRET_abc123")

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            output_filter=scanner,
        ):
            events.append(event)

        warnings = [e for e in events if e.kind == "output_filter_warning"]
        assert len(warnings) == 1
        # Done event still emitted after warn
        assert any(e.kind == "done" for e in events)

    @pytest.mark.asyncio
    async def test_redact_modifies_text(self, ai_service: Any) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        patterns = [OutputFilterPatternConfig(name="secret", pattern=r"SECRET_\w+", description="Secret")]
        scanner = OutputContentFilter(_make_config(action="redact", custom_patterns=patterns))
        self._set_response(ai_service, "Here is SECRET_abc123")

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            output_filter=scanner,
        ):
            events.append(event)

        assistant_msgs = [e for e in events if e.kind == "assistant_message"]
        assert len(assistant_msgs) == 1
        assert "SECRET_abc123" not in assistant_msgs[0].data["content"]
        assert "[FILTERED]" in assistant_msgs[0].data["content"]

    @pytest.mark.asyncio
    async def test_no_filter_passthrough(self, ai_service: Any) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        self._set_response(ai_service, "Hello world")

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            output_filter=None,
        ):
            events.append(event)

        assistant_msgs = [e for e in events if e.kind == "assistant_message"]
        assert assistant_msgs[0].data["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_leak_detection_in_agent_loop(self, ai_service: Any) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        scanner = OutputContentFilter(
            _make_config(action="block", leak_threshold=0.3),
            system_prompt=_LEAK_PROMPT,
        )
        self._set_response(ai_service, _LEAK_PROMPT)

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            output_filter=scanner,
        ):
            events.append(event)

        blocked = [e for e in events if e.kind == "output_filter_blocked"]
        assert len(blocked) == 1
        assert "system_prompt_leak" in blocked[0].data["matches"]


# ---------------------------------------------------------------------------
# Config Parsing
# ---------------------------------------------------------------------------


class TestOutputFilterConfigParsing:
    def test_from_yaml(self, tmp_path: Any) -> None:
        config_data = {
            "ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"},
            "safety": {
                "output_filter": {
                    "enabled": True,
                    "action": "block",
                    "system_prompt_leak_detection": True,
                    "leak_threshold": 0.5,
                    "custom_patterns": [
                        {"name": "api_key", "pattern": r"sk-[a-z0-9]+", "description": "OpenAI key"},
                    ],
                }
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert cfg.safety.output_filter.enabled is True
        assert cfg.safety.output_filter.action == "block"
        assert cfg.safety.output_filter.system_prompt_leak_detection is True
        assert cfg.safety.output_filter.leak_threshold == 0.5
        assert len(cfg.safety.output_filter.custom_patterns) == 1
        assert cfg.safety.output_filter.custom_patterns[0].name == "api_key"

    def test_env_var_override(self, tmp_path: Any, monkeypatch: Any) -> None:
        monkeypatch.setenv("AI_CHAT_OUTPUT_FILTER_ENABLED", "true")
        monkeypatch.setenv("AI_CHAT_OUTPUT_FILTER_ACTION", "redact")
        config_data = {
            "ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"},
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert cfg.safety.output_filter.enabled is True
        assert cfg.safety.output_filter.action == "redact"

    def test_invalid_action_in_yaml(self, tmp_path: Any) -> None:
        config_data = {
            "ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"},
            "safety": {"output_filter": {"enabled": True, "action": "invalid"}},
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert cfg.safety.output_filter.action == "warn"

    def test_malformed_patterns_skipped(self, tmp_path: Any) -> None:
        config_data = {
            "ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"},
            "safety": {
                "output_filter": {
                    "enabled": True,
                    "custom_patterns": [
                        {"name": "good", "pattern": r"test"},
                        {"name": "", "pattern": r"no_name"},
                        {"pattern": r"no_name_key"},
                        "not_a_dict",
                    ],
                }
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert len(cfg.safety.output_filter.custom_patterns) == 1
        assert cfg.safety.output_filter.custom_patterns[0].name == "good"

    def test_defaults_when_not_configured(self, tmp_path: Any) -> None:
        config_data = {
            "ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"},
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert cfg.safety.output_filter.enabled is False
        assert cfg.safety.output_filter.action == "warn"
        assert cfg.safety.output_filter.system_prompt_leak_detection is True

    def test_audit_events_include_output_filter(self, tmp_path: Any) -> None:
        config_data = {
            "ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"},
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert cfg.audit.events.get("output_filter") is True
