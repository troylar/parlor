"""Tests for ANTEROOM.md trust store and trust prompts (#219)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from anteroom.services.trust import (
    check_trust,
    compute_content_hash,
    load_trust_store,
    save_trust_decision,
)


class TestComputeContentHash:
    def test_deterministic(self):
        assert compute_content_hash("hello") == compute_content_hash("hello")

    def test_different_content_different_hash(self):
        assert compute_content_hash("hello") != compute_content_hash("world")

    def test_returns_hex_string(self):
        h = compute_content_hash("test")
        assert len(h) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in h)


class TestTrustStore:
    def test_load_empty_when_no_file(self, tmp_path: Path):
        decisions = load_trust_store(tmp_path)
        assert decisions == []

    def test_save_and_load(self, tmp_path: Path):
        save_trust_decision("/some/path", "abc123", data_dir=tmp_path)
        decisions = load_trust_store(tmp_path)
        assert len(decisions) == 1
        assert decisions[0].content_hash == "abc123"
        assert decisions[0].recursive is False

    def test_save_recursive(self, tmp_path: Path):
        save_trust_decision("/some/path", "abc123", recursive=True, data_dir=tmp_path)
        decisions = load_trust_store(tmp_path)
        assert len(decisions) == 1
        assert decisions[0].recursive is True

    def test_update_existing_entry(self, tmp_path: Path):
        save_trust_decision("/some/path", "hash1", data_dir=tmp_path)
        save_trust_decision("/some/path", "hash2", data_dir=tmp_path)
        decisions = load_trust_store(tmp_path)
        assert len(decisions) == 1
        assert decisions[0].content_hash == "hash2"

    def test_multiple_entries(self, tmp_path: Path):
        save_trust_decision("/path/a", "hash_a", data_dir=tmp_path)
        save_trust_decision("/path/b", "hash_b", data_dir=tmp_path)
        decisions = load_trust_store(tmp_path)
        assert len(decisions) == 2

    def test_file_permissions(self, tmp_path: Path):
        save_trust_decision("/some/path", "abc", data_dir=tmp_path)
        trust_file = tmp_path / "trusted_folders.json"
        assert trust_file.exists()
        mode = trust_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_corrupt_json_returns_empty(self, tmp_path: Path):
        trust_file = tmp_path / "trusted_folders.json"
        trust_file.write_text("not valid json{{{")
        decisions = load_trust_store(tmp_path)
        assert decisions == []

    def test_missing_path_field_skipped(self, tmp_path: Path):
        trust_file = tmp_path / "trusted_folders.json"
        trust_file.write_text(json.dumps({"trusted": [{"no_path": True}]}))
        decisions = load_trust_store(tmp_path)
        assert decisions == []


class TestCheckTrust:
    def test_untrusted_when_no_record(self, tmp_path: Path):
        assert check_trust("/some/project", "hash1", data_dir=tmp_path) == "untrusted"

    def test_trusted_when_hash_matches(self, tmp_path: Path):
        save_trust_decision("/some/project", "hash1", data_dir=tmp_path)
        assert check_trust("/some/project", "hash1", data_dir=tmp_path) == "trusted"

    def test_changed_when_hash_differs(self, tmp_path: Path):
        save_trust_decision("/some/project", "hash1", data_dir=tmp_path)
        assert check_trust("/some/project", "hash2", data_dir=tmp_path) == "changed"

    def test_recursive_trust_covers_subdirectory(self, tmp_path: Path):
        parent = tmp_path / "parent"
        parent.mkdir()
        child = parent / "child"
        child.mkdir()
        save_trust_decision(str(parent), "hash1", recursive=True, data_dir=tmp_path)
        # Recursive trust covers children regardless of content hash
        assert check_trust(str(child), "hash1", data_dir=tmp_path) == "trusted"

    def test_recursive_trust_covers_child_any_hash(self, tmp_path: Path):
        parent = tmp_path / "parent"
        parent.mkdir()
        child = parent / "child"
        child.mkdir()
        save_trust_decision(str(parent), "hash1", recursive=True, data_dir=tmp_path)
        # Different hash still trusted under recursive parent
        assert check_trust(str(child), "different_hash", data_dir=tmp_path) == "trusted"

    def test_non_recursive_does_not_cover_child(self, tmp_path: Path):
        parent = tmp_path / "parent"
        parent.mkdir()
        child = parent / "child"
        child.mkdir()
        save_trust_decision(str(parent), "hash1", recursive=False, data_dir=tmp_path)
        assert check_trust(str(child), "hash1", data_dir=tmp_path) == "untrusted"


class TestCheckProjectTrust:
    """Tests for the async _check_project_trust function in repl.py."""

    @pytest.mark.asyncio
    async def test_trust_project_flag_auto_trusts(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        md = tmp_path / "ANTEROOM.md"
        md.write_text("instructions")
        result = await _check_project_trust(md, "instructions", trust_project=True, data_dir=tmp_path)
        assert result == "instructions"
        # Should be persisted
        assert check_trust(str(tmp_path), compute_content_hash("instructions"), data_dir=tmp_path) == "trusted"

    @pytest.mark.asyncio
    async def test_trust_project_flag_updates_existing_hash(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        save_trust_decision(str(tmp_path), "old_hash", data_dir=tmp_path)
        md = tmp_path / "ANTEROOM.md"
        md.write_text("new instructions")
        result = await _check_project_trust(md, "new instructions", trust_project=True, data_dir=tmp_path)
        assert result == "new instructions"
        assert check_trust(str(tmp_path), compute_content_hash("new instructions"), data_dir=tmp_path) == "trusted"

    @pytest.mark.asyncio
    async def test_already_trusted_returns_content(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        content = "trusted instructions"
        md = tmp_path / "ANTEROOM.md"
        md.write_text(content)
        save_trust_decision(str(tmp_path), compute_content_hash(content), data_dir=tmp_path)

        result = await _check_project_trust(md, content, data_dir=tmp_path)
        assert result == content

    @pytest.mark.asyncio
    async def test_changed_content_prompts_user(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        save_trust_decision(str(tmp_path), "old_hash", data_dir=tmp_path)
        new_content = "updated instructions"
        md = tmp_path / "ANTEROOM.md"
        md.write_text(new_content)

        mock_session = AsyncMock()
        mock_session.prompt_async = AsyncMock(return_value="y")

        with (
            patch("anteroom.cli.repl.renderer"),
            patch("prompt_toolkit.PromptSession", return_value=mock_session),
        ):
            result = await _check_project_trust(md, new_content, data_dir=tmp_path)
        assert result == new_content
        # Trust store should be updated with new hash
        assert check_trust(str(tmp_path), compute_content_hash(new_content), data_dir=tmp_path) == "trusted"

    @pytest.mark.asyncio
    async def test_changed_content_user_denies(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        save_trust_decision(str(tmp_path), "old_hash", data_dir=tmp_path)
        new_content = "suspicious update"
        md = tmp_path / "ANTEROOM.md"
        md.write_text(new_content)

        mock_session = AsyncMock()
        mock_session.prompt_async = AsyncMock(return_value="n")

        with (
            patch("anteroom.cli.repl.renderer"),
            patch("prompt_toolkit.PromptSession", return_value=mock_session),
        ):
            result = await _check_project_trust(md, new_content, data_dir=tmp_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_untrusted_user_approves(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        content = "new instructions"
        md = tmp_path / "ANTEROOM.md"
        md.write_text(content)

        mock_session = AsyncMock()
        mock_session.prompt_async = AsyncMock(return_value="y")

        with (
            patch("anteroom.cli.repl.renderer"),
            patch("prompt_toolkit.PromptSession", return_value=mock_session),
        ):
            result = await _check_project_trust(md, content, data_dir=tmp_path)

        assert result == content
        assert check_trust(str(tmp_path), compute_content_hash(content), data_dir=tmp_path) == "trusted"

    @pytest.mark.asyncio
    async def test_untrusted_user_denies(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        content = "malicious instructions"
        md = tmp_path / "ANTEROOM.md"
        md.write_text(content)

        mock_session = AsyncMock()
        mock_session.prompt_async = AsyncMock(return_value="n")

        with (
            patch("anteroom.cli.repl.renderer"),
            patch("prompt_toolkit.PromptSession", return_value=mock_session),
        ):
            result = await _check_project_trust(md, content, data_dir=tmp_path)

        assert result is None
        assert check_trust(str(tmp_path), compute_content_hash(content), data_dir=tmp_path) == "untrusted"

    @pytest.mark.asyncio
    async def test_untrusted_eof_fails_closed(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        content = "instructions"
        md = tmp_path / "ANTEROOM.md"
        md.write_text(content)

        mock_session = AsyncMock()
        mock_session.prompt_async = AsyncMock(side_effect=EOFError)

        with (
            patch("anteroom.cli.repl.renderer"),
            patch("prompt_toolkit.PromptSession", return_value=mock_session),
        ):
            result = await _check_project_trust(md, content, data_dir=tmp_path)

        assert result is None

    @pytest.mark.asyncio
    async def test_untrusted_keyboard_interrupt_fails_closed(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        content = "instructions"
        md = tmp_path / "ANTEROOM.md"
        md.write_text(content)

        mock_session = AsyncMock()
        mock_session.prompt_async = AsyncMock(side_effect=KeyboardInterrupt)

        with (
            patch("anteroom.cli.repl.renderer"),
            patch("prompt_toolkit.PromptSession", return_value=mock_session),
        ):
            result = await _check_project_trust(md, content, data_dir=tmp_path)

        assert result is None

    @pytest.mark.asyncio
    async def test_recursive_trust_option(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        project_dir = tmp_path / "projects" / "my-project"
        project_dir.mkdir(parents=True)
        content = "instructions"
        md = project_dir / "ANTEROOM.md"
        md.write_text(content)

        mock_session = AsyncMock()
        mock_session.prompt_async = AsyncMock(return_value="r")

        with (
            patch("anteroom.cli.repl.renderer"),
            patch("prompt_toolkit.PromptSession", return_value=mock_session),
        ):
            result = await _check_project_trust(md, content, data_dir=tmp_path)

        assert result == content
        # Parent should be trusted recursively
        decisions = load_trust_store(tmp_path)
        recursive_decisions = [d for d in decisions if d.recursive]
        assert len(recursive_decisions) == 1

    @pytest.mark.asyncio
    async def test_view_then_approve(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        content = "view me first"
        md = tmp_path / "ANTEROOM.md"
        md.write_text(content)

        mock_session = AsyncMock()
        mock_session.prompt_async = AsyncMock(side_effect=["v", "y"])

        with (
            patch("anteroom.cli.repl.renderer"),
            patch("prompt_toolkit.PromptSession", return_value=mock_session),
        ):
            result = await _check_project_trust(md, content, data_dir=tmp_path)

        assert result == content

    @pytest.mark.asyncio
    async def test_empty_anteroom_md_trusted(self, tmp_path: Path):
        from anteroom.cli.repl import _check_project_trust

        md = tmp_path / "ANTEROOM.md"
        md.write_text("")
        result = await _check_project_trust(md, "", trust_project=True, data_dir=tmp_path)
        assert result == ""


class TestLoadInstructionsWithTrust:
    @pytest.mark.asyncio
    async def test_global_instructions_always_loaded(self, tmp_path: Path):
        from anteroom.cli.repl import _load_instructions_with_trust

        with patch("anteroom.cli.repl.find_global_instructions", return_value="global rules"):
            with patch("anteroom.cli.repl.find_project_instructions_path", return_value=None):
                result = await _load_instructions_with_trust(str(tmp_path), data_dir=tmp_path)

        assert result is not None
        assert "global rules" in result

    @pytest.mark.asyncio
    async def test_no_project_context_skips_project(self, tmp_path: Path):
        from anteroom.cli.repl import _load_instructions_with_trust

        project_md = tmp_path / "ANTEROOM.md"
        project_md.write_text("project rules")

        with patch("anteroom.cli.repl.find_global_instructions", return_value=None):
            with patch("anteroom.cli.repl.find_project_instructions_path") as mock_find:
                result = await _load_instructions_with_trust(str(tmp_path), no_project_context=True, data_dir=tmp_path)
                mock_find.assert_not_called()

        assert result is None

    @pytest.mark.asyncio
    async def test_global_and_trusted_project_combined(self, tmp_path: Path):
        from anteroom.cli.repl import _load_instructions_with_trust

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        md = project_dir / "ANTEROOM.md"
        md.write_text("project rules")
        save_trust_decision(str(project_dir), compute_content_hash("project rules"), data_dir=tmp_path)

        with patch("anteroom.cli.repl.find_global_instructions", return_value="global rules"):
            with patch(
                "anteroom.cli.repl.find_project_instructions_path",
                return_value=(md, "project rules"),
            ):
                result = await _load_instructions_with_trust(str(project_dir), data_dir=tmp_path)

        assert result is not None
        assert "# Global Instructions\nglobal rules" in result
        assert "# Project Instructions\nproject rules" in result
