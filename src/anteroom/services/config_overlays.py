"""Pack config overlay collection, merging, and conflict detection.

Config overlays are YAML artifacts declared by packs that influence Anteroom's
configuration.  They are merged into the config precedence chain between team
config and personal config::

    defaults < team < **packs** < personal < space < project < env vars

Team-enforced fields always win (re-applied after every merge).

Priority model (config overlays only)
--------------------------------------

Each pack attachment has an integer ``priority`` (default 50).
**Lower number = higher precedence** (1 = highest, 100 = lowest).

When two packs set the same config key (dot-path):

- **Different priorities** — the lower-number pack wins.  No error.
- **Same priority** — this is an error.  The user must either change one
  pack's priority (``aroom pack attach --priority N``) or detach the
  conflicting pack.

Range guidance::

    1-19   high-priority   (compliance, security baselines)
    20-49  above-normal
    50     default
    51-80  below-normal
    81-100 low-priority    (fallback defaults, easily overridden)

Artifact conflict model
-----------------------

Non-config artifacts are categorized by merge behavior:

- **Additive** (``skill``, ``rule``, ``instruction``, ``context``,
  ``memory``, ``mcp_server``): same name from multiple packs is fine.
  Skills with colliding names are resolved via namespace-qualified
  display names (e.g. ``/team-a/deploy`` vs ``/team-b/deploy``).
  Rules add guidance, instructions add context, MCP server configs
  merge settings.

- **Config overlay**: uses the priority-based merge model above.

Overview
--------

The overlay lifecycle has four phases:

1. **Collection** — :func:`collect_pack_overlays` reads ``config_overlay``
   artifacts from the DB for a set of pack IDs.

2. **Conflict detection** — :func:`detect_overlay_conflicts` compares a
   new pack's overlays against already-attached packs.  Two packs may set
   the same dot-path only if they have different priorities; the lower
   priority number wins.  Same priority + same key is an error.

3. **Merging** — :func:`merge_pack_overlays` combines all overlay dicts
   sorted by priority (highest number first, so lower-number overlays
   are applied last and win via ``deep_merge`` semantics).

4. **Source tracking** — :func:`track_config_sources` builds a
   ``{dot.path: layer_name}`` map for the ``aroom config view --with-sources``
   transparency command.

Key design decisions
--------------------

- **Dot-path flattening** converts nested dicts to flat ``a.b.c`` keys for
  set-intersection conflict detection.  List values are treated as opaque
  leaves — two packs that both set a list key will conflict at the same
  priority, even if the lists contain different items.  This is intentional:
  list merging semantics (append? replace? named-list match?) are ambiguous,
  so we require each list-valued key to be owned by exactly one pack at a
  given priority level.

- **Intra-pack conflicts are allowed.**  A single pack may have multiple
  ``config_overlay`` artifacts that set the same key.  This lets pack
  authors compose overlays (e.g., a base overlay + an environment-specific
  override).  Only *cross-pack* conflicts at the same priority are errors.

- **Graceful degradation.**  :func:`collect_pack_overlays` catches
  ``sqlite3.OperationalError`` so it works against minimal DB schemas
  that lack the ``artifacts`` or ``pack_artifacts`` tables.

- **Team enforcement always wins.**  Even if a pack overlay sets an
  enforced field, :func:`~anteroom.services.team_config.apply_enforcement`
  re-applies the team value after every merge step.
  :func:`check_enforced_field_violations` is provided for pre-flight
  validation (e.g., warning the user before they install a pack that
  tries to override an enforced field).
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from ..db import ThreadSafeConnection

logger = logging.getLogger(__name__)


class ComplianceError(Exception):
    """Raised when a config rebuild fails compliance validation."""


# ---------------------------------------------------------------------------
# Dot-path flattening
# ---------------------------------------------------------------------------


def flatten_to_dot_paths(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict to ``{dot.path: leaf_value}`` pairs.

    Recursively walks *d*, concatenating keys with ``.`` separators.
    Non-dict values (including lists, ``None``, booleans, etc.) are treated
    as leaf values and stored as-is.

    Parameters
    ----------
    d:
        The nested dict to flatten.
    prefix:
        Internal parameter for recursive calls — the dot-path prefix
        accumulated so far.  Callers should not set this.

    Returns
    -------
    dict[str, Any]
        A flat dict where keys are dot-separated paths and values are the
        leaf values from *d*.

    Examples
    --------
    >>> flatten_to_dot_paths({"ai": {"model": "gpt-4", "temperature": 0.7}})
    {"ai.model": "gpt-4", "ai.temperature": 0.7}

    >>> flatten_to_dot_paths({"a": [1, 2]})
    {"a": [1, 2]}

    Note
    ----
    Keys containing literal dots are not escaped.  This is safe for
    Anteroom config because config keys never contain dots.  If that
    assumption changes, this function needs a quoting strategy.
    """
    items: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.update(flatten_to_dot_paths(v, key))
        else:
            items[key] = v
    return items


