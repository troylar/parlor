"""Pack config overlay collection, merging, and conflict detection.

Config overlays are YAML artifacts declared by packs that influence Anteroom's
configuration.  They are merged into the config precedence chain between team
config and personal config::

    defaults < team < **packs** < personal < space < project < env vars

Team-enforced fields always win (re-applied after every merge).
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
    """Flatten a nested dict to ``{dot.path: leaf_value}`` pairs."""
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
    """Load config_overlay artifacts for the given pack IDs.

    Returns a list of ``(pack_label, overlay_dict)`` pairs where
    *pack_label* is ``namespace/name`` for human-readable conflict messages.
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

    Returns a list of human-readable conflict descriptions.
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
    """Return enforced dot-paths that the overlay tries to set."""
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
    """
    sources: dict[str, str] = {}
    for layer_name, raw in layers:
        for dot_path in flatten_to_dot_paths(raw):
            sources[dot_path] = layer_name
    return sources
