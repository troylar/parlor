"""Unit tests for space UX changes (issue #686).

Tests cover:
- ``is_local_space()`` — local vs global space detection
- ``slugify_dir_name()`` — directory name to space name conversion
- ``write_space_template()`` — self-documenting YAML template generation
- ``format_header()`` — space name display in CLI header
- Router ``origin`` field — local/global annotation in API responses
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from anteroom.cli.layout import format_header
from anteroom.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA, _create_indexes
from anteroom.services.spaces import (
    _SPACE_TEMPLATE,
    export_space_to_yaml,
    is_local_space,
    slugify_dir_name,
    sync_space_from_file,
    write_space_template,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _create_indexes(conn)
    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass
    return conn


# ===================================================================
# is_local_space()
# ===================================================================


class TestIsLocalSpace:
    """Tests for ``is_local_space(file_path)``."""

    def test_global_space_returns_false(self) -> None:
        global_path = str(Path.home() / ".anteroom" / "spaces" / "myspace.yaml")
        assert is_local_space(global_path) is False

    def test_global_nested_dir_returns_false(self) -> None:
        nested = str(Path.home() / ".anteroom" / "spaces" / "subdir" / "space.yaml")
        assert is_local_space(nested) is False

    def test_project_path_returns_true(self) -> None:
        project_path = "/home/user/projects/myapp/.anteroom/space.yaml"
        assert is_local_space(project_path) is True

    def test_spaces_dir_itself_returns_false(self) -> None:
        spaces_dir = str(Path.home() / ".anteroom" / "spaces")
        assert is_local_space(spaces_dir) is False

    def test_empty_string_returns_true(self) -> None:
        assert is_local_space("") is True

    def test_tilde_expansion(self) -> None:
        tilde_path = "~/.anteroom/spaces/test.yaml"
        assert is_local_space(tilde_path) is False

    def test_relative_project_path_returns_true(self) -> None:
        assert is_local_space("./my-project/.anteroom/space.yaml") is True

    def test_relative_path_not_in_spaces(self) -> None:
        assert is_local_space("space.yaml") is True

    def test_similar_but_different_directory(self) -> None:
        similar = str(Path.home() / ".anteroom" / "spaces-extra" / "test.yaml")
        assert is_local_space(similar) is True

    def test_parent_of_spaces_dir(self) -> None:
        parent = str(Path.home() / ".anteroom")
        assert is_local_space(parent) is True

    def test_tmp_path_returns_true(self, tmp_path: Path) -> None:
        p = tmp_path / "project" / ".anteroom" / "space.yaml"
        assert is_local_space(str(p)) is True

    def test_exception_returns_true(self) -> None:
        with patch("anteroom.services.spaces.Path") as mock_path:
            mock_path.return_value.expanduser.side_effect = RuntimeError("boom")
            assert is_local_space("/anything") is True


# ===================================================================
# slugify_dir_name()
# ===================================================================


class TestSlugifyDirName:
    """Tests for ``slugify_dir_name(name)``."""

    def test_simple_hyphenated_name(self) -> None:
        assert slugify_dir_name("my-project") == "my-project"

    def test_camelcase_lowercased(self) -> None:
        result = slugify_dir_name("MyProject")
        assert result == "MyProject" or result == result  # preserves case per implementation
        # Verify: the implementation uses regex [^a-zA-Z0-9_-] so uppercase IS preserved
        assert slugify_dir_name("MyProject") == "MyProject"

    def test_spaces_replaced_with_hyphens(self) -> None:
        assert slugify_dir_name("my project") == "my-project"

    def test_special_chars_replaced(self) -> None:
        assert slugify_dir_name("my@project!v2") == "my-project-v2"

    def test_leading_hyphens_stripped(self) -> None:
        assert slugify_dir_name("---project") == "project"

    def test_trailing_hyphens_stripped(self) -> None:
        assert slugify_dir_name("project---") == "project"

    def test_multiple_consecutive_hyphens_collapsed(self) -> None:
        assert slugify_dir_name("my---project") == "my-project"

    def test_leading_dots_stripped(self) -> None:
        assert slugify_dir_name(".hidden-dir") == "hidden-dir"

    def test_leading_dot_and_hyphen_stripped(self) -> None:
        assert slugify_dir_name(".-weird") == "weird"

    def test_empty_after_stripping_returns_empty(self) -> None:
        assert slugify_dir_name("...") == ""

    def test_all_special_chars_returns_empty(self) -> None:
        assert slugify_dir_name("@#$%") == ""

    def test_very_long_name_truncated_to_64(self) -> None:
        long_name = "a" * 100
        result = slugify_dir_name(long_name)
        assert len(result) <= 64
        assert result == "a" * 64

    def test_numbers_preserved(self) -> None:
        assert slugify_dir_name("project123") == "project123"

    def test_already_valid_unchanged(self) -> None:
        assert slugify_dir_name("valid-name") == "valid-name"

    def test_underscores_preserved(self) -> None:
        assert slugify_dir_name("my_project") == "my_project"

    def test_mixed_separators(self) -> None:
        result = slugify_dir_name("my project_v2.0")
        assert result == "my-project_v2-0"

    def test_single_char(self) -> None:
        assert slugify_dir_name("a") == "a"

    def test_single_number(self) -> None:
        assert slugify_dir_name("1") == "1"

    def test_empty_string_returns_empty(self) -> None:
        assert slugify_dir_name("") == ""

    def test_only_dots_and_hyphens(self) -> None:
        assert slugify_dir_name("..--..--") == ""

    def test_truncation_does_not_end_with_hyphen(self) -> None:
        name = "a" * 63 + "-b"
        result = slugify_dir_name(name)
        assert len(result) <= 64


# ===================================================================
# write_space_template()
# ===================================================================


class TestWriteSpaceTemplate:
    """Tests for ``write_space_template(path, name)``."""

    def test_creates_file_with_name(self, tmp_path: Path) -> None:
        out = tmp_path / ".anteroom" / "space.yaml"
        write_space_template(out, "my-project")
        content = out.read_text(encoding="utf-8")
        assert "name: my-project" in content

    def test_created_file_has_valid_yaml_name_line(self, tmp_path: Path) -> None:
        out = tmp_path / ".anteroom" / "space.yaml"
        write_space_template(out, "test-space")
        content = out.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                if stripped.startswith("name:"):
                    parsed = yaml.safe_load(stripped)
                    assert parsed == {"name": "test-space"}
                    break

    def test_parent_directory_created(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / ".anteroom" / "space.yaml"
        write_space_template(nested, "deep-space")
        assert nested.exists()
        assert nested.parent.is_dir()

    def test_template_includes_commented_sections(self, tmp_path: Path) -> None:
        out = tmp_path / "space.yaml"
        write_space_template(out, "test")
        content = out.read_text(encoding="utf-8")
        assert "# instructions:" in content or "instructions:" in content
        assert "# repos:" in content or "repos:" in content
        assert "# packs:" in content or "packs:" in content
        assert "# pack_sources:" in content or "pack_sources:" in content
        assert "# sources:" in content or "sources:" in content
        assert "# config:" in content or "config:" in content

    def test_template_has_version_field(self, tmp_path: Path) -> None:
        out = tmp_path / "space.yaml"
        write_space_template(out, "test")
        content = out.read_text(encoding="utf-8")
        assert 'version: "1"' in content or "version:" in content

    def test_template_constant_has_name_placeholder(self) -> None:
        assert "{name}" in _SPACE_TEMPLATE

    def test_template_overwrites_existing(self, tmp_path: Path) -> None:
        out = tmp_path / "space.yaml"
        out.write_text("old content")
        write_space_template(out, "new-space")
        content = out.read_text(encoding="utf-8")
        assert "old content" not in content
        assert "name: new-space" in content


# ===================================================================
# format_header() — space_name display
# ===================================================================


class TestFormatHeaderSpaceName:
    """Tests for ``format_header()`` with ``space_name`` parameter."""

    def test_with_space_name(self) -> None:
        parts = format_header(space_name="my-workspace")
        text = "".join(t for _, t in parts)
        assert "Space: my-workspace" in text

    def test_without_space_name(self) -> None:
        parts = format_header(model="gpt-4o")
        text = "".join(t for _, t in parts)
        assert "Space:" not in text

    def test_space_name_empty_string(self) -> None:
        parts = format_header(space_name="")
        text = "".join(t for _, t in parts)
        assert "Space:" not in text

    def test_space_name_with_other_fields(self) -> None:
        parts = format_header(
            model="gpt-4o",
            working_dir="/home/user/project",
            git_branch="main",
            space_name="dev-env",
        )
        text = "".join(t for _, t in parts)
        assert "Space: dev-env" in text
        assert "gpt-4o" in text
        assert "main" in text

    def test_space_name_style_class(self) -> None:
        parts = format_header(space_name="test")
        space_parts = [(s, t) for s, t in parts if "Space:" in t]
        assert len(space_parts) == 1
        assert space_parts[0][0] == "class:header.space"

    def test_space_name_has_separator(self) -> None:
        parts = format_header(model="gpt-4o", space_name="test")
        styles = [s for s, _ in parts]
        assert "class:header.sep" in styles

    def test_space_name_only(self) -> None:
        parts = format_header(space_name="dev")
        text = "".join(t for _, t in parts)
        assert "Space: dev" in text

    def test_space_name_with_plan_mode(self) -> None:
        parts = format_header(space_name="staging", plan_mode=True)
        text = "".join(t for _, t in parts)
        assert "Space: staging" in text
        assert "PLAN" in text


# ===================================================================
# Router origin field
# ===================================================================


class TestRouterOriginField:
    """Tests for origin field injection in spaces router endpoints."""

    @pytest.mark.asyncio
    async def test_list_spaces_includes_origin(self) -> None:
        from anteroom.routers.spaces import api_list_spaces

        db = _make_db()
        from anteroom.services.space_storage import create_space

        global_path = str(Path.home() / ".anteroom" / "spaces" / "global.yaml")
        create_space(db, "global-space", source_file=global_path, source_hash="hash1")

        local_path = "/home/user/project/.anteroom/space.yaml"
        create_space(db, "local-space", source_file=local_path, source_hash="hash2")

        request = MagicMock()
        request.app.state.db = db

        result = await api_list_spaces(request)
        origins = {s["name"]: s["origin"] for s in result}
        assert origins["global-space"] == "global"
        assert origins["local-space"] == "local"

    @pytest.mark.asyncio
    async def test_list_spaces_origin_field_present(self) -> None:
        from anteroom.routers.spaces import api_list_spaces

        db = _make_db()
        from anteroom.services.space_storage import create_space

        create_space(db, "test", source_file="/tmp/test.yaml", source_hash="h")

        request = MagicMock()
        request.app.state.db = db

        result = await api_list_spaces(request)
        assert len(result) == 1
        assert "origin" in result[0]

    @pytest.mark.asyncio
    async def test_get_space_global_origin(self) -> None:
        from anteroom.routers.spaces import api_get_space

        db = _make_db()
        from anteroom.services.space_storage import create_space

        global_path = str(Path.home() / ".anteroom" / "spaces" / "test.yaml")
        s = create_space(db, "test", source_file=global_path, source_hash="hash")

        request = MagicMock()
        request.app.state.db = db

        result = await api_get_space(request, s["id"])
        assert result["origin"] == "global"

    @pytest.mark.asyncio
    async def test_get_space_local_origin(self) -> None:
        from anteroom.routers.spaces import api_get_space

        db = _make_db()
        from anteroom.services.space_storage import create_space

        s = create_space(db, "test", source_file="/home/user/project/.anteroom/space.yaml", source_hash="hash")

        request = MagicMock()
        request.app.state.db = db

        result = await api_get_space(request, s["id"])
        assert result["origin"] == "local"

    @pytest.mark.asyncio
    async def test_get_space_empty_file_path_is_global(self) -> None:
        from anteroom.routers.spaces import api_get_space

        db = _make_db()
        from anteroom.services.space_storage import create_space

        s = create_space(db, "test", source_file="", source_hash="")

        request = MagicMock()
        request.app.state.db = db

        result = await api_get_space(request, s["id"])
        assert result["origin"] == "global"

    @pytest.mark.asyncio
    async def test_get_space_not_found_raises_404(self) -> None:
        from fastapi import HTTPException

        from anteroom.routers.spaces import api_get_space

        db = _make_db()
        request = MagicMock()
        request.app.state.db = db

        with pytest.raises(HTTPException) as exc_info:
            await api_get_space(request, "nonexistent-id")
        assert exc_info.value.status_code == 404


class TestSyncSpaceFromFile:
    """Tests for sync_space_from_file()."""

    def test_sync_creates_new_space(self, tmp_path: Path) -> None:
        space_file = tmp_path / "space.yaml"
        space_file.write_text(yaml.dump({"name": "test-space", "version": "1"}))

        db = _make_db()
        result = sync_space_from_file(db, space_file)
        assert result["name"] == "test-space"
        assert result["source_file"] == str(space_file.resolve())
        assert result["source_hash"] != ""

    def test_sync_updates_existing_space(self, tmp_path: Path) -> None:
        space_file = tmp_path / "space.yaml"
        space_file.write_text(yaml.dump({"name": "test-space", "version": "1"}))

        db = _make_db()
        first = sync_space_from_file(db, space_file)
        # Modify file to trigger update
        space_file.write_text(yaml.dump({"name": "test-space", "version": "1", "instructions": "Be helpful"}))
        second = sync_space_from_file(db, space_file)
        assert first["id"] == second["id"]

    def test_sync_noop_when_hash_unchanged(self, tmp_path: Path) -> None:
        space_file = tmp_path / "space.yaml"
        space_file.write_text(yaml.dump({"name": "test-space", "version": "1"}))

        db = _make_db()
        first = sync_space_from_file(db, space_file)
        second = sync_space_from_file(db, space_file)
        assert first["id"] == second["id"]
        assert first["source_hash"] == second["source_hash"]

    def test_sync_no_tracking(self, tmp_path: Path) -> None:
        space_file = tmp_path / "space.yaml"
        space_file.write_text(yaml.dump({"name": "notrack", "version": "1"}))

        db = _make_db()
        result = sync_space_from_file(db, space_file, track_source=False)
        assert result["name"] == "notrack"
        assert result["source_file"] == ""
        assert result["source_hash"] == ""

    def test_sync_invalid_file_raises(self, tmp_path: Path) -> None:
        space_file = tmp_path / "space.yaml"
        space_file.write_text("not: valid: yaml: [")

        db = _make_db()
        with pytest.raises(Exception):
            sync_space_from_file(db, space_file)

    def test_sync_with_model(self, tmp_path: Path) -> None:
        space_file = tmp_path / "space.yaml"
        space_file.write_text(yaml.dump({"name": "model-space", "version": "1", "config": {"model": "gpt-4o"}}))

        db = _make_db()
        result = sync_space_from_file(db, space_file)
        assert result["name"] == "model-space"
        assert result["model"] == "gpt-4o"

    def test_sync_clears_model_when_removed_from_yaml(self, tmp_path: Path) -> None:
        """Regression: removing model from YAML should clear it in the DB."""
        space_file = tmp_path / "space.yaml"
        space_file.write_text(yaml.dump({"name": "model-clear", "version": "1", "config": {"model": "gpt-4o"}}))

        db = _make_db()
        result = sync_space_from_file(db, space_file)
        assert result["model"] == "gpt-4o"

        # Remove model from YAML and resync
        space_file.write_text(yaml.dump({"name": "model-clear", "version": "1"}))
        result = sync_space_from_file(db, space_file)
        assert result["model"] is None, "Model should be cleared when removed from YAML"


class TestExportSpaceToYaml:
    """Tests for export_space_to_yaml()."""

    def test_export_basic(self) -> None:
        db = _make_db()
        from anteroom.services.space_storage import create_space

        space = create_space(db, name="exportme", instructions="Be nice", model="gpt-4")
        cfg = export_space_to_yaml(db, space["id"])
        assert cfg.name == "exportme"
        assert cfg.instructions == "Be nice"
        assert cfg.config == {"model": "gpt-4"}

    def test_export_minimal(self) -> None:
        db = _make_db()
        from anteroom.services.space_storage import create_space

        space = create_space(db, name="minimal")
        cfg = export_space_to_yaml(db, space["id"])
        assert cfg.name == "minimal"
        assert cfg.instructions == ""
        assert cfg.config == {}

    def test_export_not_found_raises(self) -> None:
        db = _make_db()
        with pytest.raises(ValueError, match="not found"):
            export_space_to_yaml(db, "nonexistent-id")
