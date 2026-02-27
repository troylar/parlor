"""Space DB CRUD operations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _uuid() -> str:
    return str(uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_UPDATABLE_COLUMNS = frozenset({"file_path", "file_hash", "last_loaded_at", "updated_at"})


def create_space(
    db: sqlite3.Connection,
    name: str,
    file_path: str,
    file_hash: str = "",
) -> dict[str, Any]:
    sid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO spaces (id, name, file_path, file_hash, last_loaded_at, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, name, file_path, file_hash, now, now, now),
    )
    db.commit()
    return {
        "id": sid,
        "name": name,
        "file_path": file_path,
        "file_hash": file_hash,
        "last_loaded_at": now,
        "created_at": now,
        "updated_at": now,
    }


def get_space(db: sqlite3.Connection, space_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT id, name, file_path, file_hash, last_loaded_at, created_at, updated_at FROM spaces WHERE id = ?",
        (space_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def get_space_by_name(db: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT id, name, file_path, file_hash, last_loaded_at, created_at, updated_at FROM spaces WHERE name = ?",
        (name,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def list_spaces(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT id, name, file_path, file_hash, last_loaded_at, created_at, updated_at FROM spaces ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def update_space(db: sqlite3.Connection, space_id: str, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return get_space(db, space_id)

    bad = set(kwargs.keys()) - _UPDATABLE_COLUMNS
    if bad:
        raise ValueError(f"Cannot update column(s): {', '.join(sorted(bad))}")

    kwargs["updated_at"] = _now()
    set_clause = ", ".join(f"{col} = ?" for col in kwargs)
    values = list(kwargs.values())
    values.append(space_id)
    db.execute(f"UPDATE spaces SET {set_clause} WHERE id = ?", values)  # noqa: S608
    db.commit()
    return get_space(db, space_id)


def delete_space(db: sqlite3.Connection, space_id: str) -> bool:
    space = get_space(db, space_id)
    if not space:
        return False
    db.execute("UPDATE conversations SET space_id = NULL WHERE space_id = ?", (space_id,))
    db.execute("UPDATE folders SET space_id = NULL WHERE space_id = ?", (space_id,))
    db.execute("DELETE FROM pack_attachments WHERE space_id = ?", (space_id,))
    db.execute("DELETE FROM spaces WHERE id = ?", (space_id,))
    db.commit()
    return True


def sync_space_paths(
    db: sqlite3.Connection,
    space_id: str,
    paths: list[dict[str, Any]],
) -> None:
    db.execute("DELETE FROM space_paths WHERE space_id = ?", (space_id,))
    seen_paths: set[str] = set()
    for p in paths:
        local = p.get("local_path", "")
        if local in seen_paths:
            continue
        seen_paths.add(local)
        db.execute(
            "INSERT INTO space_paths (id, space_id, repo_url, local_path, created_at) VALUES (?, ?, ?, ?, ?)",
            (_uuid(), space_id, p.get("repo_url", ""), local, _now()),
        )
    db.commit()


def get_space_paths(db: sqlite3.Connection, space_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT id, space_id, repo_url, local_path FROM space_paths WHERE space_id = ?",
        (space_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_conversation_space(
    db: sqlite3.Connection,
    conversation_id: str,
    space_id: str | None,
) -> None:
    db.execute(
        "UPDATE conversations SET space_id = ? WHERE id = ?",
        (space_id, conversation_id),
    )
    db.commit()


def count_space_conversations(db: sqlite3.Connection, space_id: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM conversations WHERE space_id = ?",
        (space_id,),
    ).fetchone()
    return row[0] if row else 0


def get_space_local_dirs(db: sqlite3.Connection, space_id: str) -> list[str]:
    """Return local_path values for a space that have non-empty local_path."""
    rows = db.execute(
        "SELECT local_path FROM space_paths WHERE space_id = ? AND local_path != ''",
        (space_id,),
    ).fetchall()
    return [r["local_path"] if hasattr(r, "keys") else r[0] for r in rows]


def resolve_space_by_cwd(db: sqlite3.Connection, cwd: str) -> dict[str, Any] | None:
    """Resolve a space by working directory (exact match or subdirectory).

    Checks exact path first, then walks up parent directories to find a
    mapped space path. Returns the deepest (most specific) match.
    """
    # Exact match first
    row = db.execute(
        "SELECT s.id, s.name, s.file_path, s.file_hash, s.last_loaded_at, s.created_at, s.updated_at"
        " FROM spaces s JOIN space_paths sp ON s.id = sp.space_id"
        " WHERE sp.local_path = ?",
        (cwd,),
    ).fetchone()
    if row:
        return dict(row)

    # Walk up parent directories
    from pathlib import Path

    current = Path(cwd)
    for parent in current.parents:
        row = db.execute(
            "SELECT s.id, s.name, s.file_path, s.file_hash, s.last_loaded_at, s.created_at, s.updated_at"
            " FROM spaces s JOIN space_paths sp ON s.id = sp.space_id"
            " WHERE sp.local_path = ?",
            (str(parent),),
        ).fetchone()
        if row:
            return dict(row)

    return None
