"""Tests for render_conversation_recap and render_help."""

from __future__ import annotations

from unittest.mock import patch

from anteroom.cli.renderer import render_conversation_recap, render_help


class TestRenderConversationRecap:
    def test_shows_last_exchange(self) -> None:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm doing well."},
        ]
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_conversation_recap(messages)
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "How are you?" in output
            assert "doing well" in output

    def test_no_messages(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_conversation_recap([])
            mock_console.print.assert_not_called()

    def test_only_user_message(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_conversation_recap(messages)
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "Hello" in output

    def test_skips_empty_content(self) -> None:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "Real response"},
        ]
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_conversation_recap(messages)
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "Real response" in output

    def test_truncates_long_user_message(self) -> None:
        messages = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "ok"},
        ]
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_conversation_recap(messages)
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "..." in output

    def test_truncates_long_assistant_message(self) -> None:
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "y" * 400},
        ]
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_conversation_recap(messages)
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "..." in output

    def test_skips_non_string_content(self) -> None:
        messages = [
            {"role": "user", "content": ["list", "content"]},
            {"role": "user", "content": "text message"},
        ]
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_conversation_recap(messages)
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "text message" in output

    def test_skips_tool_messages(self) -> None:
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "tool", "content": "tool output"},
            {"role": "assistant", "content": "done"},
        ]
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_conversation_recap(messages)
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "do something" in output
            assert "done" in output
            assert "tool output" not in output


class TestRenderHelp:
    def test_help_includes_all_commands(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_help()
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            for cmd in [
                "/new",
                "/last",
                "/list",
                "/search",
                "/resume",
                "/delete",
                "/rewind",
                "/compact",
                "/model",
                "/tools",
                "/skills",
                "/mcp",
                "/verbose",
                "/detail",
                "/quit",
            ]:
                assert cmd in output, f"Missing {cmd} in /help output"

    def test_help_includes_input_section(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_help()
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "@<path>" in output
            assert "Alt+Enter" in output
            assert "Escape" in output
