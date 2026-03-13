"""Scoped config editing: inspect and modify configuration fields with scope selection.

Provides a shared service layer for both CLI ``/config`` and web API config
endpoints.  Supports reading field values with full precedence-chain attribution
and writing to personal, space, or project scopes.

Layer precedence (highest wins)::

    env var > project > space > personal > pack > team > default

Team-enforced fields cannot be overridden from any writable scope.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config_overlays import flatten_to_dot_paths

logger = logging.getLogger(__name__)

_SAFE_DOT_PATH = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*){0,3}$")

# Fields that must never be exposed or written via the web API.
_SENSITIVE_FIELDS = frozenset(
    {
        "ai.api_key",
        "ai.api_key_command",
        "embeddings.api_key",
        "embeddings.api_key_command",
        "identity.private_key",
        "identity.public_key",
        "identity.user_id",
        "storage.encryption_kdf",
    }
)

# Source layer names, ordered lowest to highest precedence.
LAYER_ORDER = ("default", "team", "pack", "personal", "space", "project", "env var")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigFieldInfo:
    """Schema for a single settable config field."""

    dot_path: str
    field_type: str  # "str", "int", "float", "bool", "enum", "list"
    default: Any = None
    allowed_values: tuple[str, ...] | None = None
    min_val: int | float | None = None
    max_val: int | float | None = None


@dataclass(frozen=True)
class ConfigFieldValue:
    """Result of reading a single config field."""

    dot_path: str
    effective_value: Any
    source_layer: str  # one of LAYER_ORDER or "team (enforced)"
    is_enforced: bool
    field_info: ConfigFieldInfo | None = None
    layer_values: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


def list_settable_fields(*, include_sensitive: bool = False) -> list[ConfigFieldInfo]:
    """Build the list of settable fields from the config validator registries.

    Excludes sensitive fields (API keys, crypto) by default.
    """
    from .config_validator import _ENUM_FIELDS, _FLOAT_FIELDS, _INT_FIELDS, _KNOWN_KEYS

    # Build a map of (section.key) -> ConfigFieldInfo
    fields: dict[str, ConfigFieldInfo] = {}

    # Gather all known keys as string fields first
    for section_path, keys in _KNOWN_KEYS.items():
        for key in sorted(keys):
            dot_path = f"{section_path}.{key}"
            if not include_sensitive and dot_path in _SENSITIVE_FIELDS:
                continue
            fields[dot_path] = ConfigFieldInfo(dot_path=dot_path, field_type="str")

    # Override with typed info for int fields
    for section_path, key, lo, hi, default in _INT_FIELDS:
        dot_path = f"{section_path}.{key}"
        if not include_sensitive and dot_path in _SENSITIVE_FIELDS:
            continue
        fields[dot_path] = ConfigFieldInfo(
            dot_path=dot_path,
            field_type="int",
            default=default,
            min_val=lo,
            max_val=hi,
        )

    # Override with typed info for float fields
    for section_path, key, flo, fhi, fdefault in _FLOAT_FIELDS:
        dot_path = f"{section_path}.{key}"
        if not include_sensitive and dot_path in _SENSITIVE_FIELDS:
            continue
        fields[dot_path] = ConfigFieldInfo(
            dot_path=dot_path,
            field_type="float",
            default=fdefault,
            min_val=flo,
            max_val=fhi,
        )

    # Override with typed info for enum fields
    for section_path, key, allowed in _ENUM_FIELDS:
        dot_path = f"{section_path}.{key}"
        if not include_sensitive and dot_path in _SENSITIVE_FIELDS:
            continue
        fields[dot_path] = ConfigFieldInfo(
            dot_path=dot_path,
            field_type="enum",
            allowed_values=tuple(sorted(allowed)),
        )

    # Identify bool fields from the validator's inline list
    bool_field_paths = [
        ("ai", "verify_ssl"),
        ("ai", "block_localhost_api"),
        ("app", "tls"),
        ("cli", "builtin_tools"),
        ("cli", "tool_dedup"),
        ("cli.planning", "enabled"),
        ("embeddings", "enabled"),
        ("safety", "enabled"),
        ("safety", "read_only"),
        ("proxy", "enabled"),
        ("storage", "purge_attachments"),
        ("storage", "purge_embeddings"),
        ("storage", "encrypt_at_rest"),
        ("safety.prompt_injection", "enabled"),
        ("safety.prompt_injection", "detect_encoding_attacks"),
        ("safety.prompt_injection", "detect_instruction_override"),
        ("safety.prompt_injection", "log_detections"),
        ("safety.dlp", "enabled"),
        ("safety.dlp", "scan_output"),
        ("safety.dlp", "scan_input"),
        ("safety.dlp", "log_detections"),
        ("safety.output_filter", "enabled"),
        ("safety.output_filter", "system_prompt_leak_detection"),
        ("safety.output_filter", "log_detections"),
        ("rag", "enabled"),
        ("rag", "include_sources"),
        ("rag", "include_conversations"),
        ("rag", "exclude_current"),
        ("reranker", "enabled"),
        ("codebase_index", "enabled"),
        ("session", "log_session_events"),
        ("audit", "enabled"),
        ("audit", "redact_content"),
    ]
    for section_path, key in bool_field_paths:
        dot_path = f"{section_path}.{key}"
        if dot_path in fields:
            fields[dot_path] = ConfigFieldInfo(dot_path=dot_path, field_type="bool")

    # Identify list fields
    list_field_paths = [
        ("safety", "custom_patterns"),
        ("safety", "sensitive_paths"),
        ("safety", "allowed_tools"),
        ("safety", "denied_tools"),
        ("proxy", "allowed_origins"),
        ("ai", "allowed_domains"),
        ("session", "allowed_ips"),
    ]
    for section_path, key in list_field_paths:
        dot_path = f"{section_path}.{key}"
        if dot_path in fields:
            fields[dot_path] = ConfigFieldInfo(dot_path=dot_path, field_type="list")

    return sorted(fields.values(), key=lambda f: f.dot_path)


# ---------------------------------------------------------------------------
# Field reading
# ---------------------------------------------------------------------------


def build_full_source_map(
    *,
    team_raw: dict[str, Any] | None = None,
    pack_raw: dict[str, Any] | None = None,
    personal_raw: dict[str, Any] | None = None,
    space_raw: dict[str, Any] | None = None,
    project_raw: dict[str, Any] | None = None,
    env_overrides: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build a ``{dot.path: layer_name}`` map covering all 7 precedence layers.

    Later layers overwrite earlier ones, matching the actual merge precedence:
    team < pack < personal < space < project < env var.
    """
    layers: list[tuple[str, dict[str, Any]]] = []
    if team_raw:
        layers.append(("team", team_raw))
    if pack_raw:
        layers.append(("pack", pack_raw))
    if personal_raw:
        layers.append(("personal", personal_raw))
    if space_raw:
        layers.append(("space", space_raw))
    if project_raw:
        layers.append(("project", project_raw))
    if env_overrides:
        layers.append(("env var", env_overrides))

    sources: dict[str, str] = {}
    for layer_name, raw in layers:
        for dot_path in flatten_to_dot_paths(raw):
            sources[dot_path] = layer_name
    return sources