# ---------------------------------------------------------------------------
# Overlay collection from DB
# ---------------------------------------------------------------------------


def collect_pack_overlays(
    db: ThreadSafeConnection,
    pack_ids: list[str],
) -> list[tuple[str, dict[str, Any]]]:
    """Load ``config_overlay`` artifacts for the given pack IDs.

    Queries the ``artifacts`` table (joined through ``pack_artifacts``) for
    each pack ID.  Each artifact's YAML content is parsed and validated as
    a dict.  Malformed YAML and non-dict content are logged and skipped.

    Parameters
    ----------
    db:
        Thread-safe SQLite connection.
    pack_ids:
        Pack IDs to collect overlays for.  Order does not affect the
        result because each overlay is returned as an independent pair.

    Returns
    -------
    list[tuple[str, dict[str, Any]]]
        A list of ``(pack_label, overlay_dict)`` pairs where *pack_label*
        is ``namespace/name`` for human-readable conflict messages.

    Notes
    -----
    - Returns an empty list if *pack_ids* is empty.
    - Catches ``sqlite3.OperationalError`` so this works against minimal
      DB schemas that lack the ``artifacts`` or ``pack_artifacts`` tables
      (e.g., test fixtures, fresh installs before migration).
    - Uses N+1 queries (2 per pack ID).  Acceptable for the typical case
      of <10 attached packs.  If pack counts grow, batch with IN clause.
    """
    if not pack_ids:
        return []

    import sqlite3

    results: list[tuple[str, dict[str, Any]]] = []

    for pack_id in pack_ids:
        # Get pack info for labelling
        row = db.execute("SELECT namespace, name FROM packs WHERE id = ?", (pack_id,)).fetchone()
        if not row:
            continue
        ns = row[0] if isinstance(row, (tuple, list)) else row["namespace"]
        nm = row[1] if isinstance(row, (tuple, list)) else row["name"]
        label = f"{ns}/{nm}"

        # Get config_overlay artifacts linked to this pack
        try:
            art_rows = db.execute(
                "SELECT a.content FROM artifacts a "
                "JOIN pack_artifacts pa ON a.id = pa.artifact_id "
                "WHERE pa.pack_id = ? AND a.type = 'config_overlay'",
                (pack_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            # artifacts or pack_artifacts table may not exist in minimal DB setups
            continue

        for art_row in art_rows:
            content = art_row[0] if isinstance(art_row, (tuple, list)) else art_row["content"]
            if not content:
                continue
            try:
                parsed = yaml.safe_load(content)
            except yaml.YAMLError:
                logger.warning("Skipping malformed config_overlay YAML in pack %s", label)
                continue
            if not isinstance(parsed, dict):
                logger.warning("Config overlay in pack %s is not a dict; skipping", label)
                continue
            results.append((label, parsed))

    return results


# ---------------------------------------------------------------------------
# Overlay merging
# ---------------------------------------------------------------------------


def merge_pack_overlays(
    overlays: list[tuple[str, dict[str, Any]]],
    priorities: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Merge multiple pack overlays into a single config dict.

    Uses :func:`~anteroom.services.team_config.deep_merge` for recursive
    merging.  When *priorities* is provided, overlays are sorted so that
    **lower-priority-number packs win** (applied last in the merge chain,
    since ``deep_merge`` overlay values override base values).

    Parameters
    ----------
    overlays:
        List of ``(pack_label, overlay_dict)`` pairs as returned by
        :func:`collect_pack_overlays`.
    priorities:
        Optional ``{pack_label: priority_int}`` map from
        :func:`~anteroom.services.pack_attachments.get_attachment_priorities`.
        When provided, overlays are sorted by priority descending (highest
        number first) so that lower-number packs are applied last and win.
        When ``None``, overlays are merged in the order given (backward
        compatible with pre-priority code).

    Returns
    -------
    dict[str, Any]
        A single merged dict.  With priorities, the merge is deterministic:
        lower priority number wins for overlapping keys.  Without priorities,
        merge order is undefined for overlapping keys (use conflict
        detection to prevent this).
    """
    if not overlays:
        return {}

    from .team_config import deep_merge

    sorted_overlays = overlays
    if priorities is not None:
        # Sort by priority descending — highest number (lowest precedence) first.
        # deep_merge(base, overlay) → overlay wins, so the last overlay applied
        # (lowest priority number = highest precedence) wins.
        sorted_overlays = sorted(
            overlays,
            key=lambda pair: priorities.get(pair[0], 50),
            reverse=True,
        )

    merged: dict[str, Any] = {}
    for _label, overlay in sorted_overlays:
        merged = deep_merge(merged, overlay)
    return merged


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


def detect_overlay_conflicts(
    existing_overlays: list[tuple[str, dict[str, Any]]],
    new_overlay: tuple[str, dict[str, Any]],
    *,
    new_priority: int | None = None,
    existing_priorities: dict[str, int] | None = None,
) -> list[str]:
    """Find dot-paths where *new_overlay* conflicts with *existing_overlays*.

    Conflict policy (priority-aware):

    - If *new_priority* and *existing_priorities* are both provided,
      overlapping keys are only conflicts when the two packs share the
      same priority.  Different priorities are allowed — the lower number
      wins at merge time.
    - If priority info is not provided (backward compat), **any** overlap
      is a conflict ("forbid overlaps" — the pre-priority behavior).

    Parameters
    ----------
    existing_overlays:
        Overlays from already-attached packs (as returned by
        :func:`collect_pack_overlays`).
    new_overlay:
        The ``(pack_label, overlay_dict)`` pair for the pack being attached.
    new_priority:
        Priority of the pack being attached.  ``None`` falls back to
        strict "forbid overlaps" mode.
    existing_priorities:
        ``{pack_label: priority}`` map for already-attached packs, from
        :func:`~anteroom.services.pack_attachments.get_attachment_priorities`.
        ``None`` falls back to strict mode.

    Returns
    -------
    list[str]
        Human-readable conflict descriptions, one per conflicting dot-path.
        Empty list means no conflicts.

    Notes
    -----
    - Only detects **cross-pack** conflicts.  A single pack with multiple
      overlays that set the same key is allowed (intra-pack conflict).
    - List-valued keys are compared at the list level, not element-by-element.
      Two packs that both set ``mcp_servers`` will conflict at the same
      priority even if their lists contain different server entries.
    """
    new_label, new_dict = new_overlay
    new_paths = set(flatten_to_dot_paths(new_dict).keys())
    use_priorities = new_priority is not None and existing_priorities is not None

    conflicts: list[str] = []
    for existing_label, existing_dict in existing_overlays:
        existing_paths = set(flatten_to_dot_paths(existing_dict).keys())
        overlap = new_paths & existing_paths
        if not overlap:
            continue

        if use_priorities and existing_priorities is not None:
            existing_pri = existing_priorities.get(existing_label, 50)
            if existing_pri != new_priority:
                # Different priorities — lower number wins at merge time.
                # Not a conflict.
                continue

        for path in sorted(overlap):
            if use_priorities:
                conflicts.append(
                    f"'{path}' is set by both {existing_label} and {new_label} (both at priority {new_priority})"
                )
            else:
                conflicts.append(f"'{path}' is set by both {existing_label} and {new_label}")

    return conflicts


def check_enforced_field_violations(
    overlay: dict[str, Any],
    enforced_fields: list[str],
) -> list[str]:
    """Return enforced dot-paths that the overlay tries to set.

    Used for pre-flight validation — warn the user before they install a
    pack that tries to set a field the team admin has locked down.  The
    actual enforcement happens in
    :func:`~anteroom.services.team_config.apply_enforcement` after every
    merge step, so even if this check is skipped, the team value always wins.

    Parameters
    ----------
    overlay:
        A single overlay dict (not flattened).
    enforced_fields:
        List of dot-paths from the team config's ``enforce`` section.

    Returns
    -------
    list[str]
        Sorted list of dot-paths that appear in both the overlay and the
        enforced list.  Empty means no violations.
    """
    overlay_paths = set(flatten_to_dot_paths(overlay).keys())
    return sorted(overlay_paths & set(enforced_fields))


# ---------------------------------------------------------------------------
# Source tracking
# ---------------------------------------------------------------------------


def track_config_sources(
    layers: list[tuple[str, dict[str, Any]]],
) -> dict[str, str]:
    """Build a ``{dot.path: layer_name}`` map showing which layer set each value.

    Later layers overwrite earlier ones, matching the actual merge precedence.
    Used by ``aroom config view --with-sources`` to annotate each config key
    with its origin.

    Parameters
    ----------
    layers:
        Ordered list of ``(layer_name, raw_dict)`` pairs from lowest
        precedence to highest.  Example::

            [("team", team_raw), ("personal", personal_raw), ("env var", env_dict)]

    Returns
    -------
    dict[str, str]
        Map from dot-path to the name of the highest-precedence layer
        that set it.  Keys not present in any layer won't appear (they
        get "default" in the display logic).
    """
    sources: dict[str, str] = {}
    for layer_name, raw in layers:
        for dot_path in flatten_to_dot_paths(raw):
            sources[dot_path] = layer_name
    return sources


# ---------------------------------------------------------------------------
# Generalized artifact conflict detection (all artifact types)
# ---------------------------------------------------------------------------


def collect_pack_artifact_names(
    db: ThreadSafeConnection,
    pack_ids: list[str],
) -> dict[str, list[tuple[str, str]]]:
    """Collect ``{type/name: [(pack_label, fqn), ...]}`` for all artifacts in given packs.

    Groups artifacts by their user-facing identifier (``type/name``), which
    is the level at which collisions matter.  Two packs with
    ``@ns-a/skill/deploy`` and ``@ns-b/skill/deploy`` both expose ``/deploy``
    to the user — that's a collision even though their FQNs differ.

    Parameters
    ----------
    db:
        Thread-safe SQLite connection.
    pack_ids:
        Pack IDs to collect artifacts for.

    Returns
    -------
    dict[str, list[tuple[str, str]]]
        Map from ``type/name`` to a list of ``(pack_label, fqn)`` pairs.
        Only entries with 2+ items represent actual multi-pack presence.
    """
    import sqlite3

    if not pack_ids:
        return {}

    names: dict[str, list[tuple[str, str]]] = {}

    for pack_id in pack_ids:
        row = db.execute("SELECT namespace, name FROM packs WHERE id = ?", (pack_id,)).fetchone()
        if not row:
            continue
        ns = row[0] if isinstance(row, (tuple, list)) else row["namespace"]
        nm = row[1] if isinstance(row, (tuple, list)) else row["name"]
        label = f"{ns}/{nm}"

        try:
            art_rows = db.execute(
                "SELECT a.type, a.name, a.fqn FROM artifacts a "
                "JOIN pack_artifacts pa ON a.id = pa.artifact_id "
                "WHERE pa.pack_id = ?",
                (pack_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            continue

        for art_row in art_rows:
            a_type = art_row[0] if isinstance(art_row, (tuple, list)) else art_row["type"]
            a_name = art_row[1] if isinstance(art_row, (tuple, list)) else art_row["name"]
            a_fqn = art_row[2] if isinstance(art_row, (tuple, list)) else art_row["fqn"]
            key = f"{a_type}/{a_name}"
            names.setdefault(key, []).append((label, a_fqn))

    return names


# Artifact types that are inherently additive — multiple packs can provide
# artifacts with the same type/name and they all apply.  Rules add guidance,
# instructions add context, MCP server configs merge settings, etc.
# No conflict detection needed for these types.
_ADDITIVE_ARTIFACT_TYPES = frozenset({"skill", "rule", "instruction", "context", "memory", "mcp_server"})


def detect_artifact_conflicts(
    db: ThreadSafeConnection,
    new_pack_id: str,
    existing_pack_ids: list[str],
    *,
    new_priority: int | None = None,
    existing_priorities: dict[str, int] | None = None,
) -> list[str]:
    """Detect user-facing name collisions for exclusive artifact types.

    All non-config artifact types (``skill``, ``rule``, ``instruction``,
    ``context``, ``memory``, ``mcp_server``) are additive — same-name
    artifacts from multiple packs coexist.  Skills use namespace-qualified
    display names on collision (``/team-a/deploy`` vs ``/team-b/deploy``).

    ``config_overlay`` artifacts are excluded — they have their own
    dot-path-level conflict detection in :func:`detect_overlay_conflicts`
    where priority-based resolution IS supported.

    Parameters
    ----------
    db:
        Thread-safe SQLite connection.
    new_pack_id:
        Pack being attached.
    existing_pack_ids:
        Already-attached pack IDs.
    new_priority:
        Reserved for future use (not used for non-config artifacts).
    existing_priorities:
        Reserved for future use (not used for non-config artifacts).

    Returns
    -------
    list[str]
        Human-readable conflict descriptions.  Empty = no conflicts.
    """
    import sqlite3

    # Get new pack's artifacts (only exclusive types need checking)
    new_row = db.execute("SELECT namespace, name FROM packs WHERE id = ?", (new_pack_id,)).fetchone()
    if not new_row:
        return []
    new_ns = new_row[0] if isinstance(new_row, (tuple, list)) else new_row["namespace"]
    new_nm = new_row[1] if isinstance(new_row, (tuple, list)) else new_row["name"]
    new_label = f"{new_ns}/{new_nm}"

    try:
        new_art_rows = db.execute(
            "SELECT a.type, a.name FROM artifacts a "
            "JOIN pack_artifacts pa ON a.id = pa.artifact_id "
            "WHERE pa.pack_id = ? AND a.type != 'config_overlay'",
            (new_pack_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    if not new_art_rows:
        return []

    new_keys: set[str] = set()
    for art_row in new_art_rows:
        a_type = art_row[0] if isinstance(art_row, (tuple, list)) else art_row["type"]
        a_name = art_row[1] if isinstance(art_row, (tuple, list)) else art_row["name"]
        # Only check exclusive types — additive types can coexist
        if a_type in _ADDITIVE_ARTIFACT_TYPES:
            continue
        new_keys.add(f"{a_type}/{a_name}")

    if not new_keys:
        return []

    # Get existing packs' artifacts
    existing_names = collect_pack_artifact_names(db, existing_pack_ids)

    conflicts: list[str] = []
    for key in sorted(new_keys):
        if key not in existing_names:
            continue
        if key.startswith("config_overlay/"):
            continue

        for existing_label, _existing_fqn in existing_names[key]:
            conflicts.append(f"{key} provided by both {existing_label} and {new_label}")

    return conflicts


# ---------------------------------------------------------------------------
# Runtime config rebuild after pack lifecycle changes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigRebuildResult:
    """Result of rebuilding effective config after pack changes."""

    config: Any  # AppConfig — typed as Any to avoid circular imports
    enforced_fields: list[str]
    warnings: list[str] = field(default_factory=list)
    restart_required_fields: list[str] = field(default_factory=list)


# Fields that require process restart to take effect
_RESTART_ONLY_FIELDS = frozenset(
    {
        "ai.base_url",
        "ai.api_key",
        "ai.provider",
        "storage.encrypt_at_rest",
        "storage.encryption_kdf",
        "session.store",
        "audit.enabled",
        "audit.log_path",
        "audit.tamper_protection",
        "mcp_servers",
    }
)


def rebuild_effective_config(
    db: ThreadSafeConnection,
    *,
    team_config_path: Path | None = None,
    project_path: str | None = None,
    space_id: str | None = None,
    previous_config: Any | None = None,
) -> ConfigRebuildResult:
    """Rebuild the effective AppConfig after a pack lifecycle change.

    Collects current pack overlays, re-runs config loading, validates
    compliance, and detects restart-required field changes.

    Raises ``ComplianceError`` if the new config violates compliance rules.
    """
    from ..config import load_config
    from .compliance import validate_compliance
    from .pack_attachments import get_active_pack_ids, get_active_pack_ids_for_space, get_attachment_priorities

    if space_id is not None:
        active_ids = get_active_pack_ids_for_space(db, space_id, project_path=project_path)
    else:
        active_ids = get_active_pack_ids(db, project_path=project_path)
    pack_config: dict[str, Any] | None = None
    if active_ids:
        overlays = collect_pack_overlays(db, active_ids)
        if overlays:
            priorities = get_attachment_priorities(db, active_ids)
            pack_config = merge_pack_overlays(overlays, priorities)

    config, enforced_fields = load_config(
        team_config_path=team_config_path,
        pack_config=pack_config,
    )

    compliance_result = validate_compliance(config)
    if not compliance_result.is_compliant:
        msg = "Config compliance failure after pack change:\n" + compliance_result.format_report()
        raise ComplianceError(msg)

    warnings: list[str] = []
    restart_fields: list[str] = []
    if previous_config is not None:
        old_flat = flatten_to_dot_paths(_config_to_dict(previous_config))
        new_flat = flatten_to_dot_paths(_config_to_dict(config))
        for field_path in sorted(_RESTART_ONLY_FIELDS):
            old_val = old_flat.get(field_path)
            new_val = new_flat.get(field_path)
            if old_val != new_val:
                restart_fields.append(field_path)
                warnings.append(f"Config field '{field_path}' changed but requires process restart to take effect")

    return ConfigRebuildResult(
        config=config,
        enforced_fields=enforced_fields,
        warnings=warnings,
        restart_required_fields=restart_fields,
    )


def _config_to_dict(config: Any) -> dict[str, Any]:
    """Convert an AppConfig to a flat-friendly dict."""
    if dataclasses.is_dataclass(config) and not isinstance(config, type):
        return dataclasses.asdict(config)
    return {}
