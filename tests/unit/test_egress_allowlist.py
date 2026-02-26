"""Tests for egress domain allowlist."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from anteroom.config import AIConfig
from anteroom.services.ai_service import AIService
from anteroom.services.egress_allowlist import check_egress_allowed

# --- Helpers ---


def _make_config(**overrides) -> AIConfig:
    defaults = {
        "base_url": "https://api.openai.com/v1",
        "api_key": "test-key",
        "model": "gpt-4",
    }
    defaults.update(overrides)
    return AIConfig(**defaults)


# --- Pure function tests ---


class TestEmptyAllowlist:
    def test_empty_list_allows_all(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1", []) is True

    def test_empty_list_allows_any_domain(self) -> None:
        assert check_egress_allowed("https://anything.example.com", []) is True

    def test_empty_list_allows_localhost(self) -> None:
        assert check_egress_allowed("http://localhost:11434/v1", []) is True


class TestExactDomainMatch:
    def test_exact_match(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1", ["api.openai.com"]) is True

    def test_exact_match_no_path(self) -> None:
        assert check_egress_allowed("https://api.openai.com", ["api.openai.com"]) is True

    def test_no_match(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1", ["api.anthropic.com"]) is False

    def test_subdomain_not_matched(self) -> None:
        assert check_egress_allowed("https://sub.api.openai.com", ["api.openai.com"]) is False

    def test_parent_not_matched(self) -> None:
        assert check_egress_allowed("https://openai.com", ["api.openai.com"]) is False

    def test_case_insensitive(self) -> None:
        assert check_egress_allowed("https://API.OPENAI.COM/v1", ["api.openai.com"]) is True

    def test_case_insensitive_allowlist(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1", ["API.OPENAI.COM"]) is True

    def test_multiple_entries_match_first(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1", ["api.openai.com", "api.anthropic.com"]) is True

    def test_multiple_entries_match_second(self) -> None:
        assert check_egress_allowed("https://api.anthropic.com/v1", ["api.openai.com", "api.anthropic.com"]) is True

    def test_multiple_entries_no_match(self) -> None:
        assert check_egress_allowed("https://api.mistral.ai/v1", ["api.openai.com", "api.anthropic.com"]) is False


class TestUrlParsing:
    def test_url_with_path(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1/chat/completions", ["api.openai.com"]) is True

    def test_url_with_port(self) -> None:
        assert check_egress_allowed("https://api.openai.com:443/v1", ["api.openai.com"]) is True

    def test_url_with_custom_port(self) -> None:
        assert check_egress_allowed("http://my-proxy.internal:8080/v1", ["my-proxy.internal"]) is True

    def test_url_with_query_params(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1?key=val", ["api.openai.com"]) is True

    def test_url_with_userinfo(self) -> None:
        assert check_egress_allowed("https://user:pass@api.openai.com/v1", ["api.openai.com"]) is True


class TestInternalAddressBlocking:
    """Tests for block_localhost which blocks loopback, private, link-local, etc."""

    def test_localhost_allowed_by_default(self) -> None:
        assert check_egress_allowed("http://localhost:11434/v1", [], block_localhost=False) is True

    def test_localhost_blocked_when_enabled(self) -> None:
        assert check_egress_allowed("http://localhost:11434/v1", [], block_localhost=True) is False

    def test_127_0_0_1_blocked(self) -> None:
        assert check_egress_allowed("http://127.0.0.1:11434/v1", [], block_localhost=True) is False

    def test_ipv6_loopback_blocked(self) -> None:
        assert check_egress_allowed("http://[::1]:11434/v1", [], block_localhost=True) is False

    def test_localhost_localdomain_blocked(self) -> None:
        assert check_egress_allowed("http://localhost.localdomain:8080/v1", [], block_localhost=True) is False

    def test_external_domain_not_blocked(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1", [], block_localhost=True) is True

    def test_localhost_in_allowlist_still_blocked(self) -> None:
        assert check_egress_allowed("http://localhost:11434/v1", ["localhost"], block_localhost=True) is False

    def test_127_in_allowlist_still_blocked(self) -> None:
        assert check_egress_allowed("http://127.0.0.1:11434/v1", ["127.0.0.1"], block_localhost=True) is False

    # RFC-1918 private addresses
    def test_10_x_blocked(self) -> None:
        assert check_egress_allowed("http://10.0.0.1:8080/v1", [], block_localhost=True) is False

    def test_172_16_blocked(self) -> None:
        assert check_egress_allowed("http://172.16.0.1:8080/v1", [], block_localhost=True) is False

    def test_192_168_blocked(self) -> None:
        assert check_egress_allowed("http://192.168.1.1:8080/v1", [], block_localhost=True) is False

    # Link-local (cloud IMDS)
    def test_link_local_169_254_blocked(self) -> None:
        """169.254.169.254 is the cloud IMDS endpoint — must be blocked."""
        assert check_egress_allowed("http://169.254.169.254/latest/meta-data/", [], block_localhost=True) is False

    # Non-IP hostname passes through (not detectable as internal without DNS)
    def test_non_ip_hostname_not_blocked(self) -> None:
        assert check_egress_allowed("http://my-lan-host:8080/v1", [], block_localhost=True) is True


class TestInvalidInput:
    def test_empty_url(self) -> None:
        assert check_egress_allowed("", ["api.openai.com"]) is False

    def test_whitespace_url(self) -> None:
        assert check_egress_allowed("   ", ["api.openai.com"]) is False

    def test_no_hostname(self) -> None:
        assert check_egress_allowed("file:///etc/passwd", ["api.openai.com"]) is False

    def test_invalid_allowlist_entry_skipped(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1", ["", "api.openai.com"]) is True

    def test_none_allowlist_entry_skipped(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1", [None, "api.openai.com"]) is True  # type: ignore[list-item]

    def test_all_invalid_entries_denies(self) -> None:
        assert check_egress_allowed("https://api.openai.com/v1", ["", None, ""]) is False  # type: ignore[list-item]

    def test_bare_hostname_url(self) -> None:
        result = check_egress_allowed("api.openai.com", ["api.openai.com"])
        assert result is False  # no scheme = no hostname parsed


class TestLocalhostVariants:
    def test_127_0_0_1(self) -> None:
        assert check_egress_allowed("http://127.0.0.1:11434", ["127.0.0.1"]) is True

    def test_localhost_name(self) -> None:
        assert check_egress_allowed("http://localhost:11434", ["localhost"]) is True

    def test_ipv4_with_allowlist(self) -> None:
        assert check_egress_allowed("http://192.168.1.100:8080", ["192.168.1.100"]) is True

    def test_ipv4_not_in_allowlist(self) -> None:
        assert check_egress_allowed("http://192.168.1.100:8080", ["10.0.0.1"]) is False


# --- AIService integration tests ---


class TestAIServiceEgressValidation:
    def test_allowed_domain_constructs_normally(self) -> None:
        config = _make_config(allowed_domains=["api.openai.com"])
        with patch("anteroom.services.ai_service.AsyncOpenAI"):
            service = AIService(config)
        assert service.config.base_url == "https://api.openai.com/v1"

    def test_blocked_domain_raises_valueerror(self) -> None:
        config = _make_config(allowed_domains=["api.anthropic.com"])
        with pytest.raises(ValueError, match="Egress blocked"):
            with patch("anteroom.services.ai_service.AsyncOpenAI"):
                AIService(config)

    def test_empty_allowlist_allows_any(self) -> None:
        config = _make_config(allowed_domains=[])
        with patch("anteroom.services.ai_service.AsyncOpenAI"):
            service = AIService(config)
        assert service.config is not None

    def test_block_localhost_rejects_loopback(self) -> None:
        config = _make_config(base_url="http://localhost:11434/v1", block_localhost_api=True)
        with pytest.raises(ValueError, match="Egress blocked"):
            with patch("anteroom.services.ai_service.AsyncOpenAI"):
                AIService(config)

    def test_block_localhost_allows_external(self) -> None:
        config = _make_config(base_url="https://api.openai.com/v1", block_localhost_api=True)
        with patch("anteroom.services.ai_service.AsyncOpenAI"):
            service = AIService(config)
        assert service.config.base_url == "https://api.openai.com/v1"

    def test_error_message_does_not_leak_allowlist(self) -> None:
        config = _make_config(allowed_domains=["secret-internal.corp.com"])
        with pytest.raises(ValueError, match="Egress blocked") as exc_info:
            with patch("anteroom.services.ai_service.AsyncOpenAI"):
                AIService(config)
        assert "secret-internal" not in str(exc_info.value)

    def test_block_localhost_rejects_cloud_imds(self) -> None:
        config = _make_config(base_url="http://169.254.169.254/latest/meta-data/", block_localhost_api=True)
        with pytest.raises(ValueError, match="Egress blocked"):
            with patch("anteroom.services.ai_service.AsyncOpenAI"):
                AIService(config)