def get_field(
    config: Any,
    dot_path: str,
    source_map: dict[str, str],
    enforced_fields: list[str],
    *,
    layer_raws: dict[str, dict[str, Any]] | None = None,
) -> ConfigFieldValue:
    """Read a single config field with full source attribution.

    Parameters
    ----------
    config:
        The final merged ``AppConfig`` object.
    dot_path:
        The dot-separated field path (e.g. ``"ai.model"``).
    source_map:
        Output of :func:`build_full_source_map`.
    enforced_fields:
        List of dot-paths enforced by team config.
    layer_raws:
        Optional dict of ``{layer_name: raw_dict}`` for per-layer breakdown.
    """
    if not _SAFE_DOT_PATH.match(dot_path):
        raise ValueError(f"Invalid config path: {dot_path!r}")

    flat = flatten_to_dot_paths(asdict(config))
    effective = flat.get(dot_path)
    is_enforced = dot_path in enforced_fields
    source = "team (enforced)" if is_enforced else source_map.get(dot_path, "default")

    # Build per-layer breakdown
    layer_values: dict[str, Any] = {}
    if layer_raws:
        for layer_name, raw in layer_raws.items():
            layer_flat = flatten_to_dot_paths(raw)
            if dot_path in layer_flat:
                layer_values[layer_name] = layer_flat[dot_path]

    # Look up field info
    field_info = _find_field_info(dot_path)

    return ConfigFieldValue(
        dot_path=dot_path,
        effective_value=effective,
        source_layer=source,
        is_enforced=is_enforced,
        field_info=field_info,
        layer_values=layer_values,
    )


