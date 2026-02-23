"""Project-scoped configuration: discovery, loading, and merging.

Discovers project config files using walk-up from cwd, similar to
team config and ANTEROOM.md.  Project config is a YAML file that
overlays on top of the merged team+personal config.

Discovery order at each directory level:
  .anteroom/config.yaml > .claude/config.yaml > .parlor/config.yaml

The project config supports the same schema as personal config, with
the addition of a ``required`` section for declaring required keys.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .discovery import walk_up_for_file

logger = logging.getLogger(__name__)

_PROJECT_CONFIG_FILENAMES = (
    ".anteroom/config.yaml",
    ".claude/config.yaml",
    ".parlor/config.yaml",
)


def discover_project_config(
    start: str | Path | None = None,
) -> Path | None:
    """Walk up from *start* to find a project config file.

    Returns the resolved Path if found, else None.
    """
    return walk_up_for_file(_PROJECT_CONFIG_FILENAMES, start)


def load_project_config(
    path: Path,
    data_dir: Path | None = None,
    *,
    interactive: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load a project config file with trust verification.

    Returns ``(raw_dict, required_keys)`` where *required_keys* is
    a list of ``{"path": "...", "description": "..."}`` entries.

    If the file is untrusted or unreadable, returns ``({}, [])``.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cannot read project config %s: %s", path, exc)
        return {}, []

    # Trust verification (same pattern as team config)
    from .trust import check_trust, compute_content_hash, save_trust_decision

    content_hash = compute_content_hash(content)
    trust_key = str(path.resolve())
    status = check_trust(trust_key, content_hash, data_dir)

    if status == "untrusted":
        if interactive:
            answer = _prompt_trust(path, is_changed=False)
            if not answer:
                logger.info("User declined to trust project config %s", path)
                return {}, []
            save_trust_decision(trust_key, content_hash, recursive=False, data_dir=data_dir)
        else:
            logger.info("Skipping untrusted project config %s (non-interactive)", path)
            return {}, []
    elif status == "changed":
        if interactive:
            answer = _prompt_trust(path, is_changed=True)
            if not answer:
                logger.info("User declined changed project config %s", path)
                return {}, []
            save_trust_decision(trust_key, content_hash, recursive=False, data_dir=data_dir)
        else:
            logger.info("Skipping changed project config %s (non-interactive)", path)
            return {}, []

    try:
        raw = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        logger.warning("Invalid YAML in project config %s: %s", path, exc)
        return {}, []

    if not isinstance(raw, dict):
        logger.warning("Project config %s is not a YAML mapping", path)
        return {}, []

    # Validate before merging
    from .config_validator import validate_config

    validation = validate_config(raw)
    if validation.has_warnings:
        for w in validation.errors:
            if w.severity == "warning":
                logger.warning("Project config %s: %s — %s", path, w.path, w.message)

    # Extract required keys (project-specific feature)
    required = raw.pop("required", [])
    if not isinstance(required, list):
        required = []
    valid_required: list[dict[str, Any]] = []
    for entry in required:
        if isinstance(entry, dict) and "path" in entry:
            valid_required.append(
                {
                    "path": str(entry["path"]),
                    "description": str(entry.get("description", "")),
                }
            )
        else:
            logger.warning("Ignoring invalid required entry in project config: %r", entry)

    return raw, valid_required


def _prompt_trust(path: Path, *, is_changed: bool) -> bool:
    """Prompt the user to trust (or re-trust) a project config file."""
    import sys

    if not sys.stdin.isatty():
        return False

    if is_changed:
        msg = f"Project config file has changed: {path}\nTrust updated file? [y/N] "
    else:
        msg = f"Found project config file: {path}\nTrust this file? [y/N] "

    try:
        answer = input(msg).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")
