"""Space DB CRUD operations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from ..db import ThreadSafeConnection


def _uuid() -> str:
    return str(uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_UPDATABLE_COLUMNS = frozenset(
    {
        "name",
        "instructions",
        "model",
        "source_file",
        "source_hash",
        "last_loaded_at",
        "updated_at",
    }
)

_SPACE_COLUMNS = "id, name, instructions, model, source_file, source_hash, last_loaded_at, created_at, updated_at"
_SPACE_COLUMNS_PREFIXED = (
    "s.id, s.name, s.instructions, s.model, s.source_file, s.source_hash, s.last_loaded_at, s.created_at, s.updated_at"
)


def create_space(
    db: ThreadSafeConnection,
    name: str,
    *,
    source_file: str = "",
    source_hash: str = "",
    instructions: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    sid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO spaces (id, name, instructions, model, source_file, source_hash,"
        " last_loaded_at, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, name, instructions, model, source_file, source_hash, now, now, now),
    )
    db.commit()
    return {
        "id": sid,
        "name": name,
        "instructions": instructions,
        "model": model,
        "source_file": source_file,
        "source_hash": source_hash,
        "last_loaded_at": now,
        "created_at": now,
        "updated_at": now,
    }


def get_space(db: ThreadSafeConnection, space_id: str) -> dict[str, Any] | None:
    row = db.execute(
        f"SELECT {_SPACE_COLUMNS} FROM spaces WHERE id = ?",
        (space_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def get_space_by_name(db: ThreadSafeConnection, name: str) -> dict[str, Any] | None:
    row = db.execute(
        f"SELECT {_SPACE_COLUMNS} FROM spaces WHERE name = ?",
        (name,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def get_spaces_by_name(db: ThreadSafeConnection, name: str) -> list[dict[str, Any]]:
    """Return all spaces matching *name* (there may be duplicates)."""
    rows = db.execute(
        f"SELECT {_SPACE_COLUMNS} FROM spaces WHERE name = ?",
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]


def resolve_space(db: ThreadSafeConnection, name_or_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Resolve a space by exact ID, ID prefix, or name.

    Returns ``(match, [])`` on unique match, or ``(None, candidates)`` when
    ambiguous. An empty candidates list with ``None`` match means not found.
    """
    sp = get_space(db, name_or_id)
    if sp:
        return sp, []

    all_spaces = list_spaces(db)
    prefix_matches = [s for s in all_spaces if s["id"].startswith(name_or_id)]
    if len(prefix_matches) == 1:
        return prefix_matches[0], []

    name_matches = get_spaces_by_name(db, name_or_id)
    if len(name_matches) == 1:
        return name_matches[0], []
    if len(name_matches) > 1:
        return None, name_matches

    if len(prefix_matches) > 1:
        return None, prefix_matches

    return None, []


def list_spaces(db: ThreadSafeConnection) -> list[dict[str, Any]]:
    rows = db.execute(f"SELECT {_SPACE_COLUMNS} FROM spaces ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def update_space(db: ThreadSafeConnection, space_id: str, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return get_space(db, space_id)

    bad = set(kwargs.keys()) - _UPDATABLE_COLUMNS
    if bad:
        raise ValueError(f"Cannot update column(s): {', '.join(sorted(bad))}")

    kwargs["updated_at"] = _now()
    set_clause = ", ".join(f"{col} = ?" for col in kwargs)
    values = tuple(kwargs.values()) + (space_id,)
    db.execute(f"UPDATE spaces SET {set_clause} WHERE id = ?", values)  # noqa: S608
    db.commit()
    return get_space(db, space_id)


def delete_space(db: ThreadSafeConnection, space_id: str) -> bool:
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
    db: ThreadSafeConnection,
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


def get_space_paths(db: ThreadSafeConnection, space_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT id, space_id, repo_url, local_path FROM space_paths WHERE space_id = ?",
        (space_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_conversation_space(
    db: ThreadSafeConnection,
    conversation_id: str,
    space_id: str | None,
) -> None:
    db.execute(
        "UPDATE conversations SET space_id = ? WHERE id = ?",
        (space_id, conversation_id),
    )
    db.commit()


def count_space_conversations(db: ThreadSafeConnection, space_id: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM conversations WHERE space_id = ?",
        (space_id,),
    ).fetchone()
    return row[0] if row else 0


def get_space_local_dirs(db: ThreadSafeConnection, space_id: str) -> list[str]:
    """Return local_path values for a space that have non-empty local_path."""
    rows = db.execute(
        "SELECT local_path FROM space_paths WHERE space_id = ? AND local_path != ''",
        (space_id,),
    ).fetchall()
    return [r["local_path"] if hasattr(r, "keys") else r[0] for r in rows]


def discover_space_file(cwd: str) -> Path | None:
    """Walk up from *cwd* looking for a space YAML file in parent directories.

    Checks ``.anteroom/``, ``.claude/``, and ``.parlor/`` at each level.
    Prefers ``space.yaml`` over other ``*.yaml`` files.  Skips ``.local.yaml``
    files (machine-specific overrides).

    Returns the first matching path, or ``None``.
    """
    project_dirs = (".anteroom", ".claude", ".parlor")
    current = Path(cwd).resolve()
    while True:
        for dirname in project_dirs:
            candidate_dir = current / dirname
            if not candidate_dir.is_dir():
                continue
            canonical = candidate_dir / "space.yaml"
            if canonical.is_file():
                return canonical
            for p in sorted(candidate_dir.glob("*.yaml")):
                if p.name.endswith(".local.yaml"):
                    continue
                return p
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def resolve_space_by_cwd(db: ThreadSafeConnection, cwd: str) -> dict[str, Any] | None:
    """Resolve a space by working directory (exact match or subdirectory).

    Checks exact path first, then walks up parent directories to find a
    mapped space path. Returns the deepest (most specific) match.
    """
    row = db.execute(
        f"SELECT {_SPACE_COLUMNS_PREFIXED}"
        " FROM spaces s JOIN space_paths sp ON s.id = sp.space_id"
        " WHERE sp.local_path = ?",
        (cwd,),
    ).fetchone()
    if row:
        return dict(row)

    current = Path(cwd)
    for parent in current.parents:
        row = db.execute(
            f"SELECT {_SPACE_COLUMNS_PREFIXED}"
            " FROM spaces s JOIN space_paths sp ON s.id = sp.space_id"
            " WHERE sp.local_path = ?",
            (str(parent),),
        ).fetchone()
        if row:
            return dict(row)

    return None
