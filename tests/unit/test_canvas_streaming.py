"""Tests for canvas streaming content and language extractors."""

from unittest.mock import MagicMock

from anteroom.routers.chat import _canvas_needs_approval, _extract_streaming_content, _extract_streaming_language


class TestExtractStreamingContent:
    def test_returns_none_before_content_key(self) -> None:
        assert _extract_streaming_content('{"title": "My Doc"') is None

    def test_returns_none_for_empty_string(self) -> None:
        assert _extract_streaming_content("") is None

    def test_returns_empty_when_content_value_starts(self) -> None:
        assert _extract_streaming_content('{"content": "') == ""

    def test_returns_partial_content(self) -> None:
        assert _extract_streaming_content('{"content": "Hello wo') == "Hello wo"

    def test_returns_complete_content(self) -> None:
        assert _extract_streaming_content('{"content": "Hello world"}') == "Hello world"

    def test_handles_newline_escape(self) -> None:
        assert _extract_streaming_content('{"content": "line1\\nline2') == "line1\nline2"

    def test_handles_tab_escape(self) -> None:
        assert _extract_streaming_content('{"content": "col1\\tcol2') == "col1\tcol2"

    def test_handles_quote_escape(self) -> None:
        assert _extract_streaming_content('{"content": "say \\"hello\\""}') == 'say "hello"'

    def test_handles_backslash_escape(self) -> None:
        assert _extract_streaming_content('{"content": "path\\\\dir') == "path\\dir"

    def test_handles_unicode_escape(self) -> None:
        assert _extract_streaming_content('{"content": "\\u0048i"}') == "Hi"

    def test_content_after_other_keys(self) -> None:
        result = _extract_streaming_content('{"title": "Doc", "content": "body text')
        assert result == "body text"

    def test_whitespace_around_colon(self) -> None:
        assert _extract_streaming_content('{"content" : "spaced') == "spaced"

    def test_returns_none_when_colon_missing(self) -> None:
        assert _extract_streaming_content('{"content" "broken') is None

    def test_returns_none_when_quote_not_started(self) -> None:
        assert _extract_streaming_content('{"content": ') is None

    def test_incremental_accumulation(self) -> None:
        chunks = ['{"titl', 'e": "T", ', '"content', '": "He', "llo", " world"]
        accumulated = ""
        last_content = None
        for chunk in chunks:
            accumulated += chunk
            result = _extract_streaming_content(accumulated)
            if result is not None:
                assert len(result) >= (len(last_content) if last_content else 0)
                last_content = result
        assert last_content == "Hello world"

    def test_slash_escape(self) -> None:
        assert _extract_streaming_content('{"content": "a\\/b"}') == "a/b"

    def test_incomplete_unicode_escape(self) -> None:
        result = _extract_streaming_content('{"content": "\\u00')
        assert result == ""

    def test_handles_carriage_return_escape(self) -> None:
        assert _extract_streaming_content('{"content": "line1\\rline2"}') == "line1\rline2"

    def test_handles_mixed_escapes(self) -> None:
        result = _extract_streaming_content('{"content": "a\\nb\\tc\\"d"}')
        assert result == 'a\nb\tc"d'

    def test_content_key_in_nested_object(self) -> None:
        """Test finds first occurrence of 'content' key (even if nested).

        Note: In practice, canvas tool arguments have flat structure, so this edge case
        shouldn't occur. This test documents current behavior of finding the first match.
        """
        result = _extract_streaming_content('{"meta": {"content": "wrong"}, "content": "right"}')
        # Current behavior: finds first "content" key (the nested one)
        assert result == "wrong"


class TestExtractStreamingLanguage:
    def test_returns_none_before_language_key(self) -> None:
        assert _extract_streaming_language('{"title": "My Doc"') is None

    def test_returns_none_for_empty_string(self) -> None:
        assert _extract_streaming_language("") is None

    def test_extracts_language(self) -> None:
        assert _extract_streaming_language('{"language": "python", "content": "x') == "python"

    def test_extracts_language_before_content(self) -> None:
        args = '{"title": "Test", "language": "javascript", "content": "code'
        assert _extract_streaming_language(args) == "javascript"

    def test_returns_none_for_empty_language_value(self) -> None:
        assert _extract_streaming_language('{"language": ""') is None

    def test_handles_whitespace_around_colon(self) -> None:
        assert _extract_streaming_language('{"language" : "rust"') == "rust"

    def test_rejects_backtick_injection(self) -> None:
        assert _extract_streaming_language('{"language": "python`\\n<script>"') is None

    def test_rejects_space_injection(self) -> None:
        assert _extract_streaming_language('{"language": "py thon"') is None

    def test_allows_csharp(self) -> None:
        assert _extract_streaming_language('{"language": "c#"') == "c#"

    def test_allows_cpp(self) -> None:
        assert _extract_streaming_language('{"language": "c++"') == "c++"

    def test_rejects_too_long(self) -> None:
        assert _extract_streaming_language('{"language": "' + "a" * 51) is None


class TestCanvasNeedsApproval:
    def test_returns_true_when_no_safety_config(self) -> None:
        registry = MagicMock()
        assert _canvas_needs_approval(None, registry) is True

    def test_returns_false_in_auto_mode(self) -> None:
        from anteroom.tools.tiers import ApprovalMode

        safety = MagicMock()
        safety.approval_mode = ApprovalMode.AUTO
        safety.tool_tiers = None
        safety.allowed_tools = None
        registry = MagicMock()
        registry._session_allowed = set()
        assert _canvas_needs_approval(safety, registry) is False

    def test_returns_false_in_ask_for_writes_mode(self) -> None:
        from anteroom.tools.tiers import ApprovalMode

        safety = MagicMock()
        safety.approval_mode = ApprovalMode.ASK_FOR_WRITES
        safety.tool_tiers = None
        safety.allowed_tools = None
        registry = MagicMock()
        registry._session_allowed = set()
        assert _canvas_needs_approval(safety, registry) is False

    def test_returns_false_when_session_allowed(self) -> None:
        from anteroom.tools.tiers import ApprovalMode

        safety = MagicMock()
        safety.approval_mode = ApprovalMode.ASK_FOR_WRITES
        safety.tool_tiers = None
        safety.allowed_tools = None
        registry = MagicMock()
        registry._session_allowed = {"create_canvas"}
        assert _canvas_needs_approval(safety, registry) is False

    def test_returns_false_when_in_allowed_tools(self) -> None:
        from anteroom.tools.tiers import ApprovalMode

        safety = MagicMock()
        safety.approval_mode = ApprovalMode.ASK_FOR_WRITES
        safety.tool_tiers = None
        safety.allowed_tools = ["create_canvas"]
        registry = MagicMock()
        registry._session_allowed = set()
        assert _canvas_needs_approval(safety, registry) is False

    def test_handles_string_approval_mode(self) -> None:
        safety = MagicMock()
        safety.approval_mode = "ask_for_writes"
        safety.tool_tiers = None
        safety.allowed_tools = None
        registry = MagicMock()
        registry._session_allowed = set()
        assert _canvas_needs_approval(safety, registry) is False

    def test_handles_string_auto_mode(self) -> None:
        safety = MagicMock()
        safety.approval_mode = "auto"
        safety.tool_tiers = None
        safety.allowed_tools = None
        registry = MagicMock()
        registry._session_allowed = set()
        assert _canvas_needs_approval(safety, registry) is False
