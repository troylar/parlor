"""Auto-install built-in starter packs that ship with Anteroom.

Starter packs live under ``src/anteroom/packs/`` and are installed at
``built_in`` precedence (lowest — overridable by everything).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .artifacts import ArtifactSource
from .packs import PackManifest, parse_manifest, validate_manifest

UpsertFn = Callable[..., dict[str, Any] | None]

logger = logging.getLogger(__name__)

_PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"

STARTER_PACK_NAMES = ("python-dev", "security-baseline")


def list_starter_packs() -> list[dict[str, str]]:
    """Return metadata for all available starter packs (without installing)."""
    results: list[dict[str, str]] = []
    for pack_name in STARTER_PACK_NAMES:
        pack_dir = _PACKS_ROOT / pack_name
        manifest_path = pack_dir / "pack.yaml"
        if not manifest_path.is_file():
            continue
        try:
            manifest = parse_manifest(manifest_path)
            results.append(
                {
                    "name": manifest.name,
                    "namespace": manifest.namespace,
                    "version": manifest.version,
                    "description": manifest.description,
                }
            )
        except ValueError:
            continue
    return results


def install_starter_packs(
    db: sqlite3.Connection,
    *,
    names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Install built-in starter packs that are not yet in the DB.

    If *names* is given, only install those (for ``aroom init`` selection).
    Otherwise install all available starter packs.

    Returns list of dicts with ``name``, ``namespace``, ``status`` keys.
    Status is ``installed``, ``updated``, ``skipped`` (already at same version),
    or ``error``.
    """
    from .artifact_storage import upsert_artifact
    from .packs import _get_pack_row

    targets = names if names is not None else list(STARTER_PACK_NAMES)
    results: list[dict[str, str]] = []

    for pack_name in targets:
        pack_dir = _PACKS_ROOT / pack_name
        manifest_path = pack_dir / "pack.yaml"
        if not manifest_path.is_file():
            logger.warning("Starter pack %s not found at %s", pack_name, pack_dir)
            results.append({"name": pack_name, "namespace": "", "status": "error"})
            continue

        try:
            manifest = parse_manifest(manifest_path)
        except ValueError as e:
            logger.warning("Invalid starter pack manifest %s: %s", pack_name, e)
            results.append({"name": pack_name, "namespace": "", "status": "error"})
            continue

        errors = validate_manifest(manifest, pack_dir)
        if errors:
            logger.warning("Starter pack %s has validation errors: %s", pack_name, errors)
            results.append({"name": manifest.name, "namespace": manifest.namespace, "status": "error"})
            continue

        existing = _get_pack_row(db, manifest.namespace, manifest.name)
        if existing:
            existing_version = existing["version"] if isinstance(existing, dict) else existing[3]
            if existing_version == manifest.version:
                results.append({"name": manifest.name, "namespace": manifest.namespace, "status": "skipped"})
                continue
            # Version changed — update
            from .packs import remove_pack

            remove_pack(db, manifest.namespace, manifest.name)
            status = "updated"
        else:
            status = "installed"

        _install_starter(db, manifest, pack_dir, upsert_artifact)
        results.append({"name": manifest.name, "namespace": manifest.namespace, "status": status})

    return results


def _install_starter(
    db: sqlite3.Connection,
    manifest: PackManifest,
    pack_dir: Path,
    upsert_artifact: UpsertFn,
) -> None:
    """Install a single starter pack at built_in precedence."""
    import uuid
    from datetime import datetime, timezone

    from .artifacts import build_fqn
    from .packs import _read_artifact_content, _resolve_artifact_file

    now = datetime.now(timezone.utc).isoformat()
    pack_id = uuid.uuid4().hex

    artifact_ids: list[str] = []
    for art in manifest.artifacts:
        art_path = _resolve_artifact_file(art, pack_dir)
        if art_path is None:
            logger.warning("Skipping %s/%s: file not found", art.type, art.name)
            continue

        content, metadata = _read_artifact_content(art_path)
        fqn = build_fqn(manifest.namespace, art.type, art.name)

        row = upsert_artifact(
            db,
            fqn=fqn,
            artifact_type=art.type,
            namespace=manifest.namespace,
            name=art.name,
            content=content,
            source=ArtifactSource.BUILT_IN,
            metadata=metadata,
        )
        if row:
            artifact_ids.append(row["id"])

    source_path = str(pack_dir.resolve())
    db.execute(
        """INSERT INTO packs (id, name, namespace, version, description,
           source_path, installed_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pack_id,
            manifest.name,
            manifest.namespace,
            manifest.version,
            manifest.description,
            source_path,
            now,
            now,
        ),
    )

    for art_id in artifact_ids:
        db.execute(
            "INSERT INTO pack_artifacts (pack_id, artifact_id) VALUES (?, ?)",
            (pack_id, art_id),
        )

    db.commit()

    logger.info(
        "Installed starter pack %s/%s v%s (%d artifacts)",
        manifest.namespace,
        manifest.name,
        manifest.version,
        len(artifact_ids),
    )