def _find_field_info(dot_path: str) -> ConfigFieldInfo | None:
    """Find the ConfigFieldInfo for a dot-path, or None if unknown."""
    for fi in list_settable_fields(include_sensitive=True):
        if fi.dot_path == dot_path:
            return fi
    return None


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------


def validate_field_value(dot_path: str, value: str) -> tuple[Any, list[str]]:
    """Parse and validate a string value for a config field.

    Returns ``(parsed_value, errors)`` where *errors* is empty on success.
    """
    if not _SAFE_DOT_PATH.match(dot_path):
        return None, [f"Invalid config path: {dot_path!r}"]

    info = _find_field_info(dot_path)
    if info is None:
        return value, []  # Unknown field — accept as string

    errors: list[str] = []

    if info.field_type == "int":
        try:
            parsed = int(value)
        except (ValueError, TypeError):
            return None, [f"Expected integer, got: {value!r}"]
        if info.min_val is not None and parsed < info.min_val:
            errors.append(f"Value {parsed} below minimum {info.min_val}")
        if info.max_val is not None and parsed > info.max_val:
            errors.append(f"Value {parsed} above maximum {info.max_val}")
        return parsed, errors

    if info.field_type == "float":
        try:
            parsed_f = float(value)
        except (ValueError, TypeError):
            return None, [f"Expected number, got: {value!r}"]
        if info.min_val is not None and parsed_f < info.min_val:
            errors.append(f"Value {parsed_f} below minimum {info.min_val}")
        if info.max_val is not None and parsed_f > info.max_val:
            errors.append(f"Value {parsed_f} above maximum {info.max_val}")
        return parsed_f, errors

    if info.field_type == "bool":
        lower = value.lower().strip()
        if lower in ("true", "1", "yes"):
            return True, []
        if lower in ("false", "0", "no"):
            return False, []
        return None, [f"Expected boolean (true/false/yes/no), got: {value!r}"]

    if info.field_type == "enum":
        lower = value.lower().strip()
        if info.allowed_values and lower not in info.allowed_values:
            return None, [f"Must be one of: {', '.join(info.allowed_values)}; got: {value!r}"]
        return lower, []

    if info.field_type == "list":
        # Accept comma-separated values
        items = [v.strip() for v in value.split(",") if v.strip()]
        return items, []

    # Default: string
    return value, []


# ---------------------------------------------------------------------------
# Write guards
# ---------------------------------------------------------------------------


def check_write_allowed(
    dot_path: str,
    enforced_fields: list[str],
    *,
    allow_sensitive: bool = False,
) -> tuple[bool, str | None]:
    """Check if a field can be written.

    Returns ``(allowed, reason)``.  *reason* is ``None`` when allowed.
    """
    if not _SAFE_DOT_PATH.match(dot_path):
        return False, f"Invalid config path: {dot_path!r}"

    if dot_path in enforced_fields:
        return False, f"'{dot_path}' is enforced by team config and cannot be changed"

    if not allow_sensitive and dot_path in _SENSITIVE_FIELDS:
        return False, f"'{dot_path}' is a sensitive field and cannot be changed via this interface"

    return True, None


# ---------------------------------------------------------------------------
# Scoped writes
# ---------------------------------------------------------------------------


