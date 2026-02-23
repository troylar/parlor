"""Unified walk-up directory discovery for config, instructions, skills, and rules.

Provides a shared utility for the walk-up-from-cwd pattern used throughout
Anteroom.  Callers specify what to look for (files, directories, or both)
and this module handles the traversal, stopping at the home directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Anteroom recognises these directories as interchangeable.
_PROJECT_DIR_NAMES = (".anteroom", ".claude", ".parlor")


@dataclass(frozen=True)
class DiscoveryResult:
    """A discovered file or directory."""

    path: Path
    source: str  # "project", "global", or the directory name


def walk_up_for_file(
    filenames: tuple[str, ...],
    start: str | Path | None = None,
    *,
    stop_at_home: bool = True,
) -> Path | None:
    """Walk up from *start* looking for any of *filenames* at each level.

    Returns the first matching file path, or None.
    """
    current = Path(start or os.getcwd()).resolve()
    home = Path.home().resolve() if stop_at_home else None
    while True:
        for name in filenames:
            candidate = current / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        if home and current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def walk_up_for_dir(
    relative_paths: tuple[str, ...],
    start: str | Path | None = None,
    *,
    stop_at_home: bool = True,
) -> Path | None:
    """Walk up from *start* looking for a directory at each level.

    *relative_paths* are paths relative to each directory level
    (e.g. ``(".anteroom/skills", ".claude/skills")``).

    Returns the first matching directory path, or None.
    """
    current = Path(start or os.getcwd()).resolve()
    home = Path.home().resolve() if stop_at_home else None
    while True:
        for rel in relative_paths:
            candidate = current / rel
            try:
                if candidate.is_dir():
                    return candidate
            except OSError:
                continue
        if home and current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def find_project_dir(
    subdir: str,
    start: str | Path | None = None,
) -> Path | None:
    """Walk up looking for ``.anteroom/<subdir>``, ``.claude/<subdir>``, or ``.parlor/<subdir>``.

    Convenience wrapper over :func:`walk_up_for_dir` using the standard
    project directory names.
    """
    candidates = tuple(f"{d}/{subdir}" for d in _PROJECT_DIR_NAMES)
    return walk_up_for_dir(candidates, start)


def find_all_project_dirs(
    subdir: str,
    start: str | Path | None = None,
    *,
    stop_at_home: bool = True,
) -> list[Path]:
    """Walk up and collect ALL matching directories (not just the first).

    Returns directories ordered from nearest (project-level) to farthest.
    This is used for rules, which accumulate from all levels.
    """
    results: list[Path] = []
    current = Path(start or os.getcwd()).resolve()
    home = Path.home().resolve() if stop_at_home else None
    while True:
        for d in _PROJECT_DIR_NAMES:
            candidate = current / d / subdir
            try:
                if candidate.is_dir():
                    results.append(candidate)
                    break  # only one per level (first matching dir name wins)
            except OSError:
                continue
        if home and current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return results
