"""Artifact CRUD operations against SQLite."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .artifacts import ArtifactSource, ArtifactType, content_hash, validate_fqn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def create_artifact(
    db: sqlite3.Connection,
    fqn: str,
    artifact_type: str | ArtifactType,
    namespace: str,
    name: str,
    content: str,
    source: str | ArtifactSource = ArtifactSource.LOCAL,
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    """Insert a new artifact and its initial version record."""
    if not validate_fqn(fqn):
        raise ValueError(f"Invalid FQN: {fqn!r}")
    art_type = ArtifactType(artifact_type).value
    art_source = ArtifactSource(source).value
    meta_json = json.dumps(metadata or {})
    chash = content_hash(content)
    aid = _uuid()
    now = _now()

    db.execute(
        "INSERT INTO artifacts"
        " (id, fqn, type, namespace, name, content, content_hash, source,"
        "  metadata, user_id, user_display_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            aid,
            fqn,
            art_type,
            namespace,
            name,
            content,
            chash,
            art_source,
            meta_json,
            user_id,
            user_display_name,
            now,
            now,
        ),
    )

    vid = _uuid()
    db.execute(
        "INSERT INTO artifact_versions (id, artifact_id, version, content, content_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (vid, aid, 1, content, chash, now),
    )
    db.commit()

    return {
        "id": aid,
        "fqn": fqn,
        "type": art_type,
        "namespace": namespace,
        "name": name,
        "content": content,
        "content_hash": chash,
        "source": art_source,
        "metadata": metadata or {},
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }


def get_artifact(db: sqlite3.Connection, artifact_id: str) -> dict[str, Any] | None:
    """Fetch an artifact by primary key."""
    row = db.execute_fetchone("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
    if not row:
        return None
    return _row_to_dict(row)


def get_artifact_by_fqn(db: sqlite3.Connection, fqn: str) -> dict[str, Any] | None:
    """Fetch an artifact by its fully-qualified name."""
    row = db.execute_fetchone("SELECT * FROM artifacts WHERE fqn = ?", (fqn,))
    if not row:
        return None
    return _row_to_dict(row)


def list_artifacts(
    db: sqlite3.Connection,
    artifact_type: str | ArtifactType | None = None,
    namespace: str | None = None,
    source: str | ArtifactSource | None = None,
) -> list[dict[str, Any]]:
    """List artifacts with optional filtering."""
    clauses: list[str] = []
    params: list[Any] = []
    if artifact_type is not None:
        clauses.append("type = ?")
        params.append(ArtifactType(artifact_type).value)
    if namespace is not None:
        clauses.append("namespace = ?")
        params.append(namespace)
    if source is not None:
        clauses.append("source = ?")
        params.append(ArtifactSource(source).value)

    sql = "SELECT * FROM artifacts"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC"
    rows = db.execute_fetchall(sql, tuple(params))
    return [_row_to_dict(r) for r in rows]


def update_artifact(
    db: sqlite3.Connection,
    artifact_id: str,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update an artifact's content and/or metadata.

    If content changes, a new version record is created automatically.
    """
    existing = get_artifact(db, artifact_id)
    if not existing:
        return None

    now = _now()

    if content is not None and content_hash(content) != existing["content_hash"]:
        chash = content_hash(content)
        cur_version = db.execute_fetchone(
            "SELECT MAX(version) as max_v FROM artifact_versions WHERE artifact_id = ?",
            (artifact_id,),
        )
        next_ver = (cur_version["max_v"] or 0) + 1 if cur_version else 1

        db.execute(
            "UPDATE artifacts SET content = ?, content_hash = ?, updated_at = ? WHERE id = ?",
            (content, chash, now, artifact_id),
        )
        vid = _uuid()
        db.execute(
            "INSERT INTO artifact_versions (id, artifact_id, version, content, content_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (vid, artifact_id, next_ver, content, chash, now),
        )

    if metadata is not None:
        db.execute(
            "UPDATE artifacts SET metadata = ?, updated_at = ? WHERE id = ?",
            (json.dumps(metadata), now, artifact_id),
        )

    if content is not None or metadata is not None:
        db.commit()

    return get_artifact(db, artifact_id)


def delete_artifact(db: sqlite3.Connection, artifact_id: str) -> bool:
    """Delete an artifact and its version history (CASCADE)."""
    existing = get_artifact(db, artifact_id)
    if not existing:
        return False
    db.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
    db.commit()
    return True


def list_artifact_versions(db: sqlite3.Connection, artifact_id: str) -> list[dict[str, Any]]:
    """List all versions of an artifact, newest first."""
    rows = db.execute_fetchall(
        "SELECT * FROM artifact_versions WHERE artifact_id = ? ORDER BY version DESC",
        (artifact_id,),
    )
    return [dict(r) for r in rows]


def upsert_artifact(
    db: sqlite3.Connection,
    fqn: str,
    artifact_type: str | ArtifactType,
    namespace: str,
    name: str,
    content: str,
    source: str | ArtifactSource = ArtifactSource.LOCAL,
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any] | None:
    """Create or update an artifact by FQN.

    If the FQN already exists, updates content/metadata and bumps version.
    Returns None only if the artifact was deleted between lookup and update.
    """
    existing = get_artifact_by_fqn(db, fqn)
    if existing:
        return update_artifact(
            db,
            existing["id"],
            content=content,
            metadata=metadata if metadata is not None else existing["metadata"],
        )
    return create_artifact(
        db,
        fqn,
        artifact_type,
        namespace,
        name,
        content,
        source,
        metadata,
        user_id,
        user_display_name,
    )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a Row to dict, deserializing the metadata JSON."""
    d = dict(row)
    if "metadata" in d and isinstance(d["metadata"], str):
        d["metadata"] = json.loads(d["metadata"])
    return d
