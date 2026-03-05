"""Tests for the CLI layout utilities module."""

from __future__ import annotations

from unittest.mock import MagicMock

from anteroom.cli.layout import (
    InputLexer,
    _shorten_path,
    input_line_prefix,
    set_approval_mode,
)

# ---------------------------------------------------------------------------
# _shorten_path
# ---------------------------------------------------------------------------


class TestShortenPath:
    def test_home_replaced(self, monkeypatch):
        monkeypatch.setenv("HOME", "/Users/alice")
        assert _shorten_path("/Users/alice/projects/foo") == "~/projects/foo"

    def test_non_home_unchanged(self, monkeypatch):
        monkeypatch.setenv("HOME", "/Users/alice")
        assert _shorten_path("/opt/data") == "/opt/data"


# ---------------------------------------------------------------------------
# set_approval_mode / input_line_prefix
# ---------------------------------------------------------------------------


class TestInputLinePrefix:
    def test_default_prompt(self):
        set_approval_mode("")
        result = input_line_prefix(0, 0)
        assert result == [("class:prompt", "> ")]

    def test_auto_mode(self):
        set_approval_mode("auto")
        result = input_line_prefix(0, 0)
        assert result == [("class:prompt.auto", "> ")]

    def test_ask_mode(self):
        set_approval_mode("ask")
        result = input_line_prefix(0, 0)
        assert result == [("class:prompt.strict", "> ")]

    def test_continuation_line(self):
        set_approval_mode("auto")
        result = input_line_prefix(1, 0)
        assert result == [("class:prompt.continuation", ". ")]


# ---------------------------------------------------------------------------
# InputLexer
# ---------------------------------------------------------------------------


class TestInputLexer:
    def _make_doc(self, text: str) -> MagicMock:
        doc = MagicMock()
        doc.lines = text.split("\n")
        return doc

    def test_slash_command_highlighted(self):
        lexer = InputLexer()
        doc = self._make_doc("/help some args")
        get_line = lexer.lex_document(doc)
        result = get_line(0)
        assert result == [("class:input.command", "/help"), ("", " some args")]

    def test_plain_text(self):
        lexer = InputLexer()
        doc = self._make_doc("hello world")
        get_line = lexer.lex_document(doc)
        result = get_line(0)
        assert result == [("", "hello world")]

    def test_continuation_no_highlight(self):
        lexer = InputLexer()
        doc = self._make_doc("/cmd\n/not-a-cmd")
        get_line = lexer.lex_document(doc)
        result = get_line(1)
        assert result == [("", "/not-a-cmd")]

    def test_empty_line(self):
        lexer = InputLexer()
        doc = self._make_doc("")
        get_line = lexer.lex_document(doc)
        result = get_line(0)
        assert result == [("", "")]
