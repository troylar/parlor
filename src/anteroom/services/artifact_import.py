"""Migration: import existing skills and instructions into the artifact system.

Non-destructive — original files are preserved, artifacts created alongside.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

from .artifact_storage import upsert_artifact
from .artifacts import ArtifactSource, ArtifactType, build_fqn

logger = logging.getLogger(__name__)

_LOCAL_NAMESPACE = "local"


@dataclass(frozen=True)
class ImportResult:
    """Result of an import operation."""

    imported: int = 0
    skipped: int = 0
    errors: int = 0
    details: tuple[str, ...] = ()


def import_skills(
    db: sqlite3.Connection,
    skills_dir: Path,
) -> ImportResult:
    """Import YAML skill files from a directory into the artifact system.

    Scans *skills_dir* for ``.yaml`` files, reads their content, and
    upserts as ``@local/skill/<name>`` artifacts with ``local`` source.
    """
    if not skills_dir.is_dir():
        return ImportResult(details=(f"Directory not found: {skills_dir}",))

    imported = 0
    skipped = 0
    errors = 0
    details: list[str] = []

    for path in sorted(skills_dir.glob("*.yaml")):
        name = path.stem
        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                details.append(f"Skipped {path.name}: not a YAML mapping")
                skipped += 1
                continue

            content = str(data.get("content", data.get("prompt", raw)))
            fqn = build_fqn(_LOCAL_NAMESPACE, ArtifactType.SKILL.value, name)

            upsert_artifact(
                db,
                fqn=fqn,
                artifact_type=ArtifactType.SKILL,
                namespace=_LOCAL_NAMESPACE,
                name=name,
                content=content,
                source=ArtifactSource.LOCAL,
                metadata={"imported_from": str(path)},
            )
            imported += 1
            details.append(f"Imported skill: {name}")

        except Exception as e:
            errors += 1
            details.append(f"Error importing {path.name}: {e}")

    return ImportResult(imported=imported, skipped=skipped, errors=errors, details=tuple(details))


def import_instructions(
    db: sqlite3.Connection,
    instructions_path: Path,
) -> ImportResult:
    """Import an ANTEROOM.md file by splitting sections into instruction artifacts.

    Each top-level ``## Heading`` becomes a separate instruction artifact
    named after the heading (kebab-case). Content between headings becomes
    the artifact content.
    """
    if not instructions_path.is_file():
        return ImportResult(details=(f"File not found: {instructions_path}",))

    raw = instructions_path.read_text(encoding="utf-8")
    sections = _split_markdown_sections(raw)

    if not sections:
        return ImportResult(details=("No sections found in file",))

    imported = 0
    errors = 0
    details: list[str] = []

    for heading, content in sections:
        name = _heading_to_name(heading)
        if not name:
            continue

        try:
            fqn = build_fqn(_LOCAL_NAMESPACE, ArtifactType.INSTRUCTION.value, name)
            upsert_artifact(
                db,
                fqn=fqn,
                artifact_type=ArtifactType.INSTRUCTION,
                namespace=_LOCAL_NAMESPACE,
                name=name,
                content=content.strip(),
                source=ArtifactSource.LOCAL,
                metadata={"imported_from": str(instructions_path), "heading": heading},
            )
            imported += 1
            details.append(f"Imported instruction: {name}")
        except Exception as e:
            errors += 1
            details.append(f"Error importing section '{heading}': {e}")

    return ImportResult(imported=imported, errors=errors, details=tuple(details))


def import_all(
    db: sqlite3.Connection,
    data_dir: Path,
    *,
    project_dir: Path | None = None,
) -> dict[str, ImportResult]:
    """Import all existing extensibility files into the artifact system.

    Looks for skills in ``data_dir/skills/`` and instructions in
    common ANTEROOM.md locations under *project_dir*.
    """
    results: dict[str, ImportResult] = {}

    # Import skills from global directory
    skills_dir = data_dir / "skills"
    if skills_dir.is_dir():
        results["skills"] = import_skills(db, skills_dir)

    # Import instructions from common locations
    if project_dir is not None:
        for inst_name in (".anteroom.md", "ANTEROOM.md", "anteroom.md"):
            inst_path = project_dir / inst_name
            if inst_path.is_file():
                results["instructions"] = import_instructions(db, inst_path)
                break

    return results


def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, content) pairs on ``## `` boundaries."""
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_heading:
                sections.append((current_heading, "\n".join(current_lines)))
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading:
            current_lines.append(line)

    if current_heading:
        sections.append((current_heading, "\n".join(current_lines)))

    return sections


def _heading_to_name(heading: str) -> str:
    """Convert a markdown heading to a kebab-case artifact name."""
    name = heading.lower().strip()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    name = name.strip("-")
    if not name or len(name) > 64:
        return ""
    return name
