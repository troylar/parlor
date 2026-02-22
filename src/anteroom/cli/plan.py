"""Planning mode helpers: file I/O and system prompt construction."""

from __future__ import annotations

import os
from pathlib import Path

# Tools allowed during plan mode — read-only exploration plus write_file for the plan itself
PLAN_MODE_ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "glob_files",
        "grep",
        "bash",
        "write_file",
        "run_agent",
    }
)


def get_plan_file_path(data_dir: Path, conversation_id: str) -> Path:
    """Return the plan file path for a conversation, creating the plans/ dir.

    Raises ValueError if conversation_id contains path traversal.
    """
    plans_dir = data_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = (plans_dir / f"{conversation_id}.md").resolve()
    if not str(plan_path).startswith(str(plans_dir.resolve())):
        raise ValueError("Invalid conversation_id")
    return plan_path


def build_planning_system_prompt(plan_file_path: Path) -> str:
    """Return a system prompt section that constrains the AI to planning mode."""
    return (
        "<planning_mode>\n"
        "You are in PLANNING MODE. Your job is to explore the codebase, gather information, "
        "and write a detailed implementation plan — NOT to implement anything.\n\n"
        "Rules:\n"
        "- Use read_file, glob_files, grep, and bash to explore the codebase\n"
        "- You MUST NOT create, edit, or delete any files EXCEPT the plan file below\n"
        "- When you have gathered enough information, write your plan using write_file to:\n"
        f"  {plan_file_path}\n\n"
        "Plan format (Markdown):\n"
        "- ## Overview — what the task is and the approach\n"
        "- ## Files to Change — list of files with what changes are needed\n"
        "- ## Implementation Steps — ordered steps with details\n"
        "- ## Test Strategy — what tests to add or modify\n\n"
        "When the plan is written, tell the user it is ready and they can review it with "
        "`/plan status` and approve it with `/plan approve`.\n"
        "</planning_mode>"
    )


def read_plan(plan_file_path: Path) -> str | None:
    """Read the plan file, returning None if it doesn't exist."""
    if not plan_file_path.exists():
        return None
    return plan_file_path.read_text(encoding="utf-8")


def get_editor() -> str:
    """Resolve the user's preferred editor: $VISUAL > $EDITOR > vi."""
    return os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"


def delete_plan(plan_file_path: Path) -> None:
    """Delete the plan file if it exists."""
    if plan_file_path.exists():
        plan_file_path.unlink()
