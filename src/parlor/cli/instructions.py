"""PARLOR.md discovery and loading for project/global instructions."""

from __future__ import annotations

import os
from pathlib import Path

INSTRUCTION_FILENAME = "PARLOR.md"


def find_project_instructions(start_dir: str | None = None) -> str | None:
    """Walk up from start_dir to find the nearest PARLOR.md."""
    current = Path(start_dir or os.getcwd()).resolve()
    while True:
        candidate = current / INSTRUCTION_FILENAME
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                return None
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def find_global_instructions() -> str | None:
    """Load ~/.parlor/PARLOR.md if it exists."""
    path = Path.home() / ".parlor" / INSTRUCTION_FILENAME
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None
    return None


def load_instructions(working_dir: str | None = None) -> str | None:
    """Load and concatenate global + project instructions."""
    parts: list[str] = []

    global_inst = find_global_instructions()
    if global_inst:
        parts.append(f"# Global Instructions\n{global_inst}")

    project_inst = find_project_instructions(working_dir)
    if project_inst:
        parts.append(f"# Project Instructions\n{project_inst}")

    if not parts:
        return None
    return "\n\n".join(parts)
