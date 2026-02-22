"""Skill system for custom CLI commands.

Skills are YAML files in ~/.anteroom/skills/ or .anteroom/skills/ (project-level).
Each skill defines a prompt template that gets injected when invoked via /skill_name.

Example skill file (~/.anteroom/skills/commit.yaml):
    name: commit
    description: Create a git commit with a conventional message
    prompt: |
      Look at the current git diff and staged changes.
      Create a commit with a conventional commit message.
      Format: type(scope): description
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    prompt: str
    source: str = ""  # "global" or "project"


def _load_skills_from_dir(skills_dir: Path, source: str) -> list[Skill]:
    """Load all .yaml skill files from a directory."""
    skills = []
    if not skills_dir.is_dir():
        return skills
    for path in sorted(skills_dir.glob("*.yaml")):
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                continue
            name = data.get("name", path.stem)
            description = data.get("description", "")
            prompt = data.get("prompt", "")
            if not prompt:
                continue
            skills.append(
                Skill(
                    name=name,
                    description=description,
                    prompt=prompt,
                    source=source,
                )
            )
        except Exception as e:
            logger.warning("Failed to load skill %s: %s", path, e)
    return skills


def _skill_dirs(working_dir: str | None = None) -> list[Path]:
    """Return skill directories (global + project)."""
    from ..config import _resolve_data_dir

    data_dir = _resolve_data_dir()
    dirs = [data_dir / "skills"]
    current = Path(working_dir or os.getcwd()).resolve()
    while True:
        for dirname in (".anteroom", ".parlor"):
            project_dir = current / dirname / "skills"
            if project_dir.is_dir():
                dirs.append(project_dir)
                return dirs
        parent = current.parent
        if parent == current:
            break
        current = parent
    return dirs


def load_skills(working_dir: str | None = None) -> list[Skill]:
    """Load skills from global and project directories."""
    skills: list[Skill] = []
    dirs = _skill_dirs(working_dir)
    sources = ["global"] + ["project"] * (len(dirs) - 1)
    for d, source in zip(dirs, sources):
        skills.extend(_load_skills_from_dir(d, source))
    return skills


class SkillRegistry:
    """Manages loaded skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self.load_warnings: list[str] = []

    def load(self, working_dir: str | None = None) -> None:
        self.load_warnings.clear()

        # Load bundled default skills first (can be overridden by user skills)
        default_dir = Path(__file__).parent / "default_skills"
        for skill in _load_skills_from_dir(default_dir, "default"):
            self._skills[skill.name] = skill

        # Load user skills (override defaults with same name)
        for skill in load_skills(working_dir):
            self._skills[skill.name] = skill

        # Capture any warnings from the logger
        # Re-scan for parse errors to surface them
        for skills_dir in _skill_dirs(working_dir):
            if not skills_dir.is_dir():
                continue
            for path in sorted(skills_dir.glob("*.yaml")):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if not isinstance(data, dict):
                        self.load_warnings.append(f"Skipped {path.name}: invalid format")
                    elif not data.get("prompt"):
                        self.load_warnings.append(f"Skipped {path.name}: missing 'prompt' field")
                except Exception as e:
                    self.load_warnings.append(f"Failed to load {path.name}: {e}")

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def has_skill(self, name: str) -> bool:
        return name in self._skills

    def resolve_input(self, user_input: str) -> tuple[bool, str]:
        """Check if input is a skill invocation. Returns (is_skill, expanded_prompt)."""
        if not user_input.startswith("/"):
            return False, user_input
        parts = user_input.split(maxsplit=1)
        skill_name = parts[0][1:]  # Remove leading /
        skill = self._skills.get(skill_name)
        if not skill:
            return False, user_input
        args = parts[1] if len(parts) > 1 else ""
        prompt = skill.prompt
        if args:
            prompt = f"{prompt}\n\nAdditional context: {args}"
        return True, prompt

    def get_skill_descriptions(self) -> list[tuple[str, str]]:
        """Return (name, description) pairs for all loaded skills, sorted by name."""
        return [(s.name, s.description) for s in self.list_skills()]

    def get_invoke_skill_definition(self) -> dict[str, Any] | None:
        """Return an OpenAI function schema for the invoke_skill tool.

        Returns None if no skills are loaded.
        """
        skills = self.list_skills()
        if not skills:
            return None
        return {
            "type": "function",
            "function": {
                "name": "invoke_skill",
                "description": (
                    "Invoke a predefined skill/workflow. Use this when the user's request "
                    "clearly matches one of the available skills listed in <available_skills>."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "enum": [s.name for s in skills],
                            "description": "The name of the skill to invoke.",
                        },
                        "args": {
                            "type": "string",
                            "description": "Optional additional context or arguments for the skill.",
                        },
                    },
                    "required": ["skill_name"],
                },
            },
        }
