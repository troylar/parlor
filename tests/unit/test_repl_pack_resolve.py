"""Tests for REPL helpers: _resolve_pack_interactive, _timed_input, and subcommand completions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anteroom.cli.repl import _resolve_pack_interactive, _timed_input


@pytest.mark.asyncio
async def test_resolve_pack_interactive_unique_match() -> None:
    """Returns the pack dict directly when resolve_pack finds a unique match."""
    pack_dict = {"id": "abc123", "name": "my-pack", "namespace": "ns"}
    db = MagicMock()
    with patch("anteroom.cli.repl.packs_service.resolve_pack", return_value=(pack_dict, [])):
        result = await _resolve_pack_interactive(db, "ns", "my-pack")
    assert result == pack_dict


@pytest.mark.asyncio
async def test_resolve_pack_interactive_not_found() -> None:
    """Returns None when no packs match."""
    db = MagicMock()
    with patch("anteroom.cli.repl.packs_service.resolve_pack", return_value=(None, [])):
        result = await _resolve_pack_interactive(db, "ns", "missing")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_pack_interactive_ambiguous_inline() -> None:
    """Prompts user for disambiguation in inline mode."""
    candidates = [
        {"id": "aaa11111", "namespace": "ns", "name": "p", "version": "1.0"},
        {"id": "bbb22222", "namespace": "ns", "name": "p", "version": "2.0"},
    ]
    db = MagicMock()
    with (
        patch("anteroom.cli.repl.packs_service.resolve_pack", return_value=(None, candidates)),
        patch("anteroom.cli.repl.renderer"),
        patch("anteroom.cli.repl._timed_input", new_callable=AsyncMock, return_value="2"),
    ):
        result = await _resolve_pack_interactive(db, "ns", "p")
    assert result == candidates[1]


# ---------------------------------------------------------------------------
# _timed_input tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_input_returns_user_input() -> None:
    """Returns the string from input() when it completes in time."""
    with patch("anteroom.cli.repl.input", return_value="hello"):
        result = await _timed_input("prompt: ", timeout=5.0)
    assert result == "hello"


@pytest.mark.asyncio
async def test_timed_input_raises_on_timeout() -> None:
    """Raises TimeoutError when input doesn't complete in time."""

    async def slow_input(*_args: object) -> str:
        await asyncio.sleep(10)
        return "too late"

    with patch("anteroom.cli.repl.asyncio.to_thread", side_effect=slow_input):
        with pytest.raises(TimeoutError):
            await _timed_input("prompt: ", timeout=0.05)
