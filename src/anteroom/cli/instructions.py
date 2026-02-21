"""ANTEROOM.md discovery and loading for project/global instructions."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

INSTRUCTION_FILENAME = "ANTEROOM.md"
_HIDDEN_INSTRUCTION_FILENAME = ".anteroom.md"

_SEARCH_FILENAMES = (
    _HIDDEN_INSTRUCTION_FILENAME,
    INSTRUCTION_FILENAME,
)

CONVENTIONS_TOKEN_WARNING_THRESHOLD = 4000
_CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // _CHARS_PER_TOKEN_ESTIMATE


@dataclass(frozen=True)
class ConventionsInfo:
    """Metadata about a discovered conventions file."""

    path: Path | None
    content: str | None
    source: str  # "project", "global", or "none"
    estimated_tokens: int = 0
    is_oversized: bool = False

    @property
    def warning(self) -> str | None:
        if self.is_oversized:
            return (
                f"Conventions file is ~{self.estimated_tokens:,} tokens "
                f"(threshold: {CONVENTIONS_TOKEN_WARNING_THRESHOLD:,}). "
                f"Large files reduce prompt effectiveness."
            )
        return None


def find_project_instructions_path(start_dir: str | None = None) -> tuple[Path, str] | None:
    """Walk up from start_dir to find the nearest ANTEROOM.md. Returns (path, content) or None.

    Search order at each directory level:
      .anteroom.md > ANTEROOM.md
    """
    current = Path(start_dir or os.getcwd()).resolve()
    while True:
        for filename in _SEARCH_FILENAMES:
            candidate = current / filename
            if candidate.is_file():
                try:
                    return candidate, candidate.read_text(encoding="utf-8")
                except OSError:
                    return None
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def find_project_instructions(start_dir: str | None = None) -> str | None:
    """Walk up from start_dir to find the nearest ANTEROOM.md."""
    result = find_project_instructions_path(start_dir)
    if result is None:
        return None
    return result[1]


def find_global_instructions() -> str | None:
    """Load ~/.anteroom/ANTEROOM.md if it exists."""
    from ..config import _resolve_data_dir

    data_dir = _resolve_data_dir()
    for filename in _SEARCH_FILENAMES:
        path = data_dir / filename
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return None
    return None


def find_global_instructions_path() -> tuple[Path, str] | None:
    """Load ~/.anteroom/ANTEROOM.md with path info. Returns (path, content) or None."""
    from ..config import _resolve_data_dir

    data_dir = _resolve_data_dir()
    for filename in _SEARCH_FILENAMES:
        path = data_dir / filename
        if path.is_file():
            try:
                return path, path.read_text(encoding="utf-8")
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


def discover_conventions(working_dir: str | None = None) -> ConventionsInfo:
    """Discover the active conventions file and return metadata.

    Checks project-level first (walking up from working_dir), then global.
    """
    result = find_project_instructions_path(working_dir)
    if result is not None:
        path, content = result
        tokens = estimate_tokens(content)
        return ConventionsInfo(
            path=path,
            content=content,
            source="project",
            estimated_tokens=tokens,
            is_oversized=tokens > CONVENTIONS_TOKEN_WARNING_THRESHOLD,
        )

    global_result = find_global_instructions_path()
    if global_result is not None:
        path, content = global_result
        tokens = estimate_tokens(content)
        return ConventionsInfo(
            path=path,
            content=content,
            source="global",
            estimated_tokens=tokens,
            is_oversized=tokens > CONVENTIONS_TOKEN_WARNING_THRESHOLD,
        )

    return ConventionsInfo(path=None, content=None, source="none")
