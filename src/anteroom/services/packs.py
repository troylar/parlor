"""Pack management: manifests, install/remove, reference counting.

Packs are named groupings of artifacts. A pack manifest (``pack.yaml``)
declares which artifacts it includes. Packs are immutable in consuming
projects — content is generated output, never hand-edited. Updates are
wholesale replacement (remove + reinstall).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..db import ThreadSafeConnection

import yaml

from .artifact_storage import delete_artifact, upsert_artifact
from .artifacts import ArtifactSource, ArtifactType, build_fqn

logger = logging.getLogger(__name__)

_MANIFEST_FILE = "pack.yaml"
_PACKS_DIR = "packs"
_ANTEROOM_DIR = ".anteroom"
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

# Artifact type -> subdirectory name in pack directory
_TYPE_DIRS: dict[str, str] = {
    ArtifactType.SKILL: "skills",
    ArtifactType.RULE: "rules",
    ArtifactType.INSTRUCTION: "instructions",
    ArtifactType.CONTEXT: "context",
    ArtifactType.MEMORY: "memories",
    ArtifactType.MCP_SERVER: "mcp_servers",
    ArtifactType.CONFIG_OVERLAY: "config_overlays",
}


@dataclass(frozen=True)
class ManifestArtifact:
    """A single artifact reference in a pack manifest."""

    type: str
    name: str
    file: str = ""


@dataclass(frozen=True)
class PackManifest:
    """Parsed pack.yaml manifest."""

    name: str
    namespace: str
    version: str = "0.0.0"
    description: str = ""
    artifacts: tuple[ManifestArtifact, ...] = ()


def parse_manifest(manifest_path: Path) -> PackManifest:
    """Parse a pack.yaml file into a PackManifest.

    Raises ``ValueError`` on missing required fields or invalid format.
    """
    if not manifest_path.is_file():
        msg = f"Manifest not found: {manifest_path}"
        raise ValueError(msg)

    with open(manifest_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        msg = f"Manifest must be a YAML mapping, got {type(data).__name__}"
        raise ValueError(msg)

    name = str(data.get("name", "")).strip()
    if not name:
        msg = "Manifest missing required field: name"
        raise ValueError(msg)
    if not _SAFE_NAME_RE.match(name):
        msg = f"Invalid pack name: must match {_SAFE_NAME_RE.pattern}"
        raise ValueError(msg)

    namespace = str(data.get("namespace", "")).strip()
    if not namespace:
        msg = "Manifest missing required field: namespace"
        raise ValueError(msg)
    if not _SAFE_NAME_RE.match(namespace):
        msg = f"Invalid namespace: must match {_SAFE_NAME_RE.pattern}"
        raise ValueError(msg)

    version = str(data.get("version", "0.0.0")).strip()
    description = str(data.get("description", "")).strip()

    raw_artifacts = data.get("artifacts", [])
    if not isinstance(raw_artifacts, list):
        msg = "Manifest 'artifacts' must be a list"
        raise ValueError(msg)

    artifacts: list[ManifestArtifact] = []
    valid_types = {t.value for t in ArtifactType}
    for i, entry in enumerate(raw_artifacts):
        if not isinstance(entry, dict):
            msg = f"Artifact entry {i} must be a mapping"
            raise ValueError(msg)
        art_type = str(entry.get("type", "")).strip()
        if art_type not in valid_types:
            msg = f"Artifact entry {i}: invalid type '{art_type}'. Must be one of: {', '.join(sorted(valid_types))}"
            raise ValueError(msg)
        art_name = str(entry.get("name", "")).strip()
        if not art_name:
            msg = f"Artifact entry {i}: missing required field 'name'"
            raise ValueError(msg)
        art_file = str(entry.get("file", "")).strip()
        artifacts.append(ManifestArtifact(type=art_type, name=art_name, file=art_file))

    return PackManifest(
        name=name,
        namespace=namespace,
        version=version,
        description=description,
        artifacts=tuple(artifacts),
    )


def _is_symlink(path: Path) -> bool:
    """Check if *path* is a symlink using lstat (never follows the link)."""
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except OSError:
        return False


def validate_manifest(manifest: PackManifest, pack_dir: Path) -> list[str]:
    """Validate that all artifacts referenced in the manifest exist as files.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    resolved_pack = pack_dir.resolve()
    for art in manifest.artifacts:
        if art.file:
            art_path = pack_dir / art.file
            if _is_symlink(art_path):
                errors.append(f"Symlink not allowed for artifact {art.type}/{art.name}: {art.file}")
                continue
            if not art_path.resolve().is_relative_to(resolved_pack):
                errors.append(f"Path traversal in artifact {art.type}/{art.name}: {art.file}")
                continue
        else:
            type_dir = _TYPE_DIRS.get(art.type, art.type)
            art_path = pack_dir / type_dir / f"{art.name}.yaml"
            if not art_path.is_file():
                art_path = pack_dir / type_dir / f"{art.name}.md"
            if _is_symlink(art_path):
                errors.append(f"Symlink not allowed for artifact {art.type}/{art.name}: {art_path.name}")
                continue

        if not art_path.is_file():
            errors.append(f"Missing artifact file for {art.type}/{art.name}: {art_path}")

    return errors


