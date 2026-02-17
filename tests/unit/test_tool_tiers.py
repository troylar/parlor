"""Tests for tools/tiers.py — tool risk tiers and approval mode logic."""

from __future__ import annotations

from anteroom.tools.tiers import (
    DEFAULT_MCP_TIER,
    DEFAULT_TOOL_TIERS,
    ApprovalMode,
    ToolTier,
    get_tool_tier,
    parse_approval_mode,
    should_require_approval,
)


class TestToolTier:
    def test_tier_ordering(self) -> None:
        assert ToolTier.READ < ToolTier.WRITE < ToolTier.EXECUTE < ToolTier.DESTRUCTIVE

    def test_default_tiers_assigned(self) -> None:
        assert DEFAULT_TOOL_TIERS["read_file"] == ToolTier.READ
        assert DEFAULT_TOOL_TIERS["glob_files"] == ToolTier.READ
        assert DEFAULT_TOOL_TIERS["grep"] == ToolTier.READ
        assert DEFAULT_TOOL_TIERS["write_file"] == ToolTier.WRITE
        assert DEFAULT_TOOL_TIERS["edit_file"] == ToolTier.WRITE
        assert DEFAULT_TOOL_TIERS["bash"] == ToolTier.EXECUTE

    def test_canvas_tools_are_write_tier(self) -> None:
        for name in ("create_canvas", "update_canvas", "patch_canvas"):
            assert DEFAULT_TOOL_TIERS[name] == ToolTier.WRITE


class TestGetToolTier:
    def test_builtin_tool(self) -> None:
        assert get_tool_tier("read_file") == ToolTier.READ

    def test_unknown_tool_defaults_to_execute(self) -> None:
        assert get_tool_tier("some_mcp_tool") == DEFAULT_MCP_TIER

    def test_config_override(self) -> None:
        assert get_tool_tier("read_file", tier_overrides={"read_file": "destructive"}) == ToolTier.DESTRUCTIVE

    def test_invalid_override_falls_back_to_default(self) -> None:
        assert get_tool_tier("bash", tier_overrides={"bash": "invalid_tier"}) == ToolTier.EXECUTE

    def test_override_for_unknown_tool(self) -> None:
        assert get_tool_tier("my_mcp_tool", tier_overrides={"my_mcp_tool": "read"}) == ToolTier.READ


class TestParseApprovalMode:
    def test_all_valid_modes(self) -> None:
        assert parse_approval_mode("auto") == ApprovalMode.AUTO
        assert parse_approval_mode("ask_for_dangerous") == ApprovalMode.ASK_FOR_DANGEROUS
        assert parse_approval_mode("ask_for_writes") == ApprovalMode.ASK_FOR_WRITES
        assert parse_approval_mode("ask") == ApprovalMode.ASK

    def test_case_insensitive(self) -> None:
        assert parse_approval_mode("AUTO") == ApprovalMode.AUTO
        assert parse_approval_mode("Ask_For_Dangerous") == ApprovalMode.ASK_FOR_DANGEROUS

    def test_whitespace_stripped(self) -> None:
        assert parse_approval_mode("  auto  ") == ApprovalMode.AUTO

    def test_invalid_defaults_to_ask_for_writes(self) -> None:
        assert parse_approval_mode("invalid") == ApprovalMode.ASK_FOR_WRITES
        assert parse_approval_mode("") == ApprovalMode.ASK_FOR_WRITES


class TestShouldRequireApproval:
    def test_denied_tool_returns_none(self) -> None:
        result = should_require_approval("bash", ToolTier.EXECUTE, ApprovalMode.AUTO, denied_tools={"bash"})
        assert result is None

    def test_allowed_tool_returns_false(self) -> None:
        result = should_require_approval("bash", ToolTier.EXECUTE, ApprovalMode.ASK, allowed_tools={"bash"})
        assert result is False

    def test_session_allowed_returns_false(self) -> None:
        result = should_require_approval("bash", ToolTier.EXECUTE, ApprovalMode.ASK, session_allowed={"bash"})
        assert result is False

    def test_auto_mode_always_false(self) -> None:
        assert should_require_approval("bash", ToolTier.EXECUTE, ApprovalMode.AUTO) is False
        assert should_require_approval("bash", ToolTier.DESTRUCTIVE, ApprovalMode.AUTO) is False

    def test_ask_for_dangerous_only_destructive(self) -> None:
        assert should_require_approval("bash", ToolTier.EXECUTE, ApprovalMode.ASK_FOR_DANGEROUS) is False
        assert should_require_approval("bash", ToolTier.DESTRUCTIVE, ApprovalMode.ASK_FOR_DANGEROUS) is True

    def test_ask_for_writes_includes_write_tier(self) -> None:
        assert should_require_approval("write_file", ToolTier.WRITE, ApprovalMode.ASK_FOR_WRITES) is True
        assert should_require_approval("bash", ToolTier.EXECUTE, ApprovalMode.ASK_FOR_WRITES) is True
        assert should_require_approval("read_file", ToolTier.READ, ApprovalMode.ASK_FOR_WRITES) is False

    def test_ask_mode_includes_write_tier(self) -> None:
        assert should_require_approval("write_file", ToolTier.WRITE, ApprovalMode.ASK) is True
        assert should_require_approval("read_file", ToolTier.READ, ApprovalMode.ASK) is False

    def test_denied_overrides_allowed(self) -> None:
        result = should_require_approval(
            "bash",
            ToolTier.EXECUTE,
            ApprovalMode.AUTO,
            allowed_tools={"bash"},
            denied_tools={"bash"},
        )
        assert result is None  # denied wins

    def test_allowed_overrides_mode(self) -> None:
        result = should_require_approval(
            "write_file",
            ToolTier.WRITE,
            ApprovalMode.ASK,
            allowed_tools={"write_file"},
        )
        assert result is False

    def test_empty_sets_treated_as_none(self) -> None:
        result = should_require_approval("bash", ToolTier.EXECUTE, ApprovalMode.AUTO, denied_tools=set())
        assert result is False

    def test_read_tier_never_asked_in_ask_mode(self) -> None:
        assert should_require_approval("read_file", ToolTier.READ, ApprovalMode.ASK) is False
