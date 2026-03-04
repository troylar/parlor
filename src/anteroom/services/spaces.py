"""Space file parser and manager.

Spaces are YAML-based workspace definitions that can live in two locations:

- **Local**: ``.anteroom/space.yaml`` inside a directory.
  Auto-discovered when you ``cd`` into the directory and run ``aroom chat``.
- **Global**: ``~/.anteroom/spaces/<name>.yaml``. Available from any directory.

``space init`` creates a local space file in the current directory.
``space create`` creates a global space.  ``space list`` shows origin
(local/global) and which space is active.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .pack_sources import _validate_url_scheme

logger = logging.getLogger(__name__)

_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_MAX_FILE_SIZE = 256 * 1024  # 256 KB


@dataclass(frozen=True)
class SpacePackSource:
    url: str
    branch: str = "main"


@dataclass(frozen=True)
class SpaceSource:
    path: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class SpaceConfig:
    name: str
    version: str = "1"
    repos: list[str] = field(default_factory=list)
    pack_sources: list[SpacePackSource] = field(default_factory=list)
    packs: list[str] = field(default_factory=list)
    sources: list[SpaceSource] = field(default_factory=list)
    instructions: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpaceLocalConfig:
    repos_root: str = ""
    paths: dict[str, str] = field(default_factory=dict)


def get_spaces_dir() -> Path:
    return Path.home() / ".anteroom" / "spaces"


def list_space_files() -> list[Path]:
    d = get_spaces_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.yaml") if not p.name.endswith(".local.yaml"))


def compute_file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_local_path(space_path: Path) -> Path | None:
    local = space_path.with_suffix("").with_suffix(".local.yaml")
    return local if local.is_file() else None


def parse_space_file(path: Path) -> SpaceConfig:
    if not path.is_file():
        raise FileNotFoundError(f"Space file not found: {path}")
    if path.stat().st_size > _MAX_FILE_SIZE:
        raise ValueError(f"Space file exceeds {_MAX_FILE_SIZE // 1024}KB limit: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Space file must be a YAML mapping: {path}")

    name = raw.get("name", "")
    if not name or not _NAME_PATTERN.match(name):
        raise ValueError(f"Invalid space name: {name!r}")

    pack_sources = []
    for ps in raw.get("pack_sources", []):
        if isinstance(ps, str):
            pack_sources.append(SpacePackSource(url=ps))
        elif isinstance(ps, dict):
            pack_sources.append(SpacePackSource(url=ps["url"], branch=ps.get("branch", "main")))

    sources = []
    for src in raw.get("sources", []):
        if isinstance(src, str):
            sources.append(SpaceSource(path=src))
        elif isinstance(src, dict):
            sources.append(SpaceSource(path=src.get("path"), url=src.get("url")))

    return SpaceConfig(
        name=name,
        version=str(raw.get("version", "1")),
        repos=list(raw.get("repos", [])),
        pack_sources=pack_sources,
        packs=list(raw.get("packs", [])),
        sources=sources,
        instructions=raw.get("instructions", ""),
        config=raw.get("config", {}),
    )


def parse_local_file(path: Path) -> SpaceLocalConfig:
    if not path.is_file():
        raise FileNotFoundError(f"Local config file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Local config must be a YAML mapping: {path}")

    return SpaceLocalConfig(
        repos_root=raw.get("repos_root", ""),
        paths=dict(raw.get("paths", {})),
    )


def write_space_file(path: Path, config: SpaceConfig) -> None:
    data: dict[str, Any] = {"name": config.name, "version": config.version}
    if config.repos:
        data["repos"] = config.repos
    if config.pack_sources:
        data["pack_sources"] = [{"url": ps.url, "branch": ps.branch} for ps in config.pack_sources]
    if config.packs:
        data["packs"] = config.packs
    if config.sources:
        data["sources"] = [{k: v for k, v in [("path", s.path), ("url", s.url)] if v} for s in config.sources]
    if config.instructions:
        data["instructions"] = config.instructions
    if config.config:
        data["config"] = config.config

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def write_local_file(path: Path, local: SpaceLocalConfig) -> None:
    data: dict[str, Any] = {}
    if local.repos_root:
        data["repos_root"] = local.repos_root
    if local.paths:
        data["paths"] = local.paths

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def get_space_config_overlay(space_file_path: Path) -> dict[str, Any]:
    """Extract the ``config`` overlay dict from a space file.

    Returns an empty dict if the file doesn't exist, is invalid, or has no
    ``config`` section.
    """
    try:
        cfg = parse_space_file(space_file_path)
        return dict(cfg.config) if cfg.config else {}
    except Exception:
        logger.warning("Could not read space config overlay from %s", space_file_path)
        return {}


def is_local_space(file_path: str) -> bool:
    """Return ``True`` if *file_path* is NOT inside the global spaces directory."""
    try:
        resolved = Path(file_path).expanduser().resolve()
        global_dir = get_spaces_dir().resolve()
        return not resolved.is_relative_to(global_dir)
    except Exception:
        return True


def slugify_dir_name(name: str) -> str:
    """Convert a directory name to a valid space name.

    Strips leading dots/hyphens, replaces disallowed characters with hyphens,
    collapses runs, and truncates to 64 characters.  Returns an empty string
    if nothing useful remains.
    """
    s = name.lstrip(".-")
    s = re.sub(r"[^a-zA-Z0-9_-]", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:64] if _NAME_PATTERN.match(s[:64] if s else "") else ""


_SPACE_TEMPLATE = """\
# Anteroom Space: {name}
# This file configures an AI workspace for this directory.
#
# When you run `aroom chat` from this directory, this space activates
# automatically — your tools, instructions, and config apply instantly.
#
# Docs: https://github.com/troylar/anteroom/blob/main/docs/spaces.md