def _resolve_artifact_file(art: ManifestArtifact, pack_dir: Path) -> Path | None:
    """Resolve the file path for a manifest artifact entry."""
    resolved_pack = pack_dir.resolve()
    if art.file:
        candidate = pack_dir / art.file
        if _is_symlink(candidate):
            logger.warning("Symlink rejected: %s", art.file)
            return None
        if not candidate.resolve().is_relative_to(resolved_pack):
            logger.warning("Path traversal blocked: %s", art.file)
            return None
        return candidate if candidate.is_file() else None

    type_dir = _TYPE_DIRS.get(art.type, art.type)
    for ext in (".yaml", ".md", ".txt", ".json"):
        candidate = pack_dir / type_dir / f"{art.name}{ext}"
        if candidate.is_file():
            if _is_symlink(candidate):
                logger.warning("Symlink rejected: %s", candidate)
                return None
            return candidate
    return None


def _read_artifact_content(path: Path) -> tuple[str, dict[str, Any]]:
    """Read artifact content from a file.

    For YAML files, extracts ``content`` and ``metadata`` fields.
    For other files, the entire content is the artifact content.
    """
    raw = path.read_text(encoding="utf-8")

    if path.suffix in (".yaml", ".yml"):
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            logger.warning("Invalid YAML in %s, treating as raw content: %s", path, e)
            return raw, {}
        if isinstance(data, dict):
            content = str(data.get("content", raw))
            metadata = data.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            return content, metadata

    return raw, {}


