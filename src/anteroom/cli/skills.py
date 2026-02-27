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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_VALID_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_SKILLS = 100
_ARGS_PLACEHOLDER = "{args}"
MAX_PROMPT_SIZE = 50_000  # 50KB limit on skill prompts

# Built-in slash commands that skill names must not shadow.
# If a skill has one of these names, direct /invocation would hit the built-in
# command handler instead, while invoke_skill would hit the skill — causing
# inconsistent behavior.
_BUILTIN_COMMANDS = frozenset(
    {
        "quit",
        "exit",
        "new",
        "append",
        "tools",
        "conventions",
        "upload",
        "usage",
        "help",
        "compact",
        "last",
        "list",
        "delete",
        "rename",
        "slug",
        "search",
        "skills",
        "reload-skills",
        "projects",
        "project",
        "mcp",
        "model",
        "pack",
        "packs",
        "plan",
        "verbose",
        "detail",
        "resume",
        "rewind",
    }
)

# Regex to match fenced code blocks (``` ... ```)
_CODE_FENCE_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)


@dataclass
class Skill:
    name: str
    description: str
    prompt: str
    source: str = ""  # "default", "global", or "project"


@dataclass
class _SearchedDir:
    """Record of a directory that was checked during skill loading."""

    path: str
    source: str  # "default", "global", or "project"
    skill_count: int
    exists: bool


@dataclass
class _LoadResult:
    skills: list[Skill] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    searched_dirs: list[_SearchedDir] = field(default_factory=list)


def _yaml_error_hint(error: yaml.YAMLError) -> str:
    """Return an actionable hint for common YAML errors."""
    msg = str(error)
    if "mapping values are not allowed here" in msg:
        return "Hint: values containing colons must be quoted, or use block scalar '|' for multi-line prompts"
    if "while parsing a flow mapping" in msg or "expected ',' or '}'" in msg:
        return (
            "Hint: '{args}' is interpreted as YAML flow mapping. "
            "Use block scalar 'prompt: |' for prompts containing curly braces"
        )
    return ""


def _format_yaml_error(path: Path, error: yaml.YAMLError) -> str:
    """Format a YAML error with file location and hint."""
    parts = [f"Failed to load {path.name}"]
    if hasattr(error, "problem_mark") and error.problem_mark is not None:
        mark = error.problem_mark
        parts.append(f"line {mark.line + 1}, column {mark.column + 1}")
    parts_str = " (".join(parts) + (")" if len(parts) > 1 else "")
    msg = f"{parts_str}: {error.problem}" if hasattr(error, "problem") else f"{parts_str}: {error}"
    hint = _yaml_error_hint(error)
    if hint:
        msg = f"{msg}. {hint}"
    return msg


def _validate_skill_name(raw_name: str, stem: str) -> tuple[str, str | None]:
    """Validate and normalize a skill name.

    Returns (normalized_name, warning_or_none).
    """
    name = raw_name.strip() if raw_name else ""
    if not name:
        name = stem
    if not _VALID_SKILL_NAME.match(name):
        return "", f"Skipped {stem}: invalid skill name '{name}' (must match [a-z0-9][a-z0-9_-]*)"
    if name in _BUILTIN_COMMANDS:
        return "", f"Skipped {stem}: skill name '{name}' conflicts with built-in /{name} command"
    return name, None


def _load_skills_from_dir(skills_dir: Path, source: str) -> _LoadResult:
    """Load all .yaml/.yml skill files from a directory."""
    result = _LoadResult()
    if not skills_dir.is_dir():
        result.searched_dirs.append(_SearchedDir(str(skills_dir), source, 0, False))
        return result
    paths = sorted(set(skills_dir.glob("*.yaml")) | set(skills_dir.glob("*.yml")), key=lambda p: p.name)
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data is None:
                result.warnings.append(f"Skipped {path.name}: empty file")
                continue
            if not isinstance(data, dict):
                result.warnings.append(f"Skipped {path.name}: invalid format (expected YAML mapping)")
                continue
            raw_name = data.get("name", path.stem)
            if not isinstance(raw_name, str):
                result.warnings.append(f"Skipped {path.name}: 'name' must be a string, got {type(raw_name).__name__}")
                continue
            name, warning = _validate_skill_name(raw_name, path.stem)
            if warning:
                result.warnings.append(warning)
                continue
            prompt = data.get("prompt", "")
            if not isinstance(prompt, str):
                result.warnings.append(f"Skipped {path.name}: 'prompt' must be a string, got {type(prompt).__name__}")
                continue
            if not prompt.strip():
                result.warnings.append(f"Skipped {path.name}: missing 'prompt' field")
                continue
            if len(prompt) > MAX_PROMPT_SIZE:
                result.warnings.append(
                    f"Skipped {path.name}: prompt exceeds {MAX_PROMPT_SIZE // 1000}KB limit ({len(prompt) // 1000}KB)"
                )
                continue
            description = data.get("description", "")
            if not description:
                result.warnings.append(f"{path.name} has no description")
            result.skills.append(
                Skill(
                    name=name,
                    description=str(description) if description else "",
                    prompt=prompt,
                    source=source,
                )
            )
        except yaml.YAMLError as e:
            result.warnings.append(_format_yaml_error(path, e))
        except Exception as e:
            result.warnings.append(f"Failed to load {path.name}: {e}")
    result.searched_dirs.append(_SearchedDir(str(skills_dir), source, len(result.skills), True))
    return result


