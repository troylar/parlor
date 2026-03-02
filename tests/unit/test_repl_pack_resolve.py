"""Tests for REPL helpers: _resolve_pack_interactive and subcommand completions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from anteroom.cli.repl import _resolve_pack_interactive


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
    """Prompts user for disambiguation in inline (non-fullscreen) mode."""
    candidates = [
        {"id": "aaa11111", "namespace": "ns", "name": "p", "version": "1.0"},
        {"id": "bbb22222", "namespace": "ns", "name": "p", "version": "2.0"},
    ]
    db = MagicMock()
    with (
        patch("anteroom.cli.repl.packs_service.resolve_pack", return_value=(None, candidates)),
        patch("anteroom.cli.repl.renderer") as mock_renderer,
        patch("builtins.input", return_value="2"),
    ):
        mock_renderer.is_fullscreen.return_value = False
        result = await _resolve_pack_interactive(db, "ns", "p")
    assert result == candidates[1]
