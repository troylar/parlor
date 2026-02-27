"""Local artifact discovery and loading from filesystem directories.

Local artifacts live in ``~/.anteroom/local/`` (global) and
``.anteroom/local/`` (project). They are loaded at ``local`` precedence
(highest — override everything including packs).

Directory structure::

    local/
        skills/
            my-skill.yaml
        rules/
            my-rule.md
        instructions/
            my-instruction.md
        context/
            my-context.md
        memories/
            my-memory.md
        mcp_servers/
            my-server.yaml
        config_overlays/
            my-overlay.yaml
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from .artifact_storage import upsert_artifact
from .artifacts import ArtifactSource, ArtifactType, build_fqn

logger = logging.getLogger(__name__)

_LOCAL_DIR = "local"
_ANTEROOM_DIR = ".anteroom"
_LOCAL_NAMESPACE = "local"

# Map artifact type to subdirectory name
_TYPE_DIRS: dict[str, str] = {
    ArtifactType.SKILL: "skills",
    ArtifactType.RULE: "rules",
    ArtifactType.INSTRUCTION: "instructions",
    ArtifactType.CONTEXT: "context",
    ArtifactType.MEMORY: "memories",
    ArtifactType.MCP_SERVER: "mcp_servers",
    ArtifactType.CONFIG_OVERLAY: "config_overlays",
}

_EXT_MAP: dict[str, tuple[str, ...]] = {
    ArtifactType.SKILL: (".yaml", ".yml"),
    ArtifactType.RULE: (".md", ".txt"),
    ArtifactType.INSTRUCTION: (".md", ".txt"),
    ArtifactType.CONTEXT: (".md", ".txt", ".json"),
    ArtifactType.MEMORY: (".md", ".txt"),
    ArtifactType.MCP_SERVER: (".yaml", ".yml", ".json"),
    ArtifactType.CONFIG_OVERLAY: (".yaml", ".yml"),
}


def discover_local_artifacts(
    local_dir: Path,
) -> list[dict[str, Any]]:
    """Scan a ``local/`` directory and return artifact dicts ready for DB upsert.

    Does NOT write to DB — returns the discovered artifact metadata.
    """
    if not local_dir.is_dir():
        return []

    artifacts: list[dict[str, Any]] = []
    for art_type in ArtifactType:
        subdir_name = _TYPE_DIRS.get(art_type, art_type.value)
        subdir = local_dir / subdir_name
        if not subdir.is_dir():
            continue

        valid_exts = _EXT_MAP.get(art_type, (".yaml", ".yml", ".md", ".txt"))
        for path in sorted(subdir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in valid_exts:
                continue

            name = path.stem
            content = _read_content(path, art_type)
            if content is None:
                continue

            try:
                fqn = build_fqn(_LOCAL_NAMESPACE, art_type.value, name)
            except ValueError:
                logger.warning("Invalid artifact name %s in %s, skipping", name, subdir)
                continue

            artifacts.append(
                {
                    "fqn": fqn,
                    "type": art_type.value,
                    "namespace": _LOCAL_NAMESPACE,
                    "name": name,
                    "content": content,
                    "source": ArtifactSource.LOCAL,
                    "path": str(path),
                }
            )

    return artifacts


def load_local_artifacts(
    db: sqlite3.Connection,
    data_dir: Path,
    *,
    project_dir: Path | None = None,
) -> int:
    """Discover and upsert local artifacts from global and project directories.

    Returns the number of artifacts loaded.
    """
    count = 0

    # Global local artifacts: ~/.anteroom/local/
    global_local = data_dir / _LOCAL_DIR
    for art in discover_local_artifacts(global_local):
        upsert_artifact(
            db,
            fqn=art["fqn"],
            artifact_type=art["type"],
            namespace=art["namespace"],
            name=art["name"],
            content=art["content"],
            source=ArtifactSource.LOCAL,
        )
        count += 1

    # Project local artifacts: .anteroom/local/
    if project_dir is not None:
        project_local = project_dir / _ANTEROOM_DIR / _LOCAL_DIR
        for art in discover_local_artifacts(project_local):
            upsert_artifact(
                db,
                fqn=art["fqn"],
                artifact_type=art["type"],
                namespace=art["namespace"],
                name=art["name"],
                content=art["content"],
                source=ArtifactSource.LOCAL,
            )
            count += 1

    if count:
        logger.info("Loaded %d local artifact(s)", count)
    return count


_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def scaffold_local_artifact(
    artifact_type: str,
    name: str,
    data_dir: Path,
    *,
    project: bool = False,
    project_dir: Path | None = None,
) -> Path:
    """Create a template local artifact file.

    Returns the path to the created file.
    Raises ``ValueError`` if the type is invalid or the file already exists.
    """
    if not _SAFE_NAME_RE.match(name) or ".." in name:
        msg = f"Invalid artifact name: {name!r}. Use alphanumeric, hyphens, underscores, dots only."
        raise ValueError(msg)

    try:
        art_type = ArtifactType(artifact_type)
    except ValueError:
        valid = ", ".join(t.value for t in ArtifactType)
        msg = f"Invalid artifact type: {artifact_type!r}. Must be one of: {valid}"
        raise ValueError(msg)

    subdir_name = _TYPE_DIRS[art_type]

    if project:
        if project_dir is None:
            msg = "project_dir required when project=True"
            raise ValueError(msg)
        base = project_dir / _ANTEROOM_DIR / _LOCAL_DIR / subdir_name
    else:
        base = data_dir / _LOCAL_DIR / subdir_name

    ext = ".yaml" if art_type in (ArtifactType.SKILL, ArtifactType.MCP_SERVER, ArtifactType.CONFIG_OVERLAY) else ".md"
    path = base / f"{name}{ext}"

    if path.exists():
        msg = f"Artifact already exists: {path}"
        raise ValueError(msg)

    base.mkdir(parents=True, exist_ok=True)
    template = _get_template(art_type, name)
    path.write_text(template, encoding="utf-8")
    return path


def _read_content(path: Path, art_type: ArtifactType) -> str | None:
    """Read artifact content from a file."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot read %s: %s", path, e)
        return None

    if path.suffix in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(raw)
        if isinstance(data, dict) and "content" in data:
            return str(data["content"])

    return raw


def _get_template(art_type: ArtifactType, name: str) -> str:
    """Return a template for a new local artifact."""
    if art_type == ArtifactType.SKILL:
        return f"name: {name}\ndescription: TODO\ncontent: |\n  TODO: skill prompt here\n"
    if art_type in (ArtifactType.MCP_SERVER, ArtifactType.CONFIG_OVERLAY):
        return f"# {name}\n# TODO: add configuration\n"
    return f"# {name}\n\nTODO: add content here\n"
