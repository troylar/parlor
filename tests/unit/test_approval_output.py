"""Tests for approval prompt output escaping (#111).

Verifies that tool names containing Rich markup characters are properly
escaped in the approval confirmation/denial messages printed by
_confirm_destructive() in cli/repl.py.
"""

from __future__ import annotations

import pytest
from rich.markup import escape


class TestApprovalOutputEscaping:
    """Ensure tool names with Rich markup chars don't corrupt output."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "server[tool]",
            "ns:tool[0]",
            "[red]evil[/red]",
            "mcp__server__list[all]",
            "tool:with:colons",
            "bracket]only",
        ],
    )
    def test_escape_prevents_markup_injection_in_allowed_message(self, tool_name: str) -> None:
        escaped = escape(tool_name)
        msg = f"  [dim]✓ Allowed: {escaped} (session)[/dim]"
        assert tool_name.replace("[", "\\[") in msg or escaped in msg
        assert "[red]" not in msg or "\\[red]" in msg

    @pytest.mark.parametrize(
        "tool_name",
        [
            "server[tool]",
            "[red]evil[/red]",
        ],
    )
    def test_escape_prevents_markup_injection_in_denied_message(self, tool_name: str) -> None:
        escaped = escape(tool_name)
        msg = f"  [dim]✗ Denied: {escaped}[/dim]"
        assert escaped in msg

    def test_escape_is_identity_for_safe_names(self) -> None:
        safe_names = ["bash", "read_file", "write_file", "mcp__server__tool"]
        for name in safe_names:
            assert escape(name) == name

    def test_unescaped_brackets_would_corrupt_markup(self) -> None:
        bad_name = "[red]injected[/red]"
        unescaped_msg = f"  [dim]✓ Allowed: {bad_name} (session)[/dim]"
        escaped_msg = f"  [dim]✓ Allowed: {escape(bad_name)} (session)[/dim]"
        assert "[red]" in unescaped_msg
        assert "\\[red]" in escaped_msg

    def test_all_scope_variants_use_same_pattern(self) -> None:
        tool_name = "[red]injected[/red]"
        escaped = escape(tool_name)
        for scope in ("always", "session", "once"):
            msg = f"  [dim]✓ Allowed: {escaped} ({scope})[/dim]"
            assert escaped in msg
            assert "\\[red]" in msg
