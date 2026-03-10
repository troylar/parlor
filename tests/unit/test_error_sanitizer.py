"""Tests for services.error_sanitizer."""

from anteroom.services.error_sanitizer import sanitize_provider_error


class TestSanitizeProviderError:
    def test_plain_text_passes_through(self) -> None:
        assert sanitize_provider_error("The model was unable to complete inference") == (
            "The model was unable to complete inference"
        )

    def test_empty_string_returns_fallback(self) -> None:
        assert sanitize_provider_error("") == "AI request error"

    def test_whitespace_only_returns_fallback(self) -> None:
        assert sanitize_provider_error("   \n\t  ") == "AI request error"

    def test_urls_are_stripped(self) -> None:
        result = sanitize_provider_error("Error at https://api.openai.com/v1/chat - bad request")
        assert "https://" not in result
        assert "bad request" in result

    def test_api_keys_are_redacted(self) -> None:
        result = sanitize_provider_error("Invalid key sk-abc123defghijklmn in request")
        assert "sk-abc123" not in result
        assert "[REDACTED]" in result

    def test_bearer_tokens_are_redacted(self) -> None:
        result = sanitize_provider_error("Auth failed: Bearer eyJhbGciOiJIUzI1NiJ9token")
        assert "eyJhbGci" not in result
        assert "[REDACTED]" in result

    def test_raw_json_returns_fallback(self) -> None:
        assert sanitize_provider_error('{"error": {"code": 400}}') == "AI request error"

    def test_raw_json_array_returns_fallback(self) -> None:
        assert sanitize_provider_error('[{"error": "bad"}]') == "AI request error"

    def test_raw_html_returns_fallback(self) -> None:
        assert sanitize_provider_error("<html><body>Error</body></html>") == "AI request error"

    def test_angle_bracket_prefix_returns_fallback(self) -> None:
        assert sanitize_provider_error("<Error>Something went wrong</Error>") == "AI request error"
        assert sanitize_provider_error("<error>details</error>") == "AI request error"

    def test_truncation_at_max_length(self) -> None:
        long_msg = "A" * 201
        result = sanitize_provider_error(long_msg)
        assert len(result) == 203  # 200 + "..."
        assert result.endswith("...")

    def test_exactly_max_length_not_truncated(self) -> None:
        msg = "A" * 200
        assert sanitize_provider_error(msg) == msg

    def test_combined_url_and_key_stripped(self) -> None:
        raw = "Error calling https://api.example.com with key sk-testkey12345678 failed"
        result = sanitize_provider_error(raw)
        assert "https://" not in result
        assert "sk-testkey" not in result
        assert "Error calling" in result

    def test_custom_fallback(self) -> None:
        assert sanitize_provider_error("", fallback="custom fallback") == "custom fallback"

    def test_stripping_leaves_nothing_returns_fallback(self) -> None:
        result = sanitize_provider_error("https://api.example.com/error")
        assert result == "AI request error"

    def test_whitespace_collapsed_after_stripping(self) -> None:
        result = sanitize_provider_error("Error  at   https://example.com/foo  occurred")
        assert "  " not in result
