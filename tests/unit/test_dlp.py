"""Tests for DLP scanning pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from anteroom.config import DlpConfig, DlpPatternConfig
from anteroom.services.dlp import BUILTIN_PATTERNS, DlpScanner

# --- Helpers ---


def _make_config(**overrides: object) -> DlpConfig:
    defaults: dict[str, object] = {"enabled": True}
    defaults.update(overrides)
    return DlpConfig(**defaults)  # type: ignore[arg-type]


def _make_scanner(**overrides: object) -> DlpScanner:
    return DlpScanner(_make_config(**overrides))


# --- DlpConfig validation ---


class TestDlpConfig:
    def test_defaults(self) -> None:
        cfg = DlpConfig()
        assert cfg.enabled is False
        assert cfg.scan_output is True
        assert cfg.scan_input is False
        assert cfg.action == "redact"
        assert cfg.patterns == []
        assert cfg.custom_patterns == []
        assert cfg.redaction_string == "[REDACTED]"
        assert cfg.log_detections is True

    def test_invalid_action_defaults_to_redact(self) -> None:
        cfg = DlpConfig(action="invalid")
        assert cfg.action == "redact"

    def test_valid_actions_unchanged(self) -> None:
        for action in ("redact", "block", "warn"):
            cfg = DlpConfig(action=action)
            assert cfg.action == action


# --- Scanner disabled ---


class TestDlpScannerDisabled:
    def test_disabled_scanner_returns_pass(self) -> None:
        scanner = DlpScanner(DlpConfig(enabled=False))
        result = scanner.scan("123-45-6789")
        assert not result.matched
        assert result.action == "pass"

    def test_disabled_scanner_apply_returns_original(self) -> None:
        scanner = DlpScanner(DlpConfig(enabled=False))
        text, result = scanner.apply("123-45-6789")
        assert text == "123-45-6789"
        assert not result.matched


# --- Scanner properties ---


class TestDlpScannerProperties:
    def test_enabled_property(self) -> None:
        scanner = _make_scanner(enabled=True)
        assert scanner.enabled is True

    def test_scan_output_property(self) -> None:
        scanner = _make_scanner(scan_output=True)
        assert scanner.scan_output is True

    def test_scan_input_property(self) -> None:
        scanner = _make_scanner(scan_input=True)
        assert scanner.scan_input is True


# --- Built-in patterns ---


class TestBuiltInPatterns:
    def test_builtin_patterns_loaded_when_no_custom(self) -> None:
        scanner = _make_scanner()
        assert len(scanner._rules) == len(BUILTIN_PATTERNS)

    @pytest.mark.parametrize(
        "text",
        [
            "My SSN is 123-45-6789",
            "SSN: 999-88-7777",
            "Number 111-22-3333 is sensitive",
        ],
    )
    def test_ssn_detected(self, text: str) -> None:
        scanner = _make_scanner()
        result = scanner.scan(text)
        assert result.matched
        assert any(m.rule_name == "ssn" for m in result.matches)

    @pytest.mark.parametrize(
        "text",
        [
            "Phone: 123-456-7890",
            "Date: 2024-01-15",
            "Code: ABC-DE-FGHI",
        ],
    )
    def test_ssn_not_false_positive(self, text: str) -> None:
        scanner = _make_scanner(
            patterns=[DlpPatternConfig(name="ssn", pattern=r"\b\d{3}-\d{2}-\d{4}\b", description="SSN")]
        )
        result = scanner.scan(text)
        ssn_matches = [m for m in result.matches if m.rule_name == "ssn"]
        assert len(ssn_matches) == 0

    @pytest.mark.parametrize(
        "text",
        [
            "Card: 4111111111111111",
            "Visa: 4111 1111 1111 1111",
            "MC: 5500-0000-0000-0004",
        ],
    )
    def test_credit_card_detected(self, text: str) -> None:
        scanner = _make_scanner()
        result = scanner.scan(text)
        assert result.matched
        assert any(m.rule_name == "credit_card" for m in result.matches)

    @pytest.mark.parametrize(
        "text",
        [
            "Email: user@example.com",
            "Contact john.doe+tag@company.co.uk for info",
        ],
    )
    def test_email_detected(self, text: str) -> None:
        scanner = _make_scanner()
        result = scanner.scan(text)
        assert result.matched
        assert any(m.rule_name == "email" for m in result.matches)

    @pytest.mark.parametrize(
        "text",
        [
            "Call 555-123-4567",
            "Tel: +1-555-123-4567",
        ],
    )
    def test_phone_detected(self, text: str) -> None:
        scanner = _make_scanner()
        result = scanner.scan(text)
        assert result.matched
        assert any(m.rule_name == "phone_us" for m in result.matches)


# --- Custom patterns ---


class TestCustomPatterns:
    def test_custom_pattern_matches(self) -> None:
        scanner = _make_scanner(
            patterns=[DlpPatternConfig(name="acct", pattern=r"ACCT-\d{8}", description="Account number")]
        )
        result = scanner.scan("Your account ACCT-12345678 is active")
        assert result.matched
        assert result.matches[0].rule_name == "acct"
        assert result.matches[0].match_count == 1

    def test_custom_pattern_no_match(self) -> None:
        scanner = _make_scanner(
            patterns=[DlpPatternConfig(name="acct", pattern=r"ACCT-\d{8}", description="Account number")]
        )
        result = scanner.scan("No account numbers here")
        assert not result.matched

    def test_invalid_regex_skipped(self) -> None:
        scanner = _make_scanner(
            patterns=[
                DlpPatternConfig(name="bad", pattern="[invalid", description="broken"),
                DlpPatternConfig(name="good", pattern=r"\bsecret\b", description="keyword"),
            ]
        )
        assert len(scanner._rules) == 1
        assert scanner._rules[0].name == "good"

    def test_empty_name_or_pattern_skipped(self) -> None:
        scanner = _make_scanner(
            patterns=[
                DlpPatternConfig(name="", pattern=r"\d+", description="no name"),
                DlpPatternConfig(name="nopattern", pattern="", description="no pattern"),
                DlpPatternConfig(name="valid", pattern=r"\btest\b", description="valid"),
            ]
        )
        assert len(scanner._rules) == 1
        assert scanner._rules[0].name == "valid"

    def test_custom_patterns_merged_with_configured(self) -> None:
        cfg = DlpConfig(
            enabled=True,
            patterns=[DlpPatternConfig(name="p1", pattern=r"\bp1\b", description="first")],
            custom_patterns=[DlpPatternConfig(name="p2", pattern=r"\bp2\b", description="second")],
        )
        scanner = DlpScanner(cfg)
        assert len(scanner._rules) == 2
        names = {r.name for r in scanner._rules}
        assert names == {"p1", "p2"}


# --- Actions ---


class TestScanActions:
    def test_redact_replaces_matches(self) -> None:
        scanner = _make_scanner(action="redact")
        text, result = scanner.apply("SSN: 123-45-6789")
        assert result.matched
        assert result.action == "redact"
        assert "123-45-6789" not in text
        assert "[REDACTED]" in text

    def test_redact_custom_string(self) -> None:
        scanner = _make_scanner(action="redact", redaction_string="***")
        text, result = scanner.apply("SSN: 123-45-6789")
        assert "***" in text
        assert "123-45-6789" not in text

    def test_block_returns_empty(self) -> None:
        scanner = _make_scanner(action="block")
        text, result = scanner.apply("SSN: 123-45-6789")
        assert result.matched
        assert result.action == "block"
        assert text == ""

    def test_warn_returns_original(self) -> None:
        scanner = _make_scanner(action="warn")
        text, result = scanner.apply("SSN: 123-45-6789")
        assert result.matched
        assert result.action == "warn"
        assert text == "SSN: 123-45-6789"

    def test_no_match_returns_original(self) -> None:
        scanner = _make_scanner()
        text, result = scanner.apply("Hello world, nothing sensitive here")
        assert not result.matched
        assert text == "Hello world, nothing sensitive here"


# --- Direction filtering ---


class TestDirectionFiltering:
    def test_output_scan_skipped_when_disabled(self) -> None:
        scanner = _make_scanner(scan_output=False)
        result = scanner.scan("SSN: 123-45-6789", direction="output")
        assert not result.matched

    def test_input_scan_skipped_when_disabled(self) -> None:
        scanner = _make_scanner(scan_input=False)
        result = scanner.scan("SSN: 123-45-6789", direction="input")
        assert not result.matched

    def test_input_scan_works_when_enabled(self) -> None:
        scanner = _make_scanner(scan_input=True)
        result = scanner.scan("SSN: 123-45-6789", direction="input")
        assert result.matched

    def test_output_scan_works_when_enabled(self) -> None:
        scanner = _make_scanner(scan_output=True)
        result = scanner.scan("SSN: 123-45-6789", direction="output")
        assert result.matched


# --- Edge cases ---


class TestEdgeCases:
    def test_empty_string(self) -> None:
        scanner = _make_scanner()
        result = scanner.scan("")
        assert not result.matched

    def test_whitespace_only(self) -> None:
        scanner = _make_scanner()
        result = scanner.scan("   \n\t  ")
        assert not result.matched

    def test_multiple_matches_same_rule(self) -> None:
        scanner = _make_scanner(
            patterns=[DlpPatternConfig(name="ssn", pattern=r"\b\d{3}-\d{2}-\d{4}\b", description="SSN")]
        )
        result = scanner.scan("SSNs: 123-45-6789 and 987-65-4321")
        assert result.matched
        assert result.matches[0].match_count == 2

    def test_multiple_rules_match(self) -> None:
        scanner = _make_scanner()
        result = scanner.scan("SSN: 123-45-6789, email: user@example.com")
        assert result.matched
        assert len(result.matches) >= 2
        names = {m.rule_name for m in result.matches}
        assert "ssn" in names
        assert "email" in names

    def test_redact_multiple_patterns(self) -> None:
        scanner = _make_scanner(action="redact")
        text, result = scanner.apply("SSN: 123-45-6789, email: user@example.com")
        assert "123-45-6789" not in text
        assert "user@example.com" not in text
        assert text.count("[REDACTED]") >= 2


# --- Security: ReDoS protection and length guards ---


class TestSecurityGuards:
    def test_max_scan_length_truncates(self) -> None:
        from anteroom.services.dlp import MAX_SCAN_LENGTH

        scanner = _make_scanner(patterns=[DlpPatternConfig(name="test", pattern=r"SENSITIVE", description="test")])
        # Pattern placed beyond the max scan length — should NOT be detected
        text = "x" * (MAX_SCAN_LENGTH + 100) + "SENSITIVE"
        result = scanner.scan(text)
        assert not result.matched

    def test_within_scan_length_detected(self) -> None:
        scanner = _make_scanner(patterns=[DlpPatternConfig(name="test", pattern=r"SENSITIVE", description="test")])
        text = "SENSITIVE plus more text"
        result = scanner.scan(text)
        assert result.matched

    def test_pathological_pattern_rejected(self) -> None:
        scanner = _make_scanner(patterns=[DlpPatternConfig(name="evil", pattern=r"(a+)+$", description="ReDoS")])
        # The pathological pattern should be rejected during init
        assert len(scanner._rules) == 0

    def test_builtin_patterns_are_redos_safe(self) -> None:
        scanner = _make_scanner()
        # Ensure all built-in patterns load (none rejected by safety check)
        assert len(scanner._rules) == len(BUILTIN_PATTERNS)

    def test_iban_detected(self) -> None:
        scanner = _make_scanner()
        result = scanner.scan("IBAN: GB29NWBK60161331926819")
        assert result.matched
        assert any(m.rule_name == "iban" for m in result.matches)


# --- Agent loop integration ---


class TestAgentLoopIntegration:
    @pytest.mark.asyncio
    async def test_token_redacted_in_agent_loop(self) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop
        from anteroom.services.ai_service import AIService

        ai_service = AIService.__new__(AIService)
        ai_service.config = MagicMock()
        ai_service.config.narration_cadence = 0
        ai_service._token_provider = None
        ai_service.client = MagicMock()

        async def fake_stream_chat(messages, **kwargs):
            yield {"event": "token", "data": {"content": "Your SSN is 123-45-6789"}}
            yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        scanner = _make_scanner(action="redact")

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            dlp_scanner=scanner,
        ):
            events.append(event)

        token_events = [e for e in events if e.kind == "token"]
        assert len(token_events) == 1
        assert "123-45-6789" not in token_events[0].data["content"]
        assert "[REDACTED]" in token_events[0].data["content"]

    @pytest.mark.asyncio
    async def test_block_emits_dlp_blocked_event(self) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop
        from anteroom.services.ai_service import AIService

        ai_service = AIService.__new__(AIService)
        ai_service.config = MagicMock()
        ai_service.config.narration_cadence = 0
        ai_service._token_provider = None
        ai_service.client = MagicMock()

        async def fake_stream_chat(messages, **kwargs):
            yield {"event": "token", "data": {"content": "SSN: 123-45-6789"}}
            yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        scanner = _make_scanner(action="block")

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            dlp_scanner=scanner,
        ):
            events.append(event)

        blocked_events = [e for e in events if e.kind == "dlp_blocked"]
        assert len(blocked_events) == 1
        assert blocked_events[0].data["direction"] == "output"
        # Block should NOT emit a done event after blocking
        done_events = [e for e in events if e.kind == "done"]
        assert len(done_events) == 0

    @pytest.mark.asyncio
    async def test_warn_emits_dlp_warning_event(self) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop
        from anteroom.services.ai_service import AIService

        ai_service = AIService.__new__(AIService)
        ai_service.config = MagicMock()
        ai_service.config.narration_cadence = 0
        ai_service._token_provider = None
        ai_service.client = MagicMock()

        async def fake_stream_chat(messages, **kwargs):
            yield {"event": "token", "data": {"content": "SSN: 123-45-6789"}}
            yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        scanner = _make_scanner(action="warn")

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            dlp_scanner=scanner,
        ):
            events.append(event)

        warning_events = [e for e in events if e.kind == "dlp_warning"]
        # Exactly 1 warning: from the final assembled-text scan only (no per-chunk duplicate)
        assert len(warning_events) == 1

        # Token content should be preserved (warn doesn't modify)
        token_events = [e for e in events if e.kind == "token"]
        assert any("123-45-6789" in e.data["content"] for e in token_events)

    @pytest.mark.asyncio
    async def test_no_scanner_passes_through(self) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop
        from anteroom.services.ai_service import AIService

        ai_service = AIService.__new__(AIService)
        ai_service.config = MagicMock()
        ai_service.config.narration_cadence = 0
        ai_service._token_provider = None
        ai_service.client = MagicMock()

        async def fake_stream_chat(messages, **kwargs):
            yield {"event": "token", "data": {"content": "SSN: 123-45-6789"}}
            yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            dlp_scanner=None,
        ):
            events.append(event)

        token_events = [e for e in events if e.kind == "token"]
        assert "123-45-6789" in token_events[0].data["content"]

    @pytest.mark.asyncio
    async def test_final_scan_catches_cross_chunk_pattern(self) -> None:
        """Final assembled-text scan catches patterns split across streaming chunks."""
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop
        from anteroom.services.ai_service import AIService

        ai_service = AIService.__new__(AIService)
        ai_service.config = MagicMock()
        ai_service.config.narration_cadence = 0
        ai_service._token_provider = None
        ai_service.client = MagicMock()

        # SSN split across two chunks: "123-45" + "-6789"
        async def fake_stream_chat(messages, **kwargs):
            yield {"event": "token", "data": {"content": "SSN is 123-45"}}
            yield {"event": "token", "data": {"content": "-6789 ok"}}
            yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        scanner = _make_scanner(action="block")

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            dlp_scanner=scanner,
        ):
            events.append(event)

        # The final assembled-text scan should catch the cross-chunk SSN
        blocked = [e for e in events if e.kind == "dlp_blocked"]
        assert len(blocked) == 1

    @pytest.mark.asyncio
    async def test_scan_output_false_bypasses_dlp_in_agent_loop(self) -> None:
        """Scanner with scan_output=False should not scan agent output."""
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop
        from anteroom.services.ai_service import AIService

        ai_service = AIService.__new__(AIService)
        ai_service.config = MagicMock()
        ai_service.config.narration_cadence = 0
        ai_service._token_provider = None
        ai_service.client = MagicMock()

        async def fake_stream_chat(messages, **kwargs):
            yield {"event": "token", "data": {"content": "SSN: 123-45-6789"}}
            yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        scanner = _make_scanner(action="block", scan_output=False)

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
            dlp_scanner=scanner,
        ):
            events.append(event)

        # No blocking — scan_output is disabled
        blocked = [e for e in events if e.kind == "dlp_blocked"]
        assert len(blocked) == 0
        # Token passes through unmodified
        tokens = [e for e in events if e.kind == "token"]
        assert "123-45-6789" in tokens[0].data["content"]


# --- Config parsing ---


class TestDlpConfigParsing:
    def test_default_dlp_in_safety_config(self) -> None:
        from anteroom.config import SafetyConfig

        cfg = SafetyConfig()
        assert isinstance(cfg.dlp, DlpConfig)
        assert cfg.dlp.enabled is False

    def test_dlp_from_yaml(self, tmp_path) -> None:
        import yaml

        from anteroom.config import load_config

        config_data = {
            "ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"},
            "safety": {
                "dlp": {
                    "enabled": True,
                    "action": "block",
                    "scan_input": True,
                    "patterns": [
                        {"name": "ssn", "pattern": r"\b\d{3}-\d{2}-\d{4}\b", "description": "SSN"},
                    ],
                    "custom_patterns": [
                        {"name": "acct", "pattern": r"ACCT-\d+", "description": "Account"},
                    ],
                }
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert cfg.safety.dlp.enabled is True
        assert cfg.safety.dlp.action == "block"
        assert cfg.safety.dlp.scan_input is True
        assert len(cfg.safety.dlp.patterns) == 1
        assert cfg.safety.dlp.patterns[0].name == "ssn"
        assert len(cfg.safety.dlp.custom_patterns) == 1
        assert cfg.safety.dlp.custom_patterns[0].name == "acct"

    def test_dlp_env_var_override(self, tmp_path, monkeypatch) -> None:
        import yaml

        from anteroom.config import load_config

        monkeypatch.setenv("AI_CHAT_DLP_ENABLED", "true")
        monkeypatch.setenv("AI_CHAT_DLP_ACTION", "warn")
        config_data = {"ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"}}
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert cfg.safety.dlp.enabled is True
        assert cfg.safety.dlp.action == "warn"

    def test_dlp_invalid_action_from_yaml(self, tmp_path) -> None:
        import yaml

        from anteroom.config import load_config

        config_data = {
            "ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"},
            "safety": {"dlp": {"enabled": True, "action": "invalid_action"}},
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert cfg.safety.dlp.action == "redact"

    def test_dlp_malformed_patterns_skipped(self, tmp_path) -> None:
        import yaml

        from anteroom.config import load_config

        config_data = {
            "ai": {"api_key": "test-key", "model": "gpt-4", "base_url": "http://localhost:1234"},
            "safety": {
                "dlp": {
                    "enabled": True,
                    "patterns": [
                        {"name": "valid", "pattern": r"\d+", "description": "numbers"},
                        {"name": "", "pattern": r"\d+"},  # empty name
                        {"pattern": r"\d+"},  # missing name
                        "not_a_dict",  # wrong type
                    ],
                }
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config_data))
        cfg, _ = load_config(cfg_file)
        assert len(cfg.safety.dlp.patterns) == 1
        assert cfg.safety.dlp.patterns[0].name == "valid"

    def test_audit_events_include_dlp_by_default(self) -> None:
        from anteroom.config import AuditConfig

        cfg = AuditConfig()
        assert cfg.events.get("dlp") is True
