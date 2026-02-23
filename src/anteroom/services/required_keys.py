"""Required keys: validate and interactively prompt for missing config values.

Team and project configs can declare ``required`` keys — config paths that
must be present in the user's personal config.  This module checks for
missing values and prompts interactively when possible.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Keys containing these substrings get masked input
_SENSITIVE_SUBSTRINGS = ("key", "secret", "password", "token", "passphrase")


def _is_sensitive(path: str) -> bool:
    """Check if a config path likely contains a secret value."""
    lower = path.lower()
    return any(s in lower for s in _SENSITIVE_SUBSTRINGS)


def _resolve_dot_path(raw: dict[str, Any], dot_path: str) -> Any:
    """Resolve a dot-separated path to its value. Returns None if missing."""
    parts = dot_path.split(".")
    current: Any = raw
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_dot_path(target: dict[str, Any], dot_path: str, value: Any) -> None:
    """Set a value at a dot-separated path, creating intermediate dicts."""
    parts = dot_path.split(".")
    current = target
    for part in parts[:-1]:
        if part not in current or not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def check_required_keys(
    required: list[dict[str, Any]],
    personal_raw: dict[str, Any],
) -> list[dict[str, Any]]:
    """Check which required keys are missing from the personal config.

    Returns a list of missing required key entries (same format as input).
    """
    missing = []
    for entry in required:
        path = entry.get("path", "")
        if not path:
            continue
        val = _resolve_dot_path(personal_raw, path)
        # Also check env var override
        env_key = "AI_CHAT_" + path.upper().replace(".", "_")
        env_val = os.environ.get(env_key)
        if val is None and not env_val:
            missing.append(entry)
    return missing


def prompt_for_missing_keys(
    missing: list[dict[str, Any]],
    config_path: Path,
) -> bool:
    """Interactively prompt the user to fill in missing required keys.

    Updates the config file with the provided values.
    Returns True if all keys were filled, False if user cancelled.
    """
    if not sys.stdin.isatty():
        return False

    import getpass

    print("\n--- Required Configuration ---")
    print("The following config values are required but not set:\n")

    # Load current config
    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

    values_set = 0
    for entry in missing:
        path = entry["path"]
        description = entry.get("description", "")
        label = f"  {path}"
        if description:
            label += f" — {description}"
        print(label)

        try:
            if _is_sensitive(path):
                value = getpass.getpass("  Enter value (hidden): ")
            else:
                value = input("  Enter value: ")
        except (EOFError, KeyboardInterrupt):
            print("\n\nSetup cancelled.")
            return False

        value = value.strip()
        if not value:
            print("  (skipped)")
            continue

        _set_dot_path(raw, path, value)
        values_set += 1

    if values_set == 0:
        return False

    # Save updated config
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
    try:
        config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    print(f"\n  Updated {config_path} with {values_set} value(s).\n")
    return True


def format_missing_keys_error(missing: list[dict[str, Any]]) -> str:
    """Format an error message for missing required keys (non-interactive mode)."""
    lines = ["Missing required configuration values:"]
    for entry in missing:
        path = entry["path"]
        description = entry.get("description", "")
        env_key = "AI_CHAT_" + path.upper().replace(".", "_")
        line = f"  - {path}"
        if description:
            line += f": {description}"
        line += f" (set in config.yaml or {env_key})"
        lines.append(line)
    lines.append("\nRun 'aroom init' to set up required values interactively.")
    return "\n".join(lines)
