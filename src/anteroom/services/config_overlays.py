"""Pack config overlay collection, merging, and conflict detection.

Config overlays are YAML artifacts declared by packs that influence Anteroom's
configuration.  They are merged into the config precedence chain between team
config and personal config::

    defaults < team < **packs** < personal < space < project < env vars

Team-enforced fields always win (re-applied after every merge).

Overview
--------

The overlay lifecycle has four phases:

1. **Collection** — :func:`collect_pack_overlays` reads ``config_overlay``
   artifacts from the DB for a set of pack IDs.

2. **Conflict detection** — :func:`detect_overlay_conflicts` compares a
   new pack's overlays against already-attached packs.  The policy is
   "forbid overlaps": two packs may never set the same dot-path, even to
   the same value.  This prevents subtle precedence surprises where pack
   install order silently changes behavior.

3. **Merging** — :func:`merge_pack_overlays` combines all overlay dicts
   into a single dict using :func:`~anteroom.services.team_config.deep_merge`.
   Because conflicts are forbidden at attach time, the merge order is
   irrelevant for correctness.

4. **Source tracking** — :func:`track_config_sources` builds a
   ``{dot.path: layer_name}`` map for the ``aroom config view --with-sources``
   transparency command.

Key design decisions
--------------------

- **Dot-path flattening** converts nested dicts to flat ``a.b.c`` keys for
  set-intersection conflict detection.  List values are treated as opaque
  leaves — two packs that both set a list key will conflict, even if the
  lists contain different items.  This is intentional: list merging
  semantics (append? replace? named-list match?) are ambiguous, so we
  require each list-valued key to be owned by exactly one pack.

- **Intra-pack conflicts are allowed.**  A single pack may have multiple
  ``config_overlay`` artifacts that set the same key.  This lets pack
  authors compose overlays (e.g., a base overlay + an environment-specific
  override).  Only *cross-pack* conflicts are forbidden.

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

import logging
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from ..db import ThreadSafeConnection

logger = logging.getLogger(__name__)


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
) -> dict[str, Any]:
    """Merge multiple pack overlays into a single config dict.

    Uses :func:`~anteroom.services.team_config.deep_merge` for recursive
    merging.  Returns the combined overlay dict (empty dict if no overlays).

    Parameters
    ----------
    overlays:
        List of ``(pack_label, overlay_dict)`` pairs as returned by
        :func:`collect_pack_overlays`.  The pack labels are ignored
        during merging (they exist for conflict error messages).

    Returns
    -------
    dict[str, Any]
        A single merged dict.  If overlays have non-overlapping keys
        (the normal case after conflict detection), order doesn't matter.
        If they overlap (e.g., ``check_overlay_conflicts=False`` was used),
        later overlays in the list win — but the order is determined by
        ``get_active_pack_ids()`` which uses ``SELECT DISTINCT`` with
        no guaranteed order.  Relying on this order is a bug; use
        conflict detection to prevent it.
    """
    if not overlays:
        return {}

    from .team_config import deep_merge

    merged: dict[str, Any] = {}
    for _label, overlay in overlays:
        merged = deep_merge(merged, overlay)
    return merged


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


def detect_overlay_conflicts(
    existing_overlays: list[tuple[str, dict[str, Any]]],
    new_overlay: tuple[str, dict[str, Any]],
) -> list[str]:
    """Find dot-paths where *new_overlay* conflicts with *existing_overlays*.

    A conflict is a dot-path set by both the new overlay and an existing one
    (regardless of whether the values match — "forbid overlaps" policy).

    This is called during ``pack attach`` to prevent two packs from setting
    the same configuration key.  The strict policy avoids precedence
    surprises: rather than silently picking one value, we force the user
    to choose which pack should own the key.

    Parameters
    ----------
    existing_overlays:
        Overlays from already-attached packs (as returned by
        :func:`collect_pack_overlays`).
    new_overlay:
        The ``(pack_label, overlay_dict)`` pair for the pack being attached.

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
      Two packs that both set ``mcp_servers`` will conflict even if their
      lists contain different server entries.
    """
    new_label, new_dict = new_overlay
    new_paths = set(flatten_to_dot_paths(new_dict).keys())

    conflicts: list[str] = []
    for existing_label, existing_dict in existing_overlays:
        existing_paths = set(flatten_to_dot_paths(existing_dict).keys())
        overlap = new_paths & existing_paths
        for path in sorted(overlap):
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
