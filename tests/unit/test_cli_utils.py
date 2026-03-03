"""Tests for CLI utility functions."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from anteroom.cli.instructions import (
    find_project_instructions,
    load_instructions,
)
from anteroom.cli.repl import (
    _detect_git_branch,
    _estimate_tokens,
    _expand_file_references,
)


class TestExpandFileReferences:
    def test_file_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "hello.txt"
            test_file.write_text("hello world")
            result = _expand_file_references("check @hello.txt please", tmpdir)
            assert "hello world" in result
            assert "<file" in result

    def test_directory_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = Path(tmpdir) / "mydir"
            sub.mkdir()
            (sub / "a.txt").touch()
            (sub / "b.txt").touch()
            result = _expand_file_references("list @mydir/ please", tmpdir)
            assert "a.txt" in result
            assert "b.txt" in result
            assert "<directory" in result

    def test_nonexistent_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _expand_file_references("check @nonexistent.txt", tmpdir)
            assert result == "check @nonexistent.txt"

    def test_no_references(self) -> None:
        result = _expand_file_references("hello world", "/tmp")
        assert result == "hello world"

    def test_quoted_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "my file.txt"
            test_file.write_text("spaced content")
            result = _expand_file_references('@"my file.txt"', tmpdir)
            assert "spaced content" in result

    def test_binary_file_uses_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "deck.pptx"
            test_file.write_bytes(b"PK fake pptx data")
            with patch(
                "anteroom.services.document_extractor.extract_text",
                return_value="Extracted slide content",
            ):
                result = _expand_file_references("check @deck.pptx", tmpdir)
                assert "Extracted slide content" in result
                assert "<file" in result

    def test_binary_file_extraction_returns_none_shows_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "deck.pptx"
            test_file.write_bytes(b"PK fake pptx data")
            with patch(
                "anteroom.services.document_extractor.extract_text",
                return_value=None,
            ):
                result = _expand_file_references("check @deck.pptx", tmpdir)
                assert "Binary file" in result
                assert "deck.pptx" in result

    def test_unextractable_binary_shows_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "archive.zip"
            test_file.write_bytes(b"PK\x03\x04 fake zip")
            result = _expand_file_references("check @archive.zip", tmpdir)
            assert "Binary file" in result
            assert "archive.zip" in result

    def test_xlsx_binary_uses_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "data.xlsx"
            test_file.write_bytes(b"PK fake xlsx data")
            with patch(
                "anteroom.services.document_extractor.extract_text",
                return_value="Sheet1 data",
            ):
                result = _expand_file_references("check @data.xlsx", tmpdir)
                assert "Sheet1 data" in result
                assert "<file" in result

    def test_pdf_binary_uses_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "report.pdf"
            test_file.write_bytes(b"%PDF-1.4 fake pdf")
            with patch(
                "anteroom.services.document_extractor.extract_text",
                return_value="PDF content here",
            ):
                result = _expand_file_references("check @report.pdf", tmpdir)
                assert "PDF content here" in result


class TestEstimateTokens:
    def test_empty_messages(self) -> None:
        assert _estimate_tokens([]) == 0

    def test_simple_message(self) -> None:
        messages = [{"role": "user", "content": "hello world"}]
        tokens = _estimate_tokens(messages)
        assert tokens > 0

    def test_message_with_tool_calls(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "Let me check",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "test.py"}',
                        },
                    }
                ],
            }
        ]
        tokens = _estimate_tokens(messages)
        assert tokens > 4  # More than just overhead

    def test_list_content(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": "world"},
                ],
            }
        ]
        tokens = _estimate_tokens(messages)
        assert tokens > 0

    def test_special_tokens_in_content(self) -> None:
        """Regression test: tiktoken special tokens in content should not crash."""
        messages = [
            {"role": "user", "content": "Review this code with <|endoftext|> token"},
            {"role": "assistant", "content": "Found <|fim_prefix|> and <|fim_suffix|> patterns"},
        ]
        tokens = _estimate_tokens(messages)
        assert tokens > 0

    def test_special_tokens_in_tool_calls(self) -> None:
        """Regression test: special tokens in tool call args should not crash."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "file_with_<|endoftext|>.py"}',
                        }
                    }
                ],
            }
        ]
        tokens = _estimate_tokens(messages)
        assert tokens > 0


class TestDetectGitBranch:
    def test_detect_branch(self) -> None:
        # This test assumes we're in a git repo
        branch = _detect_git_branch()
        # In CI or non-git dir, branch might be None
        if branch is not None:
            assert isinstance(branch, str)
            assert len(branch) > 0


class TestInstructions:
    def test_find_project_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            anteroom_md = Path(tmpdir) / "ANTEROOM.md"
            anteroom_md.write_text("# Project Instructions\nDo things.")
            result = find_project_instructions(tmpdir)
            assert result is not None
            assert "Project Instructions" in result

    def test_find_project_instructions_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_project_instructions(tmpdir)
            assert result is None

    def test_load_instructions_project_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            anteroom_md = Path(tmpdir) / "ANTEROOM.md"
            anteroom_md.write_text("project instructions")
            result = load_instructions(tmpdir)
            assert result is not None
            assert "project instructions" in result
