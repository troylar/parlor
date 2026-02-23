"""Tests for CLI planning mode helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from anteroom.cli.plan import (
    PLAN_MODE_ALLOWED_TOOLS,
    build_planning_system_prompt,
    delete_plan,
    get_editor,
    get_plan_file_path,
    parse_plan_command,
    parse_plan_steps,
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


class TestGetEditor:
    def test_visual_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VISUAL", "code")
        monkeypatch.setenv("EDITOR", "nano")
        assert get_editor() == "code"

    def test_editor_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("EDITOR", "nano")
        assert get_editor() == "nano"

    def test_vi_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.delenv("EDITOR", raising=False)
        assert get_editor() == "vi"

    def test_empty_visual_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VISUAL", "")
        monkeypatch.setenv("EDITOR", "nano")
        assert get_editor() == "nano"


class TestParsePlanCommand:
    def test_no_args_defaults_to_on(self) -> None:
        sub, prompt = parse_plan_command("/plan")
        assert sub == "on"
        assert prompt is None

    def test_known_subcommand_on(self) -> None:
        sub, prompt = parse_plan_command("/plan on")
        assert sub == "on"
        assert prompt is None

    def test_known_subcommand_start(self) -> None:
        sub, prompt = parse_plan_command("/plan start")
        assert sub == "start"
        assert prompt is None

    def test_known_subcommand_approve(self) -> None:
        sub, prompt = parse_plan_command("/plan approve")
        assert sub == "approve"
        assert prompt is None

    def test_known_subcommand_status(self) -> None:
        sub, prompt = parse_plan_command("/plan status")
        assert sub == "status"
        assert prompt is None

    def test_known_subcommand_off(self) -> None:
        sub, prompt = parse_plan_command("/plan off")
        assert sub == "off"
        assert prompt is None

    def test_known_subcommand_edit(self) -> None:
        sub, prompt = parse_plan_command("/plan edit")
        assert sub == "edit"
        assert prompt is None

    def test_known_subcommand_case_insensitive(self) -> None:
        sub, prompt = parse_plan_command("/plan APPROVE")
        assert sub == "approve"
        assert prompt is None

    def test_inline_prompt_single_word(self) -> None:
        sub, prompt = parse_plan_command("/plan refactor")
        assert sub is None
        assert prompt == "refactor"

    def test_inline_prompt_multi_word(self) -> None:
        sub, prompt = parse_plan_command("/plan build a REST API for user auth")
        assert sub is None
        assert prompt == "build a REST API for user auth"

    def test_inline_prompt_preserves_case(self) -> None:
        sub, prompt = parse_plan_command("/plan Build a REST API")
        assert sub is None
        assert prompt == "Build a REST API"


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


class TestParsePlanSteps:
    """Verify extraction of implementation steps from plan markdown (#166)."""

    def test_numbered_steps(self) -> None:
        content = (
            "## Overview\nSome overview text.\n\n"
            "## Implementation Steps\n"
            "1. Read the config file\n"
            "2. Check for existing tests\n"
            "3. Write the new test cases\n\n"
            "## Test Strategy\nTest stuff.\n"
        )
        steps = parse_plan_steps(content)
        assert steps == [
            "Read the config file",
            "Check for existing tests",
            "Write the new test cases",
        ]

    def test_bulleted_steps(self) -> None:
        content = "## Implementation Steps\n- First do this\n- Then do that\n- Finally wrap up\n"
        steps = parse_plan_steps(content)
        assert steps == ["First do this", "Then do that", "Finally wrap up"]

    def test_asterisk_bullets(self) -> None:
        content = "## Implementation Steps\n* Step A\n* Step B\n"
        steps = parse_plan_steps(content)
        assert steps == ["Step A", "Step B"]

    def test_parenthesis_numbered(self) -> None:
        content = "## Implementation Steps\n1) Do thing\n2) Do other thing\n"
        steps = parse_plan_steps(content)
        assert steps == ["Do thing", "Do other thing"]

    def test_case_insensitive_heading(self) -> None:
        content = "## implementation steps\n1. Lower case heading\n"
        steps = parse_plan_steps(content)
        assert steps == ["Lower case heading"]

    def test_no_implementation_steps_section(self) -> None:
        content = "## Overview\nNo steps here.\n## Test Strategy\nTests.\n"
        assert parse_plan_steps(content) == []

    def test_empty_content(self) -> None:
        assert parse_plan_steps("") == []

    def test_steps_stop_at_next_heading(self) -> None:
        content = (
            "## Implementation Steps\n"
            "1. First step\n"
            "2. Second step\n\n"
            "## Test Strategy\n"
            "1. This is a test item, not a step\n"
        )
        steps = parse_plan_steps(content)
        assert steps == ["First step", "Second step"]

    def test_indented_steps(self) -> None:
        content = "## Implementation Steps\n  1. Indented step\n  2. Another\n"
        steps = parse_plan_steps(content)
        assert steps == ["Indented step", "Another"]

    def test_steps_with_markdown_formatting(self) -> None:
        content = "## Implementation Steps\n1. Add **bold** feature\n2. Fix `code` issue\n"
        steps = parse_plan_steps(content)
        assert steps == ["Add **bold** feature", "Fix `code` issue"]

    def test_multiline_content_before_steps(self) -> None:
        content = (
            "# Big Plan\n\n"
            "## Overview\nLots of text here.\nMore text.\n\n"
            "## Files to Change\n- file1.py\n- file2.py\n\n"
            "## Implementation Steps\n"
            "1. Modify file1.py to add the new function\n"
            "2. Update file2.py with the integration\n"
            "3. Run tests\n\n"
            "## Test Strategy\nAdd unit tests.\n"
        )
        steps = parse_plan_steps(content)
        assert len(steps) == 3
        assert steps[0] == "Modify file1.py to add the new function"
        assert steps[2] == "Run tests"