def _set_nested(d: dict[str, Any], dot_path: str, value: Any) -> None:
    """Set a value in a nested dict by dot-path, creating intermediate dicts."""
    parts = dot_path.split(".")
    current = d
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _delete_nested(d: dict[str, Any], dot_path: str) -> bool:
    """Delete a key from a nested dict by dot-path.  Returns True if deleted."""
    parts = dot_path.split(".")
    current = d
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        return False
    del current[parts[-1]]
    return True


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file, returning empty dict if missing or invalid."""
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Cannot read config %s: %s", path, exc)
        return {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write a YAML file atomically with 0600 permissions.

    Writes to a temporary sibling file first, then atomically replaces the
    target.  This prevents config corruption if the process crashes mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)
    tmp = path.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        try:
            tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(str(tmp), str(path))
    except BaseException:
        # Clean up the temp file on any failure
        tmp.unlink(missing_ok=True)
        raise


def write_personal_field(dot_path: str, value: Any, config_path: Path | None = None) -> Path:
    """Write a field to the personal config at ``~/.anteroom/config.yaml``.

    Returns the path that was written to.
    """
    if not _SAFE_DOT_PATH.match(dot_path):
        raise ValueError(f"Invalid dot-path: {dot_path!r}")
    if config_path is None:
        from ..config import _get_config_path

        config_path = _get_config_path()

    raw = _read_yaml(config_path)
    _set_nested(raw, dot_path, value)
    _write_yaml(config_path, raw)
    return config_path


def write_space_field(
    dot_path: str,
    value: Any,
    space_yaml_path: Path,
    db: Any = None,
    space_id: str | None = None,
) -> Path:
    """Write a field to a space's ``config:`` block in its YAML file.

    Updates the trust hash and syncs the DB ``model`` column if the written
    field is ``model`` or ``ai.model``.

    Returns the space YAML path.
    """
    if not _SAFE_DOT_PATH.match(dot_path):
        raise ValueError(f"Invalid dot-path: {dot_path!r}")
    from .spaces import parse_space_file, write_space_file
    from .trust import compute_content_hash, save_trust_decision

    space_config = parse_space_file(space_yaml_path)

    # Modify the config dict
    new_config = dict(space_config.config) if space_config.config else {}
    _set_nested(new_config, dot_path, value)

    # Rebuild SpaceConfig with updated config
    from dataclasses import replace

    updated = replace(space_config, config=new_config)
    write_space_file(space_yaml_path, updated)

    # Update trust hash
    content = space_yaml_path.read_text(encoding="utf-8")
    content_hash = compute_content_hash(content)
    trust_key = str(space_yaml_path.resolve())
    save_trust_decision(trust_key, content_hash, recursive=False)

    # Sync DB model column if applicable
    if db is not None and space_id and dot_path in ("model", "ai.model"):
        model_value = value if isinstance(value, str) else str(value)
        try:
            from . import space_storage

            space_storage.update_space(db, space_id, model=model_value)
        except Exception:
            logger.warning("Failed to sync space DB model for %s", space_id)

    return space_yaml_path


def write_project_field(
    dot_path: str,
    value: Any,
    working_dir: str | Path | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Write a field to the project config, respecting discovery order.

    If a project config file already exists (even ``.claude/config.yaml``
    or ``.parlor/config.yaml``), writes to that file.  Otherwise creates
    ``.anteroom/config.yaml`` in the working directory.

    Updates the trust hash after writing.

    Returns the path that was written to.
    """
    if not _SAFE_DOT_PATH.match(dot_path):
        raise ValueError(f"Invalid dot-path: {dot_path!r}")
    from .project_config import discover_project_config
    from .trust import compute_content_hash, save_trust_decision

    existing = discover_project_config(working_dir)

    if existing:
        config_path = existing
    else:
        wd = Path(working_dir) if working_dir else Path.cwd()
        config_path = wd / ".anteroom" / "config.yaml"

    raw = _read_yaml(config_path)
    _set_nested(raw, dot_path, value)
    _write_yaml(config_path, raw)

    # Update trust hash
    content = config_path.read_text(encoding="utf-8")
    content_hash = compute_content_hash(content)
    trust_key = str(config_path.resolve())
    save_trust_decision(trust_key, content_hash, recursive=False, data_dir=data_dir)

    return config_path


# ---------------------------------------------------------------------------
# Scoped reset (delete a field from a scope)
# ---------------------------------------------------------------------------