def _skill_dirs(working_dir: str | None = None) -> list[Path]:
    """Return skill directories (global + project).

    Walks up from working_dir looking for .anteroom/skills/, .claude/skills/,
    or .parlor/skills/. Collects ALL matching directories at the first level
    that has any match, not just the first one found.
    """
    from ..config import _resolve_data_dir

    data_dir = _resolve_data_dir()
    dirs = [data_dir / "skills"]
    current = Path(working_dir or os.getcwd()).resolve()
    while True:
        found: list[Path] = []
        for dirname in (".anteroom", ".claude", ".parlor"):
            project_dir = current / dirname / "skills"
            if project_dir.is_dir():
                found.append(project_dir)
        if found:
            dirs.extend(found)
            return dirs
        parent = current.parent
        if parent == current:
            break
        current = parent
    return dirs


def load_skills(working_dir: str | None = None) -> _LoadResult:
    """Load skills from global and project directories."""
    combined = _LoadResult()
    dirs = _skill_dirs(working_dir)
    sources = ["global"] + ["project"] * (len(dirs) - 1)
    for d, source in zip(dirs, sources):
        result = _load_skills_from_dir(d, source)
        combined.skills.extend(result.skills)
        combined.warnings.extend(result.warnings)
        combined.searched_dirs.extend(result.searched_dirs)
    return combined


def _expand_args(prompt: str, args: str) -> str:
    """Expand {args} placeholder in prompt, or append as context.

    - Empty/whitespace-only args are a no-op (returns prompt unchanged).
    - {args} inside fenced code blocks (``` ... ```) is NOT replaced.
    - When no {args} placeholder exists outside code blocks, args are appended.
    """
    if not args or not args.strip():
        return prompt

    # Check for {args} outside of fenced code blocks
    segments = _CODE_FENCE_RE.split(prompt)
    has_placeholder_outside_code = False
    for i, segment in enumerate(segments):
        is_code_block = i % 2 == 1  # odd indices are code fence captures
        if not is_code_block and _ARGS_PLACEHOLDER in segment:
            has_placeholder_outside_code = True
            break

    if has_placeholder_outside_code:
        # Replace {args} only in non-code-block segments
        result_parts = []
        for i, segment in enumerate(segments):
            is_code_block = i % 2 == 1
            if is_code_block:
                result_parts.append(segment)
            else:
                result_parts.append(segment.replace(_ARGS_PLACEHOLDER, args))
        return "".join(result_parts)

    return f"{prompt}\n\nAdditional context: {args}"


class SkillRegistry:
    """Manages loaded skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self.load_warnings: list[str] = []
        self.searched_dirs: list[_SearchedDir] = []

    def load(self, working_dir: str | None = None) -> list[Skill]:
        """Load skills from default, global, and project directories.

        Uses atomic swap: builds new dict fully before replacing self._skills,
        so concurrent readers never see a partially-loaded state.

        Returns the list of loaded skills (for callers that need to rebuild schemas).
        """
        new_skills: dict[str, Skill] = {}
        new_warnings: list[str] = []
        new_searched: list[_SearchedDir] = []

        default_dir = Path(__file__).parent / "default_skills"
        default_result = _load_skills_from_dir(default_dir, "default")
        new_warnings.extend(default_result.warnings)
        new_searched.extend(default_result.searched_dirs)
        default_names = set()
        for skill in default_result.skills:
            new_skills[skill.name] = skill
            default_names.add(skill.name)

        user_result = load_skills(working_dir)
        new_warnings.extend(user_result.warnings)
        new_searched.extend(user_result.searched_dirs)
        for skill in user_result.skills:
            if skill.name in default_names:
                new_warnings.append(f"User skill '{skill.name}' ({skill.source}) overrides built-in")
            new_skills[skill.name] = skill

        if len(new_skills) > MAX_SKILLS:
            new_warnings.append(
                f"Loaded {len(new_skills)} skills (limit: {MAX_SKILLS}). Consider removing unused skill files."
            )

        # Atomic swap — Python dict assignment is GIL-protected
        self._skills = new_skills
        self.load_warnings = new_warnings
        self.searched_dirs = new_searched

        return sorted(new_skills.values(), key=lambda s: s.name)

    reload = load

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name.lower() if name else name)

    def list_skills(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def has_skill(self, name: str) -> bool:
        return (name.lower() if name else name) in self._skills

    def resolve_input(self, user_input: str) -> tuple[bool, str]:
        """Check if input is a skill invocation. Returns (is_skill, expanded_prompt)."""
        if not user_input.startswith("/"):
            return False, user_input
        parts = user_input.split(maxsplit=1)
        skill_name = parts[0][1:].lower()  # Remove leading /, normalize case
        skill = self._skills.get(skill_name)
        if not skill:
            return False, user_input
        args = parts[1] if len(parts) > 1 else ""
        prompt = skill.prompt
        if args:
            prompt = _expand_args(prompt, args)
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
