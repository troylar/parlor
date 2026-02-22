"""Tests for CLI planning mode helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from anteroom.cli.plan import (
    PLAN_MODE_ALLOWED_TOOLS,
    build_planning_system_prompt,
    delete_plan,
    get_plan_file_path,
    read_plan,
)


class TestPlanFilePath:
    def test_returns_correct_path(self, tmp_path: Path) -> None:
        result = get_plan_file_path(tmp_path, "conv-123")
        assert result == (tmp_path / "plans" / "conv-123.md").resolve()

    def test_creates_plans_directory(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / "plans"
        assert not plans_dir.exists()
        get_plan_file_path(tmp_path, "conv-abc")
        assert plans_dir.is_dir()

    def test_idempotent_directory_creation(self, tmp_path: Path) -> None:
        get_plan_file_path(tmp_path, "first")
        get_plan_file_path(tmp_path, "second")
        assert (tmp_path / "plans").is_dir()

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid conversation_id"):
            get_plan_file_path(tmp_path, "../../etc/evil")

    def test_rejects_parent_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid conversation_id"):
            get_plan_file_path(tmp_path, "../outside")


class TestReadPlan:
    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "plans" / "missing.md"
        assert read_plan(path) is None

    def test_returns_content_when_exists(self, tmp_path: Path) -> None:
        path = get_plan_file_path(tmp_path, "conv-123")
        path.write_text("## Overview\nThe plan.", encoding="utf-8")
        result = read_plan(path)
        assert result == "## Overview\nThe plan."


class TestDeletePlan:
    def test_deletes_existing_file(self, tmp_path: Path) -> None:
        path = get_plan_file_path(tmp_path, "conv-123")
        path.write_text("plan content", encoding="utf-8")
        assert path.exists()
        delete_plan(path)
        assert not path.exists()

    def test_no_error_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "plans" / "nonexistent.md"
        delete_plan(path)  # should not raise


class TestBuildPlanningSystemPrompt:
    def test_contains_xml_tags(self, tmp_path: Path) -> None:
        path = tmp_path / "plans" / "conv-123.md"
        result = build_planning_system_prompt(path)
        assert "<planning_mode>" in result
        assert "</planning_mode>" in result

    def test_contains_plan_file_path(self, tmp_path: Path) -> None:
        path = tmp_path / "plans" / "conv-123.md"
        result = build_planning_system_prompt(path)
        assert str(path) in result

    def test_contains_write_file_instruction(self, tmp_path: Path) -> None:
        path = tmp_path / "plans" / "conv-123.md"
        result = build_planning_system_prompt(path)
        assert "write_file" in result

    def test_contains_plan_format(self, tmp_path: Path) -> None:
        path = tmp_path / "plans" / "conv-123.md"
        result = build_planning_system_prompt(path)
        assert "## Overview" in result
        assert "## Files to Change" in result
        assert "## Implementation Steps" in result
        assert "## Test Strategy" in result

    def test_mentions_plan_approve(self, tmp_path: Path) -> None:
        path = tmp_path / "plans" / "conv-123.md"
        result = build_planning_system_prompt(path)
        assert "/plan approve" in result


class TestPlanModeAllowedTools:
    def test_contains_read_tools(self) -> None:
        assert "read_file" in PLAN_MODE_ALLOWED_TOOLS
        assert "glob_files" in PLAN_MODE_ALLOWED_TOOLS
        assert "grep" in PLAN_MODE_ALLOWED_TOOLS

    def test_contains_bash(self) -> None:
        assert "bash" in PLAN_MODE_ALLOWED_TOOLS

    def test_contains_write_file(self) -> None:
        assert "write_file" in PLAN_MODE_ALLOWED_TOOLS

    def test_contains_run_agent(self) -> None:
        assert "run_agent" in PLAN_MODE_ALLOWED_TOOLS

    def test_excludes_edit_file(self) -> None:
        assert "edit_file" not in PLAN_MODE_ALLOWED_TOOLS

    def test_excludes_canvas_tools(self) -> None:
        assert "create_canvas" not in PLAN_MODE_ALLOWED_TOOLS
        assert "update_canvas" not in PLAN_MODE_ALLOWED_TOOLS
        assert "patch_canvas" not in PLAN_MODE_ALLOWED_TOOLS