def install_pack(
    db: ThreadSafeConnection,
    manifest: PackManifest,
    pack_dir: Path,
    *,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    """Install a pack from a local directory.

    If a pack with the same namespace/name already exists, it is updated
    atomically (remove old + reinstall).  Otherwise a fresh row is created.

    Upserts all artifacts, creates the pack DB row, and links them via
    ``pack_artifacts``. If *project_dir* is given, copies the pack
    directory into ``.anteroom/packs/<namespace>/<name>/``.

    Returns a dict with pack info and installed artifact count.
    The ``"action"`` key is ``"installed"`` or ``"updated"``.
    """
    # If the pack already exists, delegate to update_pack for atomic replace
    existing = _get_pack_row(db, manifest.namespace, manifest.name)
    if existing:
        result = update_pack(db, manifest, pack_dir, project_dir=project_dir)
        result["action"] = "updated"
        return result

    now = datetime.now(timezone.utc).isoformat()
    pack_id = uuid.uuid4().hex

    # Read all artifact content first (I/O outside the transaction)
    artifact_data: list[tuple[str, str, str, str, dict[str, Any]]] = []
    skipped: list[str] = []
    for art in manifest.artifacts:
        art_path = _resolve_artifact_file(art, pack_dir)
        if art_path is None:
            skipped.append(f"{art.type}/{art.name}")
            logger.warning("Skipping %s/%s: file not found", art.type, art.name)
            continue

        content, metadata = _read_artifact_content(art_path)
        fqn = build_fqn(manifest.namespace, art.type, art.name)
        artifact_data.append((fqn, art.type, art.name, content, metadata))

    # Install inside a single transaction
    artifact_ids: list[str] = []
    with db.transaction():
        for fqn, art_type, art_name, content, metadata in artifact_data:
            row = upsert_artifact(
                db,
                fqn=fqn,
                artifact_type=art_type,
                namespace=manifest.namespace,
                name=art_name,
                content=content,
                source=ArtifactSource.PROJECT if project_dir is not None else ArtifactSource.GLOBAL,
                metadata=metadata,
                commit=False,
            )
            if row:
                artifact_ids.append(row["id"])

        # Insert pack row
        source_path = str(pack_dir.resolve())
        db.execute(
            """INSERT INTO packs (id, name, namespace, version, description,
               source_path, installed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (pack_id, manifest.name, manifest.namespace, manifest.version, manifest.description, source_path, now, now),
        )

        # Link artifacts to pack
        for art_id in artifact_ids:
            db.execute(
                "INSERT INTO pack_artifacts (pack_id, artifact_id) VALUES (?, ?)",
                (pack_id, art_id),
            )

    # Copy to project if requested
    if project_dir is not None:
        _copy_to_project(pack_dir, manifest, project_dir)

    logger.info(
        "Installed pack %s/%s v%s (%d artifacts)",
        manifest.namespace,
        manifest.name,
        manifest.version,
        len(artifact_ids),
    )

    return {
        "id": pack_id,
        "name": manifest.name,
        "namespace": manifest.namespace,
        "version": manifest.version,
        "artifact_count": len(artifact_ids),
        "skipped_artifacts": skipped,
        "action": "installed",
    }


def remove_pack(db: ThreadSafeConnection, namespace: str, name: str) -> bool:
    """Remove a pack and any artifacts not referenced by other packs.

    Returns ``True`` if the pack was found and removed.
    """
    pack_row = _get_pack_row(db, namespace, name)
    if not pack_row:
        return False

    pack_id = pack_row["id"]

    # Remove inside a single transaction (orphan detection must be inside
    # the transaction to avoid TOCTOU race with concurrent pack installs)
    with db.transaction():
        # Find artifacts that belong ONLY to this pack (not shared with others)
        orphan_rows = db.execute(
            """SELECT pa.artifact_id FROM pack_artifacts pa
               WHERE pa.pack_id = ?
               AND pa.artifact_id NOT IN (
                   SELECT artifact_id FROM pack_artifacts WHERE pack_id != ?
               )""",
            (pack_id, pack_id),
        ).fetchall()
        orphan_ids = [r[0] if isinstance(r, (tuple, list)) else r["artifact_id"] for r in orphan_rows]

        db.execute("DELETE FROM pack_attachments WHERE pack_id = ?", (pack_id,))
        db.execute("DELETE FROM packs WHERE id = ?", (pack_id,))
        for art_id in orphan_ids:
            delete_artifact(db, art_id, commit=False)

    logger.info(
        "Removed pack %s/%s (%d orphaned artifacts deleted)",
        namespace,
        name,
        len(orphan_ids),
    )
    return True


def update_pack(
    db: ThreadSafeConnection,
    manifest: PackManifest,
    pack_dir: Path,
    *,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    """Update a pack by removing the old version and reinstalling atomically.

    Raises ``ValueError`` if the pack is not currently installed.
    """
    existing = _get_pack_row(db, manifest.namespace, manifest.name)
    if not existing:
        msg = f"Pack {manifest.namespace}/{manifest.name} is not installed"
        raise ValueError(msg)

    old_pack_id = existing[0] if isinstance(existing, (tuple, list)) else existing["id"]

    # Read all new artifact content (I/O outside the transaction)
    now = datetime.now(timezone.utc).isoformat()
    new_pack_id = uuid.uuid4().hex
    artifact_data: list[tuple[str, str, str, str, dict[str, Any]]] = []
    skipped: list[str] = []
    for art in manifest.artifacts:
        art_path = _resolve_artifact_file(art, pack_dir)
        if art_path is None:
            skipped.append(f"{art.type}/{art.name}")
            logger.warning("Skipping %s/%s: file not found", art.type, art.name)
            continue
        content, metadata = _read_artifact_content(art_path)
        fqn = build_fqn(manifest.namespace, art.type, art.name)
        artifact_data.append((fqn, art.type, art.name, content, metadata))

    # Atomic remove-and-reinstall in a single transaction
    artifact_ids: list[str] = []
    with db.transaction():
        # Find artifacts that belong ONLY to the old pack (not shared with others).
        # Must be inside the transaction to avoid TOCTOU race with concurrent installs.
        orphan_rows = db.execute(
            """SELECT pa.artifact_id FROM pack_artifacts pa
               WHERE pa.pack_id = ?
               AND pa.artifact_id NOT IN (
                   SELECT artifact_id FROM pack_artifacts WHERE pack_id != ?
               )""",
            (old_pack_id, old_pack_id),
        ).fetchall()
        orphan_ids = [r[0] if isinstance(r, (tuple, list)) else r["artifact_id"] for r in orphan_rows]

        # Save existing attachments before removing old pack
        # (DELETE FROM packs CASCADE-deletes pack_attachments)
        attachment_rows = db.execute(
            "SELECT project_path, space_id, scope, priority FROM pack_attachments WHERE pack_id = ?",
            (old_pack_id,),
        ).fetchall()

        # Remove old pack
        db.execute("DELETE FROM packs WHERE id = ?", (old_pack_id,))
        for art_id in orphan_ids:
            delete_artifact(db, art_id, commit=False)

        # Install new artifacts
        for fqn, art_type, art_name, content, metadata in artifact_data:
            row = upsert_artifact(
                db,
                fqn=fqn,
                artifact_type=art_type,
                namespace=manifest.namespace,
                name=art_name,
                content=content,
                source=ArtifactSource.PROJECT if project_dir is not None else ArtifactSource.GLOBAL,
                metadata=metadata,
                commit=False,
            )
            if row:
                artifact_ids.append(row["id"])

        # Insert new pack row
        source_path = str(pack_dir.resolve())
        db.execute(
            """INSERT INTO packs (id, name, namespace, version, description,
               source_path, installed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_pack_id,
                manifest.name,
                manifest.namespace,
                manifest.version,
                manifest.description,
                source_path,
                now,
                now,
            ),
        )

        # Link artifacts to new pack
        for art_id in artifact_ids:
            db.execute(
                "INSERT INTO pack_artifacts (pack_id, artifact_id) VALUES (?, ?)",
                (new_pack_id, art_id),
            )

        # Restore attachments from the old pack
        for att in attachment_rows:
            if isinstance(att, (tuple, list)):
                project_path, space_id, scope, priority = att[0], att[1], att[2], att[3]
            else:
                project_path = att["project_path"]
                space_id = att["space_id"]
                scope = att["scope"]
                priority = att["priority"]
            att_id = uuid.uuid4().hex
            att_now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """INSERT INTO pack_attachments
                   (id, pack_id, project_path, space_id, scope, priority, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (att_id, new_pack_id, project_path, space_id, scope, priority, att_now),
            )

    # Copy to project if requested (outside transaction — file I/O)
    if project_dir is not None:
        _copy_to_project(pack_dir, manifest, project_dir)

    logger.info(
        "Updated pack %s/%s to v%s (%d artifacts)",
        manifest.namespace,
        manifest.name,
        manifest.version,
        len(artifact_ids),
    )

    return {
        "id": new_pack_id,
        "name": manifest.name,
        "namespace": manifest.namespace,
        "version": manifest.version,
        "artifact_count": len(artifact_ids),
        "skipped_artifacts": skipped,
        "action": "updated",
    }


def list_packs(db: ThreadSafeConnection) -> list[dict[str, Any]]:
    """List all installed packs with artifact counts."""
    rows = db.execute(
        """SELECT p.id, p.name, p.namespace, p.version, p.description,
                  p.source_path, p.installed_at, p.updated_at,
                  COUNT(pa.artifact_id) AS artifact_count
           FROM packs p
           LEFT JOIN pack_artifacts pa ON p.id = pa.pack_id
           GROUP BY p.id
           ORDER BY p.namespace, p.name"""
    ).fetchall()

    return [_pack_row_to_dict(r) for r in rows]


def get_pack(db: ThreadSafeConnection, namespace: str, name: str) -> dict[str, Any] | None:
    """Get a pack with its full artifact list."""
    pack_row = _get_pack_row(db, namespace, name)
    if not pack_row:
        return None

    result = _pack_row_to_dict(pack_row)
    pack_id = result["id"]

    art_rows = db.execute(
        """SELECT a.id, a.fqn, a.type, a.namespace, a.name, a.content, a.content_hash
           FROM artifacts a
           JOIN pack_artifacts pa ON a.id = pa.artifact_id
           WHERE pa.pack_id = ?
           ORDER BY a.type, a.name""",
        (pack_id,),
    ).fetchall()

    result["artifacts"] = [_art_row_to_dict(r) for r in art_rows]
    result["artifact_count"] = len(result["artifacts"])
    return result


def load_project_packs(db: ThreadSafeConnection, project_dir: Path) -> list[dict[str, Any]]:
    """Scan ``.anteroom/packs/`` in a project and install any not yet in the DB."""
    packs_root = project_dir / _ANTEROOM_DIR / _PACKS_DIR
    if not packs_root.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for ns_dir in sorted(packs_root.iterdir()):
        if not ns_dir.is_dir():
            continue
        for pack_dir in sorted(ns_dir.iterdir()):
            if not pack_dir.is_dir():
                continue
            manifest_path = pack_dir / _MANIFEST_FILE
            if not manifest_path.is_file():
                logger.debug("Skipping %s: no pack.yaml", pack_dir)
                continue

            try:
                manifest = parse_manifest(manifest_path)
            except ValueError as e:
                logger.warning("Invalid manifest in %s: %s", pack_dir, e)
                continue

            # Skip if already installed
            if _get_pack_row(db, manifest.namespace, manifest.name):
                continue

            try:
                result = install_pack(db, manifest, pack_dir)
                results.append(result)
            except ValueError as e:
                logger.warning("Failed to install pack from %s: %s", pack_dir, e)

    return results


def _copy_to_project(pack_dir: Path, manifest: PackManifest, project_dir: Path) -> None:
    """Copy only manifest-referenced files (+ pack.yaml) into the project's ``.anteroom/packs/`` tree.

    Avoids copying unrelated files that may exist in the pack source directory.
    """
    dest = project_dir / _ANTEROOM_DIR / _PACKS_DIR / manifest.namespace / manifest.name
    if dest.exists() or _is_symlink(dest):
        if _is_symlink(dest):
            dest.unlink()
        else:
            shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # Always copy the manifest
    manifest_src = pack_dir / _MANIFEST_FILE
    if manifest_src.is_file():
        shutil.copy2(manifest_src, dest / _MANIFEST_FILE)

    # Copy only files referenced by manifest artifacts
    for art in manifest.artifacts:
        art_path = _resolve_artifact_file(art, pack_dir)
        if art_path is None or not art_path.is_file():
            continue
        rel = art_path.relative_to(pack_dir)
        dest_file = dest / rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(art_path, dest_file)

    logger.info("Copied pack to project: %s", dest)


def _get_pack_row(db: ThreadSafeConnection, namespace: str, name: str) -> Any:
    """Fetch a pack row by namespace and name."""
    return db.execute(
        "SELECT id, name, namespace, version, description, source_path, installed_at, updated_at"
        " FROM packs WHERE namespace = ? AND name = ?",
        (namespace, name),
    ).fetchone()


def get_pack_by_source_path(db: ThreadSafeConnection, source_path: str) -> dict[str, Any] | None:
    """Get a pack by its source_path. Returns None if not found."""
    row = db.execute(
        "SELECT id, name, namespace, version, description, source_path, installed_at, updated_at"
        " FROM packs WHERE source_path = ?",
        (source_path,),
    ).fetchone()
    if not row:
        return None
    return _pack_row_to_dict(row)


def get_pack_by_id(db: ThreadSafeConnection, pack_id: str) -> dict[str, Any] | None:
    """Get a pack by its unique ID, including artifacts."""
    row = db.execute(
        "SELECT id, name, namespace, version, description, source_path, installed_at, updated_at"
        " FROM packs WHERE id = ?",
        (pack_id,),
    ).fetchone()
    if not row:
        return None
    result = _pack_row_to_dict(row)
    art_rows = db.execute(
        """SELECT a.id, a.fqn, a.type, a.namespace, a.name, a.content, a.content_hash
           FROM artifacts a
           JOIN pack_artifacts pa ON a.id = pa.artifact_id
           WHERE pa.pack_id = ?
           ORDER BY a.type, a.name""",
        (pack_id,),
    ).fetchall()
    result["artifacts"] = [_art_row_to_dict(r) for r in art_rows]
    result["artifact_count"] = len(result["artifacts"])
    return result


def resolve_pack(
    db: ThreadSafeConnection, namespace: str, name: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Resolve a pack by namespace/name.

    Returns ``(match, [])`` on unique match, or ``(None, [])`` when not found.
    With the UNIQUE(namespace, name) constraint, ambiguity is impossible.
    """
    row = _get_pack_row(db, namespace, name)
    if row:
        return _pack_row_to_dict(row), []
    return None, []


def remove_pack_by_id(db: ThreadSafeConnection, pack_id: str) -> bool:
    """Remove a pack by its unique ID."""
    row = db.execute("SELECT id FROM packs WHERE id = ?", (pack_id,)).fetchone()
    if not row:
        return False

    with db.transaction():
        # Collect artifact IDs BEFORE deleting the junction rows
        art_rows = db.execute(
            "SELECT artifact_id FROM pack_artifacts WHERE pack_id = ?",
            (pack_id,),
        ).fetchall()
        artifact_ids = [r[0] if isinstance(r, (tuple, list)) else r["artifact_id"] for r in art_rows]

        # Find orphans: artifacts owned ONLY by this pack (not shared with others)
        orphan_ids: list[str] = []
        for aid in artifact_ids:
            ref = db.execute(
                "SELECT COUNT(*) FROM pack_artifacts WHERE artifact_id = ? AND pack_id != ?",
                (aid, pack_id),
            ).fetchone()
            count = ref[0] if isinstance(ref, (tuple, list)) else ref["COUNT(*)"]
            if count == 0:
                orphan_ids.append(aid)

        # Now safe to delete junction rows and pack
        db.execute("DELETE FROM pack_artifacts WHERE pack_id = ?", (pack_id,))
        db.execute("DELETE FROM pack_attachments WHERE pack_id = ?", (pack_id,))
        db.execute("DELETE FROM packs WHERE id = ?", (pack_id,))

        # Delete only truly orphaned artifacts
        for aid in orphan_ids:
            delete_artifact(db, aid, commit=False)

    return True


def _pack_row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a DB row to a dict."""
    if isinstance(row, sqlite3.Row):
        return dict(row)
    keys = (
        "id",
        "name",
        "namespace",
        "version",
        "description",
        "source_path",
        "installed_at",
        "updated_at",
        "artifact_count",
    )
    if isinstance(row, (tuple, list)):
        return {k: v for k, v in zip(keys, row)}
    return dict(row)


def _art_row_to_dict(row: Any) -> dict[str, Any]:
    """Convert an artifact DB row to a dict."""
    if isinstance(row, sqlite3.Row):
        d = dict(row)
    elif isinstance(row, (tuple, list)):
        keys = ("id", "fqn", "type", "namespace", "name", "content", "content_hash")
        d = {k: v for k, v in zip(keys, row)}
    else:
        d = dict(row)
    if "metadata" in d and isinstance(d["metadata"], str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
    return d
