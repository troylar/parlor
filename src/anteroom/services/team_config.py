"""Team configuration: discovery, loading, merging, and enforcement.

Supports a shared team config file (same YAML schema as personal config)
with an ``enforce`` list of dot-paths that cannot be overridden by personal
config, environment variables, or CLI flags.

Discovery priority:
  1. Explicit path (CLI ``--team-config`` flag)
  2. ``AI_CHAT_TEAM_CONFIG`` environment variable
  3. ``team_config_path`` field in personal config YAML
  4. Walk-up from cwd: ``.anteroom/team.yaml`` or ``anteroom.team.yaml``
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Enforce dot-path validation: only lowercase identifiers separated by dots, max 4 segments.
_SAFE_DOT_PATH = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*){0,3}$")
_MAX_ENFORCE_DEPTH = 4

_TEAM_DIR_FILENAMES = (".anteroom/team.yaml", ".claude/team.yaml")
_TEAM_FLAT_FILENAME = "anteroom.team.yaml"


def discover_team_config(
    *,
    cli_path: str | Path | None = None,
    env_path: str | None = None,
    personal_path: str | None = None,
    cwd: str | Path | None = None,
) -> Path | None:
    """Resolve team config file path using 4-method priority.

    Returns the resolved Path if a readable file is found, else None.
    """
    # 1. Explicit CLI path
    if cli_path:
        p = Path(cli_path).expanduser().resolve()
        if p.is_file():
            return p
        logger.warning("Team config path from --team-config does not exist: %s", p)
        return None

    # 2. Environment variable
    if env_path:
        p = Path(env_path).expanduser().resolve()
        try:
            if p.is_file():
                return p
        except OSError:
            pass
        logger.warning("Team config path from AI_CHAT_TEAM_CONFIG does not exist: %s", p)
        return None

    # 3. Personal config field
    if personal_path:
        p = Path(personal_path).expanduser().resolve()
        try:
            if p.is_file():
                return p
        except OSError:
            pass
        logger.warning("Team config path from personal config does not exist: %s", p)
        return None

    # 4. Walk-up from cwd
    return _walk_up_for_team_config(cwd)


def _walk_up_for_team_config(start: str | Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) looking for a team config file.

    At each directory level checks:
      - ``.anteroom/team.yaml``
      - ``anteroom.team.yaml``

    Returns the first match or None.
    """
    current = Path(start or os.getcwd()).resolve()
    home = Path.home().resolve()
    while True:
        for relative in (*_TEAM_DIR_FILENAMES, _TEAM_FLAT_FILENAME):
            candidate = current / relative
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        # Stop at home directory — don't walk above it.
        if current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def load_team_config(
    path: Path,
    data_dir: Path | None = None,
    *,
    interactive: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Load and trust-verify a team config file.

    Returns ``(raw_dict, enforce_list)``.  If the file is untrusted or
    unreadable, returns ``({}, [])``.

    Parameters
    ----------
    path:
        Resolved path to the team YAML file.
    data_dir:
        Anteroom data directory (for trust store).  ``None`` = auto-resolve.
    interactive:
        If True, prompt the user on first encounter / hash change (CLI mode).
        If False, silently skip untrusted files (web UI / non-interactive).
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cannot read team config %s: %s", path, exc)
        return {}, []

    # Trust verification
    from .trust import check_trust, compute_content_hash, save_trust_decision

    content_hash = compute_content_hash(content)
    trust_key = str(path.resolve())
    status = check_trust(trust_key, content_hash, data_dir)

    if status == "untrusted":
        if interactive:
            answer = _prompt_trust(path, is_changed=False)
            if not answer:
                logger.info("User declined to trust team config %s", path)
                return {}, []
            save_trust_decision(trust_key, content_hash, recursive=False, data_dir=data_dir)
        else:
            logger.info("Skipping untrusted team config %s (non-interactive)", path)
            return {}, []
    elif status == "changed":
        if interactive:
            answer = _prompt_trust(path, is_changed=True)
            if not answer:
                logger.info("User declined changed team config %s", path)
                return {}, []
            save_trust_decision(trust_key, content_hash, recursive=False, data_dir=data_dir)
        else:
            logger.info("Skipping changed team config %s (non-interactive)", path)
            return {}, []

    try:
        raw = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        logger.warning("Invalid YAML in team config %s: %s", path, exc)
        return {}, []

    if not isinstance(raw, dict):
        logger.warning("Team config %s is not a YAML mapping", path)
        return {}, []

    enforce = raw.pop("enforce", [])
    if not isinstance(enforce, list):
        enforce = []
    enforce = [str(e) for e in enforce if isinstance(e, str)]

    # Validate enforce dot-paths: reject anything that doesn't match safe pattern.
    valid_enforce: list[str] = []
    for dp in enforce:
        if not _SAFE_DOT_PATH.match(dp):
            logger.warning("Ignoring invalid enforce dot-path in team config: %r", dp)
            continue
        valid_enforce.append(dp)

    return raw, valid_enforce


def _prompt_trust(path: Path, *, is_changed: bool) -> bool:
    """Prompt the user to trust (or re-trust) a team config file."""
    import sys

    if not sys.stdin.isatty():
        return False

    if is_changed:
        msg = f"Team config file has changed: {path}\nTrust updated file? [y/N] "
    else:
        msg = f"Found team config file: {path}\nTrust this file? [y/N] "

    try:
        answer = input(msg).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def _is_named_list(value: Any) -> bool:
    """Return True if *value* is a list of dicts that all contain a ``name`` key.

    Named lists get special merge treatment: items are matched by their
    ``name`` field and merged individually, rather than the overlay list
    replacing the base list wholesale.

    Examples of named lists (merged by name)::

        mcp_servers:
          - name: my-server
            command: uvx my-server

        shared_databases:
          - name: team-db
            path: /shared/team.db

    Examples of plain lists (overlay replaces base)::

        safety:
          denied_tools: ["bash", "rm"]
        args: ["--verbose", "--port", "8080"]
    """
    if not isinstance(value, list) or len(value) == 0:
        return False
    return all(isinstance(item, dict) and "name" in item for item in value)


def _merge_named_lists(base_list: list[dict[str, Any]], overlay_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge two named lists by matching items on their ``name`` field.

    Merge rules:

    1. **Matching names**: When both lists contain an item with the same
       ``name``, the overlay item's fields are merged into the base item.
       Overlay fields win for scalars and plain lists; nested dicts are
       merged recursively via ``deep_merge``.

    2. **Base-only names**: Items in the base list with no matching overlay
       item are kept unchanged.

    3. **Overlay-only names**: Items in the overlay list with no matching
       base item are appended to the result.

    4. **Order**: Base items appear first (in original order), followed by
       any overlay-only items (in overlay order).

    This enables a common team-config pattern where the team defines MCP
    servers with shared settings, and individual users overlay just the
    fields they need (e.g. env vars with API tokens)::

        # team.yaml (base)
        mcp_servers:
          - name: github
            transport: stdio
            command: uvx mcp-server-github

        # personal config (overlay)
        mcp_servers:
          - name: github
            env:
              GITHUB_TOKEN: "${GITHUB_TOKEN}"

        # result: merged — command from team, env from personal
        mcp_servers:
          - name: github
            transport: stdio
            command: uvx mcp-server-github
            env:
              GITHUB_TOKEN: "${GITHUB_TOKEN}"
    """
    base_by_name: dict[str, dict[str, Any]] = {}
    base_order: list[str] = []
    for item in base_list:
        name = item["name"]
        base_by_name[name] = item
        base_order.append(name)

    overlay_by_name: dict[str, dict[str, Any]] = {}
    overlay_order: list[str] = []
    for item in overlay_list:
        name = item["name"]
        overlay_by_name[name] = item
        overlay_order.append(name)

    result: list[dict[str, Any]] = []

    # Base items first (preserving base order), merged with overlay if present
    for name in base_order:
        base_item = base_by_name[name]
        if name in overlay_by_name:
            result.append(deep_merge(base_item, overlay_by_name[name]))
        else:
            result.append(dict(base_item))

    # Overlay-only items appended at the end
    for name in overlay_order:
        if name not in base_by_name:
            result.append(dict(overlay_by_name[name]))

    return result


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *overlay* into *base*.

    Merge strategy by value type:

    - **Dicts** are merged recursively (overlay keys win for scalars).
    - **Named lists** (lists of dicts where every item has a ``name`` key)
      are merged by matching on the ``name`` field. This allows personal
      config to overlay specific fields onto team-defined items without
      replacing the entire list. See :func:`_merge_named_lists` for details.
    - **Plain lists** (strings, numbers, or mixed items) are replaced
      wholesale — the overlay list completely replaces the base list.
    - **Scalars** in overlay overwrite base.

    Returns a new dict; neither input is mutated.
    """
    result = dict(base)
    for key, overlay_val in overlay.items():
        base_val = result.get(key)
        if isinstance(base_val, dict) and isinstance(overlay_val, dict):
            result[key] = deep_merge(base_val, overlay_val)
        elif _is_named_list(base_val) and _is_named_list(overlay_val):
            result[key] = _merge_named_lists(base_val, overlay_val)  # type: ignore[arg-type]
        else:
            result[key] = overlay_val
    return result


def _resolve_dot_path(raw: dict[str, Any], dot_path: str) -> Any:
    """Resolve a dot-separated path (e.g. ``ai.base_url``) to its value.

    Returns the value, or a sentinel ``_MISSING`` if the path is invalid.
    """
    parts = dot_path.split(".")
    current: Any = raw
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _set_dot_path(target: dict[str, Any], dot_path: str, value: Any) -> bool:
    """Set a value at a dot-separated path in a nested dict.

    Creates intermediate dicts as needed.  Returns True on success.
    """
    parts = dot_path.split(".")
    current = target
    for part in parts[:-1]:
        if part not in current or not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value
    return True


class _MissingSentinel:
    """Sentinel for missing dot-path resolution."""

    def __repr__(self) -> str:
        return "<MISSING>"


_MISSING = _MissingSentinel()


def apply_enforcement(
    raw: dict[str, Any],
    team_raw: dict[str, Any],
    enforced_fields: list[str],
) -> dict[str, Any]:
    """Re-apply enforced team config values onto the merged raw dict.

    This is called *after* personal config has been merged on top of team
    config, to force enforced fields back to the team-specified values.

    Returns the modified *raw* dict (also modifies in place).
    """
    for dot_path in enforced_fields:
        if not _SAFE_DOT_PATH.match(dot_path):
            logger.warning("Skipping invalid enforce dot-path: %r", dot_path)
            continue
        team_val = _resolve_dot_path(team_raw, dot_path)
        if isinstance(team_val, _MissingSentinel):
            logger.warning("Enforced field '%s' not found in team config; skipping", dot_path)
            continue
        current_val = _resolve_dot_path(raw, dot_path)
        if not isinstance(current_val, _MissingSentinel) and current_val != team_val:
            logger.info("Enforcing team config: %s (overriding personal value)", dot_path)
        _set_dot_path(raw, dot_path, team_val)
    return raw