def reset_personal_field(dot_path: str, config_path: Path | None = None) -> bool:
    """Remove a field from the personal config.  Returns True if the field existed."""
    if config_path is None:
        from ..config import _get_config_path

        config_path = _get_config_path()

    raw = _read_yaml(config_path)
    deleted = _delete_nested(raw, dot_path)
    if deleted:
        _write_yaml(config_path, raw)
    return deleted


def reset_space_field(
    dot_path: str,
    space_yaml_path: Path,
    db: Any = None,
    space_id: str | None = None,
) -> bool:
    """Remove a field from a space's config block.  Returns True if deleted."""
    from .spaces import parse_space_file, write_space_file
    from .trust import compute_content_hash, save_trust_decision

    space_config = parse_space_file(space_yaml_path)
    if not space_config.config:
        return False

    new_config = dict(space_config.config)
    deleted = _delete_nested(new_config, dot_path)
    if not deleted:
        return False

    from dataclasses import replace

    updated = replace(space_config, config=new_config)
    write_space_file(space_yaml_path, updated)

    content = space_yaml_path.read_text(encoding="utf-8")
    content_hash = compute_content_hash(content)
    trust_key = str(space_yaml_path.resolve())
    save_trust_decision(trust_key, content_hash, recursive=False)

    # Clear DB model if applicable
    if db is not None and space_id and dot_path in ("model", "ai.model"):
        try:
            from . import space_storage

            space_storage.update_space(db, space_id, model=None)
        except Exception:
            logger.warning("Failed to clear space DB model for %s", space_id)

    return True


def reset_project_field(
    dot_path: str,
    working_dir: str | Path | None = None,
    data_dir: Path | None = None,
) -> bool:
    """Remove a field from the project config.  Returns True if deleted."""
    from .project_config import discover_project_config
    from .trust import compute_content_hash, save_trust_decision

    existing = discover_project_config(working_dir)
    if not existing:
        return False

    raw = _read_yaml(existing)
    deleted = _delete_nested(raw, dot_path)
    if not deleted:
        return False

    _write_yaml(existing, raw)

    content = existing.read_text(encoding="utf-8")
    content_hash = compute_content_hash(content)
    trust_key = str(existing.resolve())
    save_trust_decision(trust_key, content_hash, recursive=False, data_dir=data_dir)

    return True


# ---------------------------------------------------------------------------
# In-memory config update
# ---------------------------------------------------------------------------


def apply_field_to_config(config: Any, dot_path: str, value: Any) -> None:
    """Update a field on a live ``AppConfig`` object in memory.

    Navigates the dataclass hierarchy by attribute access.  Raises
    ``AttributeError`` if the path is invalid.
    """
    parts = dot_path.split(".")
    obj = config
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


# ---------------------------------------------------------------------------
# Environment variable overrides (for source tracking)
# ---------------------------------------------------------------------------

_ENV_VAR_MAP: dict[str, str] = {
    "AI_CHAT_API_KEY": "ai.api_key",
    "AI_CHAT_BASE_URL": "ai.base_url",
    "AI_CHAT_MODEL": "ai.model",
    "AI_CHAT_SYSTEM_PROMPT": "ai.system_prompt",
    "AI_CHAT_HOST": "app.host",
    "AI_CHAT_PORT": "app.port",
    "AI_CHAT_DATA_DIR": "app.data_dir",
    "AI_CHAT_TLS": "app.tls",
    "AI_CHAT_APPROVAL_MODE": "safety.approval_mode",
    "AI_CHAT_READ_ONLY": "safety.read_only",
    "AI_CHAT_VERIFY_SSL": "ai.verify_ssl",
    "AI_CHAT_PROVIDER": "ai.provider",
    "AI_CHAT_MAX_OUTPUT_TOKENS": "ai.max_output_tokens",
}


def collect_env_overrides() -> dict[str, Any]:
    """Collect config overrides from environment variables.

    Returns a nested dict matching the config structure, suitable for
    passing to :func:`build_full_source_map`.
    """
    result: dict[str, Any] = {}
    for env_var, dot_path in _ENV_VAR_MAP.items():
        val = os.environ.get(env_var)
        if val is not None:
            _set_nested(result, dot_path, val)
    return result
