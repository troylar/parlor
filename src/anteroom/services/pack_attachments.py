"""Pack attachment management: attach/detach packs to global or project scope.

Attachments are DB-tracked records that determine which packs are active
for a given context. Global attachments (``project_path=None``) apply
everywhere. Project attachments apply only when working in that directory.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _validate_project_path(project_path: str | None) -> None:
    """Reject project paths with traversal components."""
    if project_path is None:
        return
    from pathlib import PurePosixPath

    parts = PurePosixPath(project_path).parts
    if ".." in parts:
        msg = "project_path must not contain '..' components"
        raise ValueError(msg)


def attach_pack(
    db: sqlite3.Connection,
    pack_id: str,
    *,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Attach a pack to global scope or a specific project.

    Raises ``ValueError`` if the pack doesn't exist or is already attached
    at the same scope.
    """
    _validate_project_path(project_path)
    pack = db.execute("SELECT id, namespace, name FROM packs WHERE id = ?", (pack_id,)).fetchone()
    if not pack:
        msg = f"Pack not found: {pack_id}"
        raise ValueError(msg)

    scope = "project" if project_path else "global"

    existing = db.execute(
        "SELECT id FROM pack_attachments WHERE pack_id = ? AND project_path IS ?",
        (pack_id, project_path),
    ).fetchone()
    if existing:
        ns = pack["namespace"] if isinstance(pack, dict) else pack[1]
        nm = pack["name"] if isinstance(pack, dict) else pack[2]
        msg = f"Pack {ns}/{nm} is already attached at {scope} scope"
        raise ValueError(msg)

    att_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO pack_attachments (id, pack_id, project_path, scope, created_at) VALUES (?, ?, ?, ?, ?)",
        (att_id, pack_id, project_path, scope, now),
    )
    db.commit()

    return {"id": att_id, "pack_id": pack_id, "project_path": project_path, "scope": scope, "created_at": now}


def detach_pack(
    db: sqlite3.Connection,
    pack_id: str,
    *,
    project_path: str | None = None,
) -> bool:
    """Detach a pack from the given scope. Returns True if found and removed."""
    cursor = db.execute(
        "DELETE FROM pack_attachments WHERE pack_id = ? AND project_path IS ?",
        (pack_id, project_path),
    )
    db.commit()
    return cursor.rowcount > 0


def list_attachments(
    db: sqlite3.Connection,
    *,
    project_path: str | None = None,
) -> list[dict[str, Any]]:
    """List pack attachments, optionally filtered by project path.

    If *project_path* is given, returns both global and project-specific
    attachments for that path. If ``None``, returns global attachments only.
    """
    if project_path is not None:
        rows = db.execute(
            """SELECT pa.id, pa.pack_id, pa.project_path, pa.scope, pa.created_at,
                      p.namespace, p.name, p.version
               FROM pack_attachments pa
               JOIN packs p ON pa.pack_id = p.id
               WHERE pa.project_path IS NULL OR pa.project_path = ?
               ORDER BY pa.scope, p.namespace, p.name""",
            (project_path,),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT pa.id, pa.pack_id, pa.project_path, pa.scope, pa.created_at,
                      p.namespace, p.name, p.version
               FROM pack_attachments pa
               JOIN packs p ON pa.pack_id = p.id
               WHERE pa.project_path IS NULL
               ORDER BY p.namespace, p.name"""
        ).fetchall()

    return [_row_to_dict(r) for r in rows]


def get_active_pack_ids(
    db: sqlite3.Connection,
    *,
    project_path: str | None = None,
) -> list[str]:
    """Return pack IDs that should be active for the given context.

    Includes global attachments plus project-specific ones if a project
    path is provided.
    """
    if project_path is not None:
        rows = db.execute(
            "SELECT DISTINCT pack_id FROM pack_attachments WHERE project_path IS NULL OR project_path = ?",
            (project_path,),
        ).fetchall()
    else:
        rows = db.execute("SELECT DISTINCT pack_id FROM pack_attachments WHERE project_path IS NULL").fetchall()

    return [r[0] if isinstance(r, (tuple, list)) else r["pack_id"] for r in rows]


def list_attachments_for_pack(
    db: sqlite3.Connection,
    pack_id: str,
) -> list[dict[str, Any]]:
    """List all attachments for a specific pack."""
    rows = db.execute(
        """SELECT id, pack_id, project_path, scope, created_at
           FROM pack_attachments WHERE pack_id = ?
           ORDER BY scope, project_path""",
        (pack_id,),
    ).fetchall()
    keys = ("id", "pack_id", "project_path", "scope", "created_at")
    return [dict(r) if isinstance(r, sqlite3.Row) else {k: v for k, v in zip(keys, r)} for r in rows]


def resolve_pack_id(db: sqlite3.Connection, namespace: str, name: str) -> str | None:
    """Look up pack_id from namespace/name. Returns None if not found."""
    row = db.execute(
        "SELECT id FROM packs WHERE namespace = ? AND name = ?",
        (namespace, name),
    ).fetchone()
    if not row:
        return None
    return row[0] if isinstance(row, (tuple, list)) else row["id"]


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a DB row to a dict."""
    if isinstance(row, sqlite3.Row):
        return dict(row)
    keys = ("id", "pack_id", "project_path", "scope", "created_at", "namespace", "name", "version")
    if isinstance(row, (tuple, list)):
        return {k: v for k, v in zip(keys, row)}
    return dict(row)
