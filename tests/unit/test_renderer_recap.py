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
        with (
            patch("anteroom.cli.renderer.console") as mock_console,
            patch("anteroom.cli.renderer._stdout_console"),
            patch("anteroom.cli.renderer._make_markdown") as mock_md,
        ):
            render_conversation_recap(messages)
            console_output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "How are you?" in console_output
            mock_md.assert_called_once_with("I'm doing well.")

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
        with (
            patch("anteroom.cli.renderer.console"),
            patch("anteroom.cli.renderer._stdout_console"),
            patch("anteroom.cli.renderer._make_markdown") as mock_md,
        ):
            render_conversation_recap(messages)
            mock_md.assert_called_once_with("Real response")

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
            {"role": "assistant", "content": "y" * 600},
        ]
        with (
            patch("anteroom.cli.renderer.console"),
            patch("anteroom.cli.renderer._stdout_console"),
            patch("anteroom.cli.renderer._make_markdown") as mock_md,
        ):
            render_conversation_recap(messages)
            truncated_text = mock_md.call_args[0][0]
            assert truncated_text.endswith("...")
            assert len(truncated_text) < 600

    def test_skips_non_string_content(self) -> None:
        messages = [
            {"role": "user", "content": ["list", "content"]},
            {"role": "user", "content": "text message"},
        ]
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_conversation_recap(messages)
            output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "text message" in output

    def test_assistant_rendered_as_markdown(self) -> None:
        """Assistant message should be rendered via _make_markdown, not plain escape."""
        messages = [
            {"role": "user", "content": "show code"},
            {"role": "assistant", "content": "Here is code:\n```python\nprint('hello')\n```"},
        ]
        with (
            patch("anteroom.cli.renderer.console"),
            patch("anteroom.cli.renderer._stdout_console") as mock_stdout,
            patch(
                "anteroom.cli.renderer._make_markdown",
                wraps=__import__("anteroom.cli.renderer", fromlist=["_make_markdown"])._make_markdown,
            ) as mock_md,
        ):
            render_conversation_recap(messages)
            mock_md.assert_called_once()
            mock_stdout.print.assert_called_once()

    def test_truncation_preserves_line_boundary(self) -> None:
        """Long assistant messages should truncate at a newline, not mid-line."""
        lines = [f"Line {i}: " + "x" * 40 for i in range(20)]
        content = "\n".join(lines)  # ~1000 chars
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": content},
        ]
        with (
            patch("anteroom.cli.renderer.console"),
            patch("anteroom.cli.renderer._stdout_console"),
            patch("anteroom.cli.renderer._make_markdown") as mock_md,
        ):
            render_conversation_recap(messages)
            truncated_text = mock_md.call_args[0][0]
            assert truncated_text.endswith("\n\n...")
            # Text before the trailing "..." should end at a complete line
            body = truncated_text.removesuffix("\n\n...")
            assert body == body.rstrip()  # no trailing partial content
            assert len(truncated_text) < len(content)

    def test_truncation_no_suitable_newline_fallback(self) -> None:
        """When no newline > pos 100 exists, truncation falls back to plain cut."""
        # One long line with no newlines — the else branch
        content = "x" * 600
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": content},
        ]
        with (
            patch("anteroom.cli.renderer.console"),
            patch("anteroom.cli.renderer._stdout_console"),
            patch("anteroom.cli.renderer._make_markdown") as mock_md,
        ):
            render_conversation_recap(messages)
            truncated_text = mock_md.call_args[0][0]
            assert truncated_text.endswith("\n\n...")
            assert len(truncated_text) < 600

    def test_exactly_500_chars_not_truncated(self) -> None:
        """A message of exactly 500 chars should not be truncated."""
        content = "y" * 500
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": content},
        ]
        with (
            patch("anteroom.cli.renderer.console"),
            patch("anteroom.cli.renderer._stdout_console"),
            patch("anteroom.cli.renderer._make_markdown") as mock_md,
        ):
            render_conversation_recap(messages)
            truncated_text = mock_md.call_args[0][0]
            assert truncated_text == content

    def test_short_assistant_not_truncated(self) -> None:
        """Short assistant messages should be passed through intact."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Short reply."},
        ]
        with (
            patch("anteroom.cli.renderer.console"),
            patch("anteroom.cli.renderer._stdout_console"),
            patch("anteroom.cli.renderer._make_markdown") as mock_md,
        ):
            render_conversation_recap(messages)
            truncated_text = mock_md.call_args[0][0]
            assert truncated_text == "Short reply."

    def test_skips_tool_messages(self) -> None:
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "tool", "content": "tool output"},
            {"role": "assistant", "content": "done"},
        ]
        with (
            patch("anteroom.cli.renderer.console") as mock_console,
            patch("anteroom.cli.renderer._stdout_console"),
            patch("anteroom.cli.renderer._make_markdown") as mock_md,
        ):
            render_conversation_recap(messages)
            console_output = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "do something" in console_output
            assert "tool output" not in console_output
            mock_md.assert_called_once_with("done")


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
            assert "Esc" in output
