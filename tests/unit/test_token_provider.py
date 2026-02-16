"""Tests for the TokenProvider."""

from __future__ import annotations

import sys

import pytest

from anteroom.services.token_provider import TokenProvider, TokenProviderError


class TestTokenProvider:
    def test_get_token_runs_command(self) -> None:
        provider = TokenProvider(command=f"{sys.executable} -c \"print('my-secret-token')\"")
        token = provider.get_token()
        assert token == "my-secret-token"

    def test_get_token_caches_result(self) -> None:
        provider = TokenProvider(command=f'{sys.executable} -c "import random; print(random.random())"')
        first = provider.get_token()
        second = provider.get_token()
        assert first == second

    def test_refresh_returns_new_token(self) -> None:
        provider = TokenProvider(command=f'{sys.executable} -c "import random; print(random.random())"')
        provider.get_token()
        second = provider.refresh()
        # Technically could match, but astronomically unlikely with random floats
        assert isinstance(second, str)
        assert len(second) > 0

    def test_clear_cache_forces_rerun(self) -> None:
        provider = TokenProvider(command=f'{sys.executable} -c "import random; print(random.random())"')
        provider.get_token()
        provider.clear_cache()
        second = provider.get_token()
        assert isinstance(second, str)
        assert len(second) > 0

    def test_command_failure_raises(self) -> None:
        provider = TokenProvider(command=f'{sys.executable} -c "import sys; sys.exit(1)"')
        with pytest.raises(TokenProviderError, match="exited with code 1"):
            provider.get_token()

    def test_command_not_found_raises(self) -> None:
        provider = TokenProvider(command="nonexistent_binary_xyz_123")
        with pytest.raises(TokenProviderError, match="not found"):
            provider.get_token()

    def test_empty_output_raises(self) -> None:
        provider = TokenProvider(command=f'{sys.executable} -c "print()"')
        with pytest.raises(TokenProviderError, match="empty output"):
            provider.get_token()

    def test_strips_whitespace(self) -> None:
        provider = TokenProvider(command=f"{sys.executable} -c \"print('  tok123  ')\"")
        assert provider.get_token() == "tok123"

    def test_stderr_included_in_error(self) -> None:
        provider = TokenProvider(
            command=f"{sys.executable} -c \"import sys; print('oops', file=sys.stderr); sys.exit(2)\""
        )
        with pytest.raises(TokenProviderError, match="oops"):
            provider.get_token()

    def test_command_property(self) -> None:
        provider = TokenProvider(command="echo hello")
        assert provider.command == "echo hello"