name: {name}
version: "1"

# ---------------------------------------------------------------------------
# Instructions: free-form guidance the AI receives at the start of each chat.
# ---------------------------------------------------------------------------
# instructions: |
#   This is a Python FastAPI project.  Use pytest for tests.
#   Always run `ruff check` before committing.

# ---------------------------------------------------------------------------
# Repos: git repositories to clone when bootstrapping this space.
# ---------------------------------------------------------------------------
# repos:
#   - https://github.com/org/shared-context.git

# ---------------------------------------------------------------------------
# Packs: pre-built bundles of skills, rules, and instructions to install.
# ---------------------------------------------------------------------------
# packs:
#   - namespace/pack-name

# ---------------------------------------------------------------------------
# Pack sources: git repos that contain pack definitions (for private packs).
# ---------------------------------------------------------------------------
# pack_sources:
#   - url: https://github.com/org/anteroom-packs.git
#     branch: main

# ---------------------------------------------------------------------------
# Sources: files or URLs to load into the knowledge base.
# ---------------------------------------------------------------------------
# sources:
#   - path: docs/architecture.md
#   - url: https://example.com/api-reference

# ---------------------------------------------------------------------------
# Config: override any anteroom setting for this workspace.
# ---------------------------------------------------------------------------
# config:
#   model: gpt-4o
#   approval_mode: ask_for_writes
#   temperature: 0.7
"""


def write_space_template(path: Path, name: str) -> None:
    """Write a self-documenting space YAML template to *path*.

    The template contains commented-out examples for every section so a new
    user can understand what's possible without consulting external docs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SPACE_TEMPLATE.replace("{name}", name), encoding="utf-8")


def validate_space(config: SpaceConfig) -> list[str]:
    errors: list[str] = []

    if not _NAME_PATTERN.match(config.name):
        errors.append(f"Invalid space name: {config.name!r}")

    for repo in config.repos:
        err = _validate_url_scheme(repo)
        if err:
            errors.append(f"repos: {err}")

    for ps in config.pack_sources:
        err = _validate_url_scheme(ps.url)
        if err:
            errors.append(f"pack_sources: {err}")

    for src in config.sources:
        if src.path:
            if ".." in src.path.split("/"):
                errors.append(f"sources: path traversal not allowed: {src.path}")
        if src.url:
            err = _validate_url_scheme(src.url)
            if err:
                errors.append(f"sources: {err}")

    return errors


# ---------------------------------------------------------------------------
# Sync & export — DB-authoritative with YAML as sync source
# ---------------------------------------------------------------------------


def sync_space_from_file(
    db: Any,
    path: Path,
    *,
    track_source: bool = True,
) -> dict[str, Any]:
    """Parse a space YAML file and create-or-update the corresponding DB record.

    If *track_source* is ``True``, ``source_file`` and ``source_hash`` are set
    so that future syncs can detect file changes.  Set to ``False`` for a
    one-shot import with no ongoing file tracking.

    Returns the space dict (newly created or updated).
    """
    from . import space_storage

    config = parse_space_file(path)
    errors = validate_space(config)
    if errors:
        raise ValueError(f"Invalid space file: {'; '.join(errors)}")

    fhash = compute_file_hash(path)
    source_file = str(path.resolve()) if track_source else ""
    source_hash = fhash if track_source else ""

    existing = space_storage.get_space_by_name(db, config.name)
    if existing:
        if existing.get("source_hash") == fhash and existing.get("source_file"):
            return existing
        updates: dict[str, Any] = {
            "instructions": config.instructions or "",
            "source_hash": source_hash,
            "last_loaded_at": datetime.now(timezone.utc).isoformat(),
        }
        if track_source:
            updates["source_file"] = source_file
        # Always sync the model: set from YAML or clear if removed
        updates["model"] = config.config.get("model") or None
        return space_storage.update_space(db, existing["id"], **updates) or existing
    else:
        model = config.config.get("model") if config.config else None
        return space_storage.create_space(
            db,
            config.name,
            source_file=source_file,
            source_hash=source_hash,
            instructions=config.instructions or "",
            model=model,
        )


def export_space_to_yaml(db: Any, space_id: str) -> SpaceConfig:
    """Build a ``SpaceConfig`` from the DB record for *space_id*.

    Raises ``ValueError`` if the space does not exist.  The returned config
    can be written to disk with ``write_space_file()``.
    """
    from . import space_storage

    space = space_storage.get_space(db, space_id)
    if not space:
        raise ValueError(f"Space not found: {space_id}")

    config_dict: dict[str, Any] = {}
    if space.get("model"):
        config_dict["model"] = space["model"]

    return SpaceConfig(
        name=space["name"],
        instructions=space.get("instructions", ""),
        config=config_dict,
    )
