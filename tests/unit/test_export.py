"""Tests for services/export.py (#689)."""

from __future__ import annotations

from anteroom.services.export import export_conversation_markdown


class TestExportConversationMarkdown:
    def _conv(self, **overrides: object) -> dict:
        base = {"title": "Test Chat", "created_at": "2026-01-01T00:00:00"}
        base.update(overrides)
        return base

    def test_basic_user_and_assistant(self) -> None:
        md = export_conversation_markdown(
            self._conv(),
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
        )
        assert "# Test Chat" in md
        assert "## User" in md
        assert "Hello" in md
        assert "## Assistant" in md
        assert "Hi there" in md

    def test_empty_messages(self) -> None:
        md = export_conversation_markdown(self._conv(), [])
        assert "# Test Chat" in md
        assert "## User" not in md

    def test_attachments_rendered(self) -> None:
        md = export_conversation_markdown(
            self._conv(),
            [
                {
                    "role": "user",
                    "content": "See file",
                    "attachments": [{"filename": "doc.pdf", "mime_type": "application/pdf", "size_bytes": 1234}],
                },
            ],
        )
        assert "**Attachments:**" in md
        assert "doc.pdf" in md
        assert "1234 bytes" in md

    def test_tool_calls_rendered(self) -> None:
        md = export_conversation_markdown(
            self._conv(),
            [
                {
                    "role": "assistant",
                    "content": "Running tool",
                    "tool_calls": [
                        {
                            "tool_name": "bash",
                            "status": "success",
                            "input": {"command": "ls"},
                            "output": {"result": "file.txt"},
                        }
                    ],
                },
            ],
        )
        assert "**Tool Call: bash**" in md
        assert "success" in md
        assert '"command"' in md
        assert "Output:" in md

    def test_tool_call_without_output(self) -> None:
        md = export_conversation_markdown(
            self._conv(),
            [
                {
                    "role": "assistant",
                    "content": "Running",
                    "tool_calls": [
                        {"tool_name": "read_file", "status": "error", "input": {"path": "/tmp/x"}, "output": None}
                    ],
                },
            ],
        )
        assert "Output:" not in md

    def test_metadata_in_header(self) -> None:
        md = export_conversation_markdown(self._conv(created_at="2026-03-01"), [])
        assert "2026-03-01" in md
        assert "Exported from AI Chat" in md
