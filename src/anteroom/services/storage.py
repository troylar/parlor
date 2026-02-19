"""SQLite data access layer for conversations, messages, attachments, and tool calls."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import filetype

from ..tools.path_utils import safe_resolve_pathlib

logger = logging.getLogger(__name__)


VALID_CONVERSATION_TYPES = {"chat", "note", "document"}

_ALLOWED_UPDATE_COLUMNS: set[str] = {
    "name",
    "instructions",
    "model",
    "updated_at",
    "parent_id",
    "collapsed",
    "position",
    "color",
    "folder_id",
    "type",
}

_ALLOWED_SOURCE_UPDATE_COLUMNS: set[str] = {
    "title",
    "content",
    "url",
    "content_hash",
    "updated_at",
}


def _build_set_clause(updates: dict[str, Any]) -> tuple[str, list[Any]]:
    """Build a safe SET clause from a column->value dict, validating column names."""
    parts: list[str] = []
    params: list[Any] = []
    for col, val in updates.items():
        if col not in _ALLOWED_UPDATE_COLUMNS:
            raise ValueError(f"Column {col!r} not in allowed update columns")
        parts.append(f"{col} = ?")
        params.append(val)
    return ", ".join(parts), params


def _in_clause(values: list[Any]) -> tuple[str, list[Any]]:
    """Build a safe IN clause: returns '(?, ?, ?)' and the values list."""
    return f"({','.join('?' for _ in values)})", list(values)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# --- Projects ---


def create_project(
    db: sqlite3.Connection,
    name: str,
    instructions: str = "",
    model: str | None = None,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    pid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO projects (id, name, instructions, model, user_id, user_display_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, name, instructions, model or None, user_id, user_display_name, now, now),
    )
    db.commit()
    return {
        "id": pid,
        "name": name,
        "instructions": instructions,
        "model": model,
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
        "updated_at": now,
    }


def get_project(db: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not row:
        return None
    return dict(row)


def list_projects(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute_fetchall("SELECT * FROM projects ORDER BY updated_at DESC")
    return [dict(r) for r in rows]


def update_project(
    db: sqlite3.Connection,
    project_id: str,
    name: str | None = None,
    instructions: str | None = None,
    model: str | None = ...,
) -> dict[str, Any] | None:
    proj = get_project(db, project_id)
    if not proj:
        return None
    cols: dict[str, Any] = {"updated_at": _now()}
    if name is not None:
        cols["name"] = name
    if instructions is not None:
        cols["instructions"] = instructions
    if model is not ...:
        cols["model"] = model or None
    set_clause, params = _build_set_clause(cols)
    params.append(project_id)
    db.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", tuple(params))
    db.commit()
    return get_project(db, project_id)


def delete_project(db: sqlite3.Connection, project_id: str) -> bool:
    proj = get_project(db, project_id)
    if not proj:
        return False
    db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    db.commit()
    return True


# --- Folders ---


def create_folder(
    db: sqlite3.Connection,
    name: str,
    parent_id: str | None = None,
    project_id: str | None = None,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    fid = _uuid()
    now = _now()
    pos_row = db.execute_fetchone(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM folders WHERE parent_id IS ? AND project_id IS ?",
        (parent_id, project_id),
    )
    position = pos_row[0] if pos_row else 0
    db.execute(
        "INSERT INTO folders (id, name, parent_id, project_id, position, collapsed,"
        " user_id, user_display_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (fid, name, parent_id, project_id, position, user_id, user_display_name, now, now),
    )
    db.commit()
    return {
        "id": fid,
        "name": name,
        "parent_id": parent_id,
        "project_id": project_id,
        "position": position,
        "collapsed": False,
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
        "updated_at": now,
    }


def list_folders(
    db: sqlite3.Connection,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    if project_id:
        rows = db.execute_fetchall(
            "SELECT * FROM folders WHERE project_id = ? ORDER BY position",
            (project_id,),
        )
    else:
        rows = db.execute_fetchall("SELECT * FROM folders ORDER BY position")
    result = []
    for r in rows:
        d = dict(r)
        d["collapsed"] = bool(d["collapsed"])
        result.append(d)
    return result


def update_folder(
    db: sqlite3.Connection,
    folder_id: str,
    name: str | None = None,
    parent_id: str | None = ...,
    collapsed: bool | None = None,
    position: int | None = None,
) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM folders WHERE id = ?", (folder_id,))
    if not row:
        return None
    cols: dict[str, Any] = {"updated_at": _now()}
    if name is not None:
        cols["name"] = name
    if parent_id is not ...:
        cols["parent_id"] = parent_id
    if collapsed is not None:
        cols["collapsed"] = 1 if collapsed else 0
    if position is not None:
        cols["position"] = position
    set_clause, params = _build_set_clause(cols)
    params.append(folder_id)
    db.execute(f"UPDATE folders SET {set_clause} WHERE id = ?", tuple(params))
    db.commit()
    updated = db.execute_fetchone("SELECT * FROM folders WHERE id = ?", (folder_id,))
    if not updated:
        return None
    d = dict(updated)
    d["collapsed"] = bool(d["collapsed"])
    return d


def _get_descendant_folder_ids(db: sqlite3.Connection, folder_id: str) -> list[str]:
    """Recursively collect all descendant folder IDs."""
    ids = []
    children = db.execute_fetchall("SELECT id FROM folders WHERE parent_id = ?", (folder_id,))
    for child in children:
        child_id = dict(child)["id"]
        ids.append(child_id)
        ids.extend(_get_descendant_folder_ids(db, child_id))
    return ids


def delete_folder(db: sqlite3.Connection, folder_id: str) -> bool:
    row = db.execute_fetchone("SELECT * FROM folders WHERE id = ?", (folder_id,))
    if not row:
        return False
    all_ids = [folder_id] + _get_descendant_folder_ids(db, folder_id)
    in_clause, in_params = _in_clause(all_ids)
    db.execute(f"UPDATE conversations SET folder_id = NULL WHERE folder_id IN {in_clause}", tuple(in_params))
    db.execute(f"DELETE FROM folders WHERE id IN {in_clause}", tuple(in_params))
    db.commit()
    return True


def move_conversation_to_folder(
    db: sqlite3.Connection,
    conversation_id: str,
    folder_id: str | None,
) -> None:
    now = _now()
    db.execute(
        "UPDATE conversations SET folder_id = ?, updated_at = ? WHERE id = ?",
        (folder_id, now, conversation_id),
    )
    db.commit()


# --- Tags ---


def create_tag(
    db: sqlite3.Connection,
    name: str,
    color: str = "#3b82f6",
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    tid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO tags (id, name, color, user_id, user_display_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (tid, name, color, user_id, user_display_name, now),
    )
    db.commit()
    return {
        "id": tid,
        "name": name,
        "color": color,
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
    }


def list_tags(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute_fetchall("SELECT * FROM tags ORDER BY name")
    return [dict(r) for r in rows]


def update_tag(
    db: sqlite3.Connection,
    tag_id: str,
    name: str | None = None,
    color: str | None = None,
) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM tags WHERE id = ?", (tag_id,))
    if not row:
        return None
    cols: dict[str, Any] = {}
    if name is not None:
        cols["name"] = name
    if color is not None:
        cols["color"] = color
    if not cols:
        return dict(row)
    set_clause, params = _build_set_clause(cols)
    params.append(tag_id)
    db.execute(f"UPDATE tags SET {set_clause} WHERE id = ?", tuple(params))
    db.commit()
    updated = db.execute_fetchone("SELECT * FROM tags WHERE id = ?", (tag_id,))
    return dict(updated) if updated else None


def delete_tag(db: sqlite3.Connection, tag_id: str) -> bool:
    row = db.execute_fetchone("SELECT * FROM tags WHERE id = ?", (tag_id,))
    if not row:
        return False
    db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    db.commit()
    return True


def add_tag_to_conversation(db: sqlite3.Connection, conversation_id: str, tag_id: str) -> bool:
    try:
        db.execute(
            "INSERT OR IGNORE INTO conversation_tags (conversation_id, tag_id) VALUES (?, ?)",
            (conversation_id, tag_id),
        )
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def remove_tag_from_conversation(db: sqlite3.Connection, conversation_id: str, tag_id: str) -> bool:
    db.execute(
        "DELETE FROM conversation_tags WHERE conversation_id = ? AND tag_id = ?",
        (conversation_id, tag_id),
    )
    db.commit()
    return True


def get_conversation_tags(db: sqlite3.Connection, conversation_id: str) -> list[dict[str, Any]]:
    rows = db.execute_fetchall(
        "SELECT t.* FROM tags t JOIN conversation_tags ct ON ct.tag_id = t.id"
        " WHERE ct.conversation_id = ? ORDER BY t.name",
        (conversation_id,),
    )
    return [dict(r) for r in rows]


# --- Conversations ---


def create_conversation(
    db: sqlite3.Connection,
    title: str = "New Conversation",
    project_id: str | None = None,
    user_id: str | None = None,
    user_display_name: str | None = None,
    conversation_type: str = "chat",
) -> dict[str, Any]:
    if conversation_type not in VALID_CONVERSATION_TYPES:
        raise ValueError(f"Invalid conversation type: {conversation_type!r}")
    cid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO conversations (id, title, type, project_id, user_id, user_display_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, title, conversation_type, project_id, user_id, user_display_name, now, now),
    )
    db.commit()
    return {
        "id": cid,
        "title": title,
        "type": conversation_type,
        "model": None,
        "project_id": project_id,
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
        "updated_at": now,
    }


def get_conversation(db: sqlite3.Connection, conversation_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
    if not row:
        return None
    return dict(row)


def _sanitize_fts_query(query: str) -> str:
    """Escape FTS5 special characters by wrapping in double quotes."""
    safe = query.replace('"', '""')
    return f'"{safe}"'


DEFAULT_PAGE_LIMIT = 100


def list_conversations(
    db: sqlite3.Connection,
    search: str | None = None,
    project_id: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
    conversation_type: str | None = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []

    use_fts = False
    if search:
        safe_search = _sanitize_fts_query(search)
        use_fts = True
        conditions.append("conversations_fts MATCH ?")
        params.append(safe_search)

    if project_id:
        conditions.append("c.project_id = ?")
        params.append(project_id)

    if conversation_type and conversation_type in VALID_CONVERSATION_TYPES:
        conditions.append("c.type = ?")
        params.append(conversation_type)

    # SECURITY-REVIEW: conditions list contains only static literal strings (never user input);
    # all user values are in params as bind parameters. Safe query-builder pattern.
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])

    if use_fts:
        query = f"""
            SELECT c.id, c.title, c.type, c.folder_id, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) as message_count
            FROM conversations c
            JOIN conversations_fts fts ON fts.conversation_id = c.id
            {where}
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
        """
    else:
        query = f"""
            SELECT c.id, c.title, c.type, c.folder_id, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) as message_count
            FROM conversations c
            {where}
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
        """

    rows = db.execute_fetchall(query, tuple(params))
    results = [dict(r) for r in rows]
    for conv in results:
        conv["tags"] = get_conversation_tags(db, conv["id"])
    return results


def update_conversation_title(db: sqlite3.Connection, conversation_id: str, title: str) -> dict[str, Any] | None:
    now = _now()
    db.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (title, now, conversation_id),
    )
    db.commit()
    return get_conversation(db, conversation_id)


def update_conversation_type(
    db: sqlite3.Connection, conversation_id: str, conversation_type: str
) -> dict[str, Any] | None:
    if conversation_type not in VALID_CONVERSATION_TYPES:
        raise ValueError(f"Invalid conversation type: {conversation_type!r}")
    now = _now()
    db.execute(
        "UPDATE conversations SET type = ?, updated_at = ? WHERE id = ?",
        (conversation_type, now, conversation_id),
    )
    db.commit()
    return get_conversation(db, conversation_id)


def update_conversation_model(db: sqlite3.Connection, conversation_id: str, model: str | None) -> dict[str, Any] | None:
    now = _now()
    db.execute(
        "UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
        (model or None, now, conversation_id),
    )
    db.commit()
    return get_conversation(db, conversation_id)


def fork_conversation(
    db: sqlite3.Connection,
    conversation_id: str,
    up_to_position: int,
) -> dict[str, Any]:
    conv = get_conversation(db, conversation_id)
    if not conv:
        raise ValueError("Conversation not found")

    new_cid = _uuid()
    now = _now()
    fork_title = (conv.get("title") or "New Conversation") + " (fork)"

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, model, project_id, user_id, user_display_name,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_cid,
                fork_title,
                conv.get("model"),
                conv.get("project_id"),
                conv.get("user_id"),
                conv.get("user_display_name"),
                now,
                now,
            ),
        )

        old_msgs = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND position <= ? ORDER BY position",
            (conversation_id, up_to_position),
        ).fetchall()

        for msg in old_msgs:
            msg = dict(msg)
            new_mid = _uuid()
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, user_id, user_display_name,"
                " created_at, position)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_mid,
                    new_cid,
                    msg["role"],
                    msg["content"],
                    msg.get("user_id"),
                    msg.get("user_display_name"),
                    msg["created_at"],
                    msg["position"],
                ),
            )

            old_atts = conn.execute("SELECT * FROM attachments WHERE message_id = ?", (msg["id"],)).fetchall()
            for att in old_atts:
                att = dict(att)
                conn.execute(
                    "INSERT INTO attachments"
                    " (id, message_id, filename, mime_type, size_bytes, storage_path)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (_uuid(), new_mid, att["filename"], att["mime_type"], att["size_bytes"], att["storage_path"]),
                )

            old_tcs = conn.execute("SELECT * FROM tool_calls WHERE message_id = ?", (msg["id"],)).fetchall()
            for tc in old_tcs:
                tc = dict(tc)
                conn.execute(
                    "INSERT INTO tool_calls"
                    " (id, message_id, tool_name, server_name, input_json, output_json, status, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _uuid(),
                        new_mid,
                        tc["tool_name"],
                        tc["server_name"],
                        tc["input_json"],
                        tc["output_json"],
                        tc["status"],
                        tc["created_at"],
                    ),
                )

    return {
        "id": new_cid,
        "title": fork_title,
        "model": conv.get("model"),
        "project_id": conv.get("project_id"),
        "created_at": now,
        "updated_at": now,
    }


def copy_conversation_to_db(
    source_db: sqlite3.Connection,
    target_db: sqlite3.Connection,
    conversation_id: str,
) -> dict[str, Any] | None:
    """Copy a conversation with all messages, attachments, and tool calls to another database."""
    conv = get_conversation(source_db, conversation_id)
    if not conv:
        return None

    new_cid = _uuid()
    now = _now()
    target_db.execute(
        "INSERT INTO conversations (id, title, model, project_id, user_id, user_display_name,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_cid,
            conv["title"],
            conv.get("model"),
            None,
            conv.get("user_id"),
            conv.get("user_display_name"),
            conv.get("created_at", now),
            now,
        ),
    )

    messages = list_messages(source_db, conversation_id)
    for msg in messages:
        new_mid = _uuid()
        target_db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, user_id, user_display_name,"
            " created_at, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_mid,
                new_cid,
                msg["role"],
                msg["content"],
                msg.get("user_id"),
                msg.get("user_display_name"),
                msg["created_at"],
                msg["position"],
            ),
        )
        for att in msg.get("attachments", []):
            target_db.execute(
                "INSERT INTO attachments (id, message_id, filename, mime_type, size_bytes, storage_path)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (_uuid(), new_mid, att["filename"], att["mime_type"], att["size_bytes"], att["storage_path"]),
            )
        for tc in msg.get("tool_calls", []):
            target_db.execute(
                "INSERT INTO tool_calls"
                " (id, message_id, tool_name, server_name, input_json, output_json, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _uuid(),
                    new_mid,
                    tc["tool_name"],
                    tc["server_name"],
                    json.dumps(tc["input"]),
                    json.dumps(tc["output"]) if tc["output"] else None,
                    tc["status"],
                    tc["created_at"],
                ),
            )

    target_db.commit()
    return get_conversation(target_db, new_cid)


def delete_conversation(db: sqlite3.Connection, conversation_id: str, data_dir: Path) -> bool:
    conv = get_conversation(db, conversation_id)
    if not conv:
        return False
    attachments_dir = data_dir / "attachments" / conversation_id
    if attachments_dir.exists():
        shutil.rmtree(attachments_dir)
    db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    db.commit()
    return True


# --- Messages ---


def create_message(
    db: sqlite3.Connection,
    conversation_id: str,
    role: str,
    content: str,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    mid = _uuid()
    now = _now()
    with db.transaction() as conn:
        pos_row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        position = pos_row[0]
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, user_id, user_display_name,"
            " created_at, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, conversation_id, role, content, user_id, user_display_name, now, position),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
    return {
        "id": mid,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
        "position": position,
    }


def list_messages(db: sqlite3.Connection, conversation_id: str) -> list[dict[str, Any]]:
    rows = db.execute_fetchall(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY position",
        (conversation_id,),
    )
    messages = []
    for row in rows:
        msg = dict(row)
        msg["attachments"] = list_attachments(db, msg["id"])
        msg["tool_calls"] = list_tool_calls(db, msg["id"])
        messages.append(msg)
    return messages


def update_message_content(
    db: sqlite3.Connection,
    conversation_id: str,
    message_id: str,
    new_content: str,
) -> dict[str, Any] | None:
    row = db.execute_fetchone(
        "SELECT * FROM messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    )
    if not row:
        return None
    db.execute("UPDATE messages SET content = ? WHERE id = ?", (new_content, message_id))
    now = _now()
    db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    db.commit()
    updated = db.execute_fetchone("SELECT * FROM messages WHERE id = ?", (message_id,))
    return dict(updated) if updated else None


def delete_message(
    db: sqlite3.Connection,
    conversation_id: str,
    message_id: str,
) -> bool:
    """Delete a single message by ID, validating it belongs to the given conversation."""
    row = db.execute_fetchone(
        "SELECT id FROM messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    )
    if not row:
        return False
    db.execute("DELETE FROM messages WHERE id = ? AND conversation_id = ?", (message_id, conversation_id))
    now = _now()
    db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    db.commit()
    return True


def replace_document_content(
    db: sqlite3.Connection,
    conversation_id: str,
    content: str,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    """Replace all messages in a document conversation with a single new message."""
    mid = _uuid()
    now = _now()
    with db.transaction() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, user_id, user_display_name,"
            " created_at, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, conversation_id, "user", content, user_id, user_display_name, now, 0),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
    return {
        "id": mid,
        "conversation_id": conversation_id,
        "role": "user",
        "content": content,
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
        "position": 0,
    }


def delete_messages_after_position(
    db: sqlite3.Connection,
    conversation_id: str,
    position: int,
    data_dir: Path | None = None,
) -> int:
    msgs = db.execute_fetchall(
        "SELECT id FROM messages WHERE conversation_id = ? AND position > ?",
        (conversation_id, position),
    )
    msg_ids = [dict(m)["id"] for m in msgs]
    if not msg_ids:
        return 0

    if data_dir:
        in_clause, in_params = _in_clause(msg_ids)
        atts = db.execute_fetchall(
            f"SELECT storage_path FROM attachments WHERE message_id IN {in_clause}",
            tuple(in_params),
        )
        for att in atts:
            file_path = safe_resolve_pathlib(data_dir / dict(att)["storage_path"])
            if file_path.is_relative_to(safe_resolve_pathlib(data_dir)) and file_path.exists():
                file_path.unlink()

    db.execute(
        "DELETE FROM messages WHERE conversation_id = ? AND position > ?",
        (conversation_id, position),
    )
    now = _now()
    db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    db.commit()
    return len(msg_ids)


# --- Attachments ---

ALLOWED_MIME_TYPES = {
    # Text / code
    "text/plain",
    "text/markdown",
    "text/css",
    "text/csv",
    "text/xml",
    "text/html",
    "text/javascript",
    "text/x-python",
    "text/x-c",
    "text/x-c++src",
    "text/x-java-source",
    "text/x-go",
    "text/x-rust",
    "text/x-ruby",
    "text/x-shellscript",
    "text/x-sql",
    "text/x-toml",
    "text/x-typescript",
    # Application / code
    "application/json",
    "application/javascript",
    "application/x-yaml",
    "application/yaml",
    "application/x-python-code",
    "application/xml",
    "application/sql",
    "application/toml",
    # Documents
    "application/pdf",
    "application/rtf",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # Images
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    # NOTE: image/svg+xml intentionally excluded — SVG can contain embedded
    # JavaScript, making it a stored XSS vector when served to browsers.
    # Archives
    "application/zip",
    "application/gzip",
    "application/x-tar",
}

# MIME types that are text-based but don't start with "text/" — these are safe
# to accept even when filetype.guess() returns None (no magic bytes).
_TEXT_LIKE_MIME_TYPES = {
    "application/json",
    "application/javascript",
    "application/x-yaml",
    "application/yaml",
    "application/x-python-code",
    "application/xml",
    "application/sql",
    "application/toml",
    "application/rtf",
}

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB


def _sanitize_filename(filename: str) -> str:
    """Strip path components and dangerous characters from filename."""
    safe = os.path.basename(filename).replace("\x00", "")
    safe = re.sub(r"[^\w.\-]", "_", safe)
    return safe or "unnamed"


# File extensions considered text-like for application/octet-stream fallback.
# When a browser sends no MIME type, we verify the content is valid UTF-8
# AND the extension is in this set before accepting.
_TEXT_LIKE_EXTENSIONS = {
    "txt",
    "md",
    "markdown",
    "rst",
    "csv",
    "tsv",
    "log",
    "json",
    "yaml",
    "yml",
    "toml",
    "ini",
    "cfg",
    "conf",
    "xml",
    "html",
    "htm",
    "css",
    "sql",
    "py",
    "js",
    "ts",
    "jsx",
    "tsx",
    "java",
    "c",
    "cpp",
    "h",
    "hpp",
    "rs",
    "go",
    "rb",
    "php",
    "sh",
    "bat",
    "ps1",
}


def _validate_upload(mime_type: str, data: bytes, filename: str) -> None:
    """Shared upload validation: size limit, MIME allowlist, and content verification."""
    if len(data) > MAX_ATTACHMENT_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_ATTACHMENT_SIZE // (1024 * 1024)} MB")

    # Handle application/octet-stream as a special case: browser sent no MIME type.
    # Only accept if the file extension is text-like AND content is valid UTF-8.
    if mime_type == "application/octet-stream":
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in _TEXT_LIKE_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {mime_type}")
        sample = data[:8192]
        try:
            sample.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            logger.warning("Cannot verify binary content for %s", filename)
            raise ValueError("Cannot verify file content type")
        return  # Passed extension + UTF-8 checks

    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError(f"Unsupported file type: {mime_type}")

    # Magic-byte verification for binary formats
    guess = filetype.guess(data)
    if guess is not None:
        if guess.mime != mime_type:
            # Allow Office format MIME mismatches:
            # - OpenXML (.docx/.xlsx/.pptx): filetype detects ZIP container
            # - Legacy (.doc/.xls/.ppt): filetype detects OLE/CFB container
            if mime_type.startswith("application/vnd.") and guess.mime in (
                "application/zip",
                "application/x-ole-storage",
                "application/x-cfb",
            ):
                pass  # Office formats use container formats — this is expected
            else:
                logger.warning("MIME mismatch: claimed %s, detected %s for %s", mime_type, guess.mime, filename)
                raise ValueError("File content does not match declared type")
    elif mime_type.startswith("text/") or mime_type in _TEXT_LIKE_MIME_TYPES:
        pass  # Text-based formats have no magic bytes — this is expected
    else:
        logger.warning("Cannot verify binary MIME type %s for %s", mime_type, filename)
        raise ValueError("Cannot verify file content type")


def save_attachment(
    db: sqlite3.Connection,
    message_id: str,
    conversation_id: str,
    filename: str,
    mime_type: str,
    data: bytes,
    data_dir: Path,
) -> dict[str, Any]:
    _validate_upload(mime_type, data, filename)

    safe_filename = _sanitize_filename(filename)
    aid = _uuid()
    attachments_dir = data_dir / "attachments" / conversation_id
    attachments_dir.mkdir(parents=True, exist_ok=True)
    storage_path = f"attachments/{conversation_id}/{aid}_{safe_filename}"
    full_path = safe_resolve_pathlib(data_dir / storage_path)
    if not full_path.is_relative_to(safe_resolve_pathlib(data_dir)):
        raise ValueError("Invalid filename")
    full_path.write_bytes(data)

    db.execute(
        "INSERT INTO attachments (id, message_id, filename, mime_type, size_bytes, storage_path)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (aid, message_id, safe_filename, mime_type, len(data), storage_path),
    )
    db.commit()
    return {
        "id": aid,
        "message_id": message_id,
        "filename": safe_filename,
        "mime_type": mime_type,
        "size_bytes": len(data),
        "storage_path": storage_path,
    }


def get_attachment(db: sqlite3.Connection, attachment_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM attachments WHERE id = ?", (attachment_id,))
    if not row:
        return None
    return dict(row)


def list_attachments(db: sqlite3.Connection, message_id: str) -> list[dict[str, Any]]:
    rows = db.execute_fetchall("SELECT * FROM attachments WHERE message_id = ?", (message_id,))
    return [dict(r) for r in rows]


# --- Users ---


def register_user(
    db: sqlite3.Connection,
    user_id: str,
    display_name: str,
    public_key: str,
) -> None:
    """Upsert a user into the users table."""
    now = _now()
    existing = db.execute_fetchone("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if existing:
        db.execute(
            "UPDATE users SET display_name = ?, public_key = ?, updated_at = ? WHERE user_id = ?",
            (display_name, public_key, now, user_id),
        )
    else:
        db.execute(
            "INSERT INTO users (user_id, display_name, public_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, display_name, public_key, now, now),
        )
    db.commit()


# --- Tool Calls ---


def create_tool_call(
    db: sqlite3.Connection,
    message_id: str,
    tool_name: str,
    server_name: str,
    input_data: dict[str, Any],
    tool_call_id: str | None = None,
    approval_decision: str | None = None,
) -> dict[str, Any]:
    tcid = tool_call_id or _uuid()
    now = _now()
    db.execute(
        "INSERT INTO tool_calls (id, message_id, tool_name, server_name, input_json, status, created_at,"
        " approval_decision) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tcid, message_id, tool_name, server_name, json.dumps(input_data), "pending", now, approval_decision),
    )
    db.commit()
    return {
        "id": tcid,
        "message_id": message_id,
        "tool_name": tool_name,
        "server_name": server_name,
        "input": input_data,
        "output": None,
        "status": "pending",
        "created_at": now,
        "approval_decision": approval_decision,
    }


def update_tool_call(
    db: sqlite3.Connection,
    tool_call_id: str,
    output_data: Any,
    status: str,
) -> None:
    db.execute(
        "UPDATE tool_calls SET output_json = ?, status = ? WHERE id = ?",
        (json.dumps(output_data), status, tool_call_id),
    )
    db.commit()


def list_tool_calls(db: sqlite3.Connection, message_id: str) -> list[dict[str, Any]]:
    rows = db.execute_fetchall("SELECT * FROM tool_calls WHERE message_id = ?", (message_id,))
    result = []
    for r in rows:
        d = dict(r)
        d["input"] = json.loads(d.pop("input_json"))
        output = d.pop("output_json")
        d["output"] = json.loads(output) if output else None
        result.append(d)
    return result


# --- Canvases ---


def create_canvas(
    db: sqlite3.Connection,
    conversation_id: str,
    title: str = "Untitled",
    content: str = "",
    language: str | None = None,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    cid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO canvases (id, conversation_id, title, content, language, version,"
        " created_at, updated_at, user_id, user_display_name)"
        " VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
        (cid, conversation_id, title, content, language, now, now, user_id, user_display_name),
    )
    db.commit()
    return {
        "id": cid,
        "conversation_id": conversation_id,
        "title": title,
        "content": content,
        "language": language,
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "user_id": user_id,
        "user_display_name": user_display_name,
    }


def get_canvas(db: sqlite3.Connection, canvas_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM canvases WHERE id = ?", (canvas_id,))
    if not row:
        return None
    return dict(row)


def get_canvas_for_conversation(db: sqlite3.Connection, conversation_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone(
        "SELECT * FROM canvases WHERE conversation_id = ? ORDER BY updated_at DESC LIMIT 1",
        (conversation_id,),
    )
    if not row:
        return None
    return dict(row)


def update_canvas(
    db: sqlite3.Connection,
    canvas_id: str,
    content: str | None = None,
    title: str | None = None,
) -> dict[str, Any] | None:
    canvas = get_canvas(db, canvas_id)
    if not canvas:
        return None
    now = _now()
    if content is not None and title is not None:
        db.execute(
            "UPDATE canvases SET version = version + 1, updated_at = ?, content = ?, title = ? WHERE id = ?",
            (now, content, title, canvas_id),
        )
    elif content is not None:
        db.execute(
            "UPDATE canvases SET version = version + 1, updated_at = ?, content = ? WHERE id = ?",
            (now, content, canvas_id),
        )
    elif title is not None:
        db.execute(
            "UPDATE canvases SET version = version + 1, updated_at = ?, title = ? WHERE id = ?",
            (now, title, canvas_id),
        )
    else:
        return canvas
    db.commit()
    return get_canvas(db, canvas_id)


def delete_canvas(db: sqlite3.Connection, canvas_id: str) -> bool:
    canvas = get_canvas(db, canvas_id)
    if not canvas:
        return False
    db.execute("DELETE FROM canvases WHERE id = ?", (canvas_id,))
    db.commit()
    return True


# --- Embeddings ---

_MAX_EMBEDDING_DIMENSIONS = 4096
_MAX_SEARCH_LIMIT = 1000


def _validate_embedding(embedding: list[float]) -> bytes:
    """Validate embedding vector and convert to bytes for sqlite-vec."""
    import math
    import struct

    if not embedding or len(embedding) > _MAX_EMBEDDING_DIMENSIONS:
        raise ValueError(f"Embedding must have 1-{_MAX_EMBEDDING_DIMENSIONS} dimensions, got {len(embedding)}")
    for i, val in enumerate(embedding):
        if not isinstance(val, (int, float)) or (isinstance(val, float) and not math.isfinite(val)):
            raise ValueError(f"Embedding dimension {i} is not a finite number")
    return struct.pack(f"{len(embedding)}f", *embedding)


def store_embedding(
    db: sqlite3.Connection,
    message_id: str,
    conversation_id: str,
    embedding: list[float],
    content_hash: str,
) -> None:
    """Store a message embedding in both metadata and vec0 tables."""
    from ..db import has_vec_support

    raw_conn = db._conn if hasattr(db, "_conn") else db
    if not has_vec_support(raw_conn):
        return

    now = _now()
    embedding_bytes = _validate_embedding(embedding)

    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO message_embeddings (message_id, conversation_id, chunk_index, content_hash,"
            " created_at) VALUES (?, ?, 0, ?, ?)",
            (message_id, conversation_id, content_hash, now),
        )
        # Delete existing vec entry for this message before inserting
        conn.execute("DELETE FROM vec_messages WHERE message_id = ?", (message_id,))
        conn.execute(
            "INSERT INTO vec_messages (embedding, message_id, conversation_id) VALUES (?, ?, ?)",
            (embedding_bytes, message_id, conversation_id),
        )


def search_similar_messages(
    db: sqlite3.Connection,
    embedding: list[float],
    limit: int = 20,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """Search for semantically similar messages using vec0 cosine similarity."""
    from ..db import has_vec_support

    raw_conn = db._conn if hasattr(db, "_conn") else db
    if not has_vec_support(raw_conn):
        return []

    limit = max(1, min(limit, _MAX_SEARCH_LIMIT))
    embedding_bytes = _validate_embedding(embedding)

    if conversation_id:
        rows = db.execute_fetchall(
            """
            WITH knn AS (
                SELECT message_id, conversation_id, distance
                FROM vec_messages
                WHERE embedding MATCH ? AND k = ? AND conversation_id = ?
            )
            SELECT knn.message_id, knn.conversation_id, knn.distance, m.content, m.role
            FROM knn
            LEFT JOIN messages m ON m.id = knn.message_id
            """,
            (embedding_bytes, limit, conversation_id),
        )
    else:
        rows = db.execute_fetchall(
            """
            WITH knn AS (
                SELECT message_id, conversation_id, distance
                FROM vec_messages
                WHERE embedding MATCH ? AND k = ?
            )
            SELECT knn.message_id, knn.conversation_id, knn.distance, m.content, m.role
            FROM knn
            LEFT JOIN messages m ON m.id = knn.message_id
            """,
            (embedding_bytes, limit),
        )

    return [
        {
            "message_id": dict(r)["message_id"],
            "conversation_id": dict(r)["conversation_id"],
            "content": dict(r)["content"],
            "role": dict(r)["role"],
            "distance": dict(r)["distance"],
        }
        for r in rows
    ]


def get_unembedded_messages(db: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Get messages that don't have embeddings yet."""
    rows = db.execute_fetchall(
        """
        SELECT m.id, m.conversation_id, m.content, m.role
        FROM messages m
        LEFT JOIN message_embeddings me ON me.message_id = m.id
        WHERE me.message_id IS NULL AND m.role IN ('user', 'assistant')
        ORDER BY m.created_at
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(r) for r in rows]


_VALID_EMBEDDING_STATUSES = frozenset({"skipped", "failed", "embedded"})


def mark_embedding_skipped(
    db: sqlite3.Connection,
    message_id: str,
    conversation_id: str,
    content_hash: str,
    status: str = "skipped",
) -> None:
    """Write a sentinel row to message_embeddings so the message is excluded from future queries.

    No vector is inserted into vec_messages — only the metadata row is written.
    """
    if status not in _VALID_EMBEDDING_STATUSES:
        raise ValueError(f"Invalid embedding status: {status!r}")
    now = _now()
    db.execute(
        "INSERT OR IGNORE INTO message_embeddings"
        " (message_id, conversation_id, chunk_index, content_hash, status, created_at)"
        " VALUES (?, ?, 0, ?, ?, ?)",
        (message_id, conversation_id, content_hash, status, now),
    )
    db.commit()


def delete_embeddings_for_conversation(db: sqlite3.Connection, conversation_id: str) -> None:
    """Delete all embeddings for a conversation."""
    from ..db import has_vec_support

    raw_conn = db._conn if hasattr(db, "_conn") else db
    if not has_vec_support(raw_conn):
        db.execute("DELETE FROM message_embeddings WHERE conversation_id = ?", (conversation_id,))
        db.commit()
        return

    with db.transaction() as conn:
        conn.execute("DELETE FROM vec_messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM message_embeddings WHERE conversation_id = ?", (conversation_id,))


def get_embedding_stats(db: sqlite3.Connection) -> dict[str, Any]:
    """Get embedding statistics."""
    total_row = db.execute_fetchone("SELECT COUNT(*) FROM messages WHERE role IN ('user', 'assistant')")
    total_messages = total_row[0] if total_row else 0

    embedded_row = db.execute_fetchone("SELECT COUNT(*) FROM message_embeddings")
    embedded_messages = embedded_row[0] if embedded_row else 0

    return {
        "total_messages": total_messages,
        "embedded_messages": embedded_messages,
        "pending_messages": total_messages - embedded_messages,
    }


# --- Sources ---


def _build_source_set_clause(updates: dict[str, Any]) -> tuple[str, list[Any]]:
    """Build a safe SET clause for source updates."""
    parts: list[str] = []
    params: list[Any] = []
    for col, val in updates.items():
        if col not in _ALLOWED_SOURCE_UPDATE_COLUMNS:
            raise ValueError(f"Column {col!r} not in allowed source update columns")
        parts.append(f"{col} = ?")
        params.append(val)
    return ", ".join(parts), params


def chunk_text(text: str, max_size: int = 1000, overlap: int = 200) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    if not text or not text.strip():
        return []
    if len(text) <= max_size:
        return [text]

    sentence_endings = re.compile(r"(?<=[.!?])\s+")
    sentences = sentence_endings.split(text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if not sentence.strip():
            continue
        if current and len(current) + len(sentence) + 1 > max_size:
            chunks.append(current.strip())
            # Overlap: keep the tail of the current chunk
            if overlap > 0 and len(current) > overlap:
                current = current[-overlap:] + " " + sentence
            else:
                current = sentence
        else:
            current = (current + " " + sentence).strip() if current else sentence

    if current.strip():
        chunks.append(current.strip())

    return chunks


def create_source(
    db: sqlite3.Connection,
    source_type: str,
    title: str,
    content: str | None = None,
    mime_type: str | None = None,
    filename: str | None = None,
    url: str | None = None,
    storage_path: str | None = None,
    size_bytes: int | None = None,
    content_hash: str | None = None,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    if source_type not in ("file", "text", "url"):
        raise ValueError(f"Invalid source type: {source_type!r}")
    sid = _uuid()
    now = _now()
    if content and not content_hash:
        import hashlib

        content_hash = hashlib.sha256(content.encode()).hexdigest()

    db.execute(
        "INSERT INTO sources (id, type, title, content, mime_type, filename, url, storage_path,"
        " size_bytes, content_hash, user_id, user_display_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sid,
            source_type,
            title,
            content,
            mime_type,
            filename,
            url,
            storage_path,
            size_bytes,
            content_hash,
            user_id,
            user_display_name,
            now,
            now,
        ),
    )
    db.commit()

    source = {
        "id": sid,
        "type": source_type,
        "title": title,
        "content": content,
        "mime_type": mime_type,
        "filename": filename,
        "url": url,
        "storage_path": storage_path,
        "size_bytes": size_bytes,
        "content_hash": content_hash,
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
        "updated_at": now,
    }

    # Auto-chunk text content
    if content:
        chunks = chunk_text(content)
        if chunks:
            create_source_chunks(db, sid, chunks)

    return source


def get_source(db: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM sources WHERE id = ?", (source_id,))
    if not row:
        return None
    source = dict(row)
    source["tags"] = get_source_tags(db, source_id)
    source["chunks"] = list_source_chunks(db, source_id)
    return source


def list_sources(
    db: sqlite3.Connection,
    search: str | None = None,
    source_type: str | None = None,
    tag_id: str | None = None,
    group_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []

    if search:
        conditions.append("(s.title LIKE ? ESCAPE '\\' OR s.content LIKE ? ESCAPE '\\')")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        params.extend([like, like])

    if source_type:
        conditions.append("s.type = ?")
        params.append(source_type)

    if tag_id:
        conditions.append("EXISTS (SELECT 1 FROM source_tags st WHERE st.source_id = s.id AND st.tag_id = ?)")
        params.append(tag_id)

    if group_id:
        conditions.append(
            "EXISTS (SELECT 1 FROM source_group_members sgm WHERE sgm.source_id = s.id AND sgm.group_id = ?)"
        )
        params.append(group_id)

    if project_id:
        conditions.append(
            """EXISTS (
                SELECT 1 FROM project_sources ps WHERE ps.project_id = ? AND (
                    ps.source_id = s.id OR
                    EXISTS (SELECT 1 FROM source_group_members sgm2
                            WHERE sgm2.group_id = ps.group_id AND sgm2.source_id = s.id) OR
                    EXISTS (SELECT 1 FROM source_tags st2
                            JOIN tags t2 ON t2.id = st2.tag_id
                            WHERE st2.source_id = s.id AND t2.name = ps.tag_filter)
                )
            )"""
        )
        params.append(project_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])

    rows = db.execute_fetchall(
        f"SELECT s.* FROM sources s {where} ORDER BY s.updated_at DESC LIMIT ? OFFSET ?",
        tuple(params),
    )
    return [dict(r) for r in rows]


def update_source(
    db: sqlite3.Connection,
    source_id: str,
    title: str | None = None,
    content: str | None = None,
    url: str | None = None,
) -> dict[str, Any] | None:
    source = db.execute_fetchone("SELECT * FROM sources WHERE id = ?", (source_id,))
    if not source:
        return None
    cols: dict[str, Any] = {"updated_at": _now()}
    if title is not None:
        cols["title"] = title
    if content is not None:
        import hashlib

        cols["content"] = content
        cols["content_hash"] = hashlib.sha256(content.encode()).hexdigest()
    if url is not None:
        cols["url"] = url
    set_clause, params = _build_source_set_clause(cols)
    params.append(source_id)
    db.execute(f"UPDATE sources SET {set_clause} WHERE id = ?", tuple(params))
    db.commit()

    # Re-chunk if content changed
    if content is not None:
        db.execute("DELETE FROM source_chunks WHERE source_id = ?", (source_id,))
        db.commit()
        chunks = chunk_text(content)
        if chunks:
            create_source_chunks(db, source_id, chunks)

    return get_source(db, source_id)


def delete_source(db: sqlite3.Connection, source_id: str, data_dir: Path | None = None) -> bool:
    source = db.execute_fetchone("SELECT * FROM sources WHERE id = ?", (source_id,))
    if not source:
        return False
    source = dict(source)

    # Remove file from disk if it exists
    if data_dir and source.get("storage_path"):
        file_path = safe_resolve_pathlib(data_dir / source["storage_path"])
        if file_path.is_relative_to(safe_resolve_pathlib(data_dir)) and file_path.exists():
            file_path.unlink()
        # Remove the source directory if empty
        source_dir = data_dir / "sources" / source_id
        if source_dir.exists():
            try:
                source_dir.rmdir()
            except OSError:
                pass

    db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    db.commit()
    return True


def save_source_file(
    db: sqlite3.Connection,
    title: str,
    filename: str,
    mime_type: str,
    data: bytes,
    data_dir: Path,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    """Save a file as a source with MIME validation."""
    _validate_upload(mime_type, data, filename)

    import hashlib

    safe_filename = _sanitize_filename(filename)
    sid = _uuid()
    sources_dir = data_dir / "sources" / sid
    sources_dir.mkdir(parents=True, exist_ok=True)
    file_uuid = _uuid()
    storage_path = f"sources/{sid}/{file_uuid}_{safe_filename}"
    full_path = safe_resolve_pathlib(data_dir / storage_path)
    if not full_path.is_relative_to(safe_resolve_pathlib(data_dir)):
        raise ValueError("Invalid filename")
    full_path.write_bytes(data)

    content_hash = hashlib.sha256(data).hexdigest()

    # Extract text content for text-based files
    content = None
    if mime_type.startswith("text/") or mime_type in (
        "application/json",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
        "application/x-python-code",
    ):
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            pass

    now = _now()
    db.execute(
        "INSERT INTO sources (id, type, title, content, mime_type, filename, storage_path,"
        " size_bytes, content_hash, user_id, user_display_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sid,
            "file",
            title,
            content,
            mime_type,
            safe_filename,
            storage_path,
            len(data),
            content_hash,
            user_id,
            user_display_name,
            now,
            now,
        ),
    )
    db.commit()

    # Auto-chunk text content
    if content:
        chunks = chunk_text(content)
        if chunks:
            create_source_chunks(db, sid, chunks)

    return {
        "id": sid,
        "type": "file",
        "title": title,
        "content": content,
        "mime_type": mime_type,
        "filename": safe_filename,
        "storage_path": storage_path,
        "size_bytes": len(data),
        "content_hash": content_hash,
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
        "updated_at": now,
    }


# --- Source Chunks ---


def create_source_chunks(db: sqlite3.Connection, source_id: str, chunks: list[str]) -> list[dict[str, Any]]:
    """Bulk insert source chunks with content hashing."""
    import hashlib

    now = _now()
    result = []
    for i, chunk_content in enumerate(chunks):
        chunk_id = _uuid()
        content_hash = hashlib.sha256(chunk_content.encode()).hexdigest()
        db.execute(
            "INSERT INTO source_chunks (id, source_id, chunk_index, content, content_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (chunk_id, source_id, i, chunk_content, content_hash, now),
        )
        result.append(
            {
                "id": chunk_id,
                "source_id": source_id,
                "chunk_index": i,
                "content": chunk_content,
                "content_hash": content_hash,
                "created_at": now,
            }
        )
    db.commit()
    return result


def list_source_chunks(db: sqlite3.Connection, source_id: str) -> list[dict[str, Any]]:
    rows = db.execute_fetchall(
        "SELECT * FROM source_chunks WHERE source_id = ? ORDER BY chunk_index",
        (source_id,),
    )
    return [dict(r) for r in rows]


def get_unembedded_source_chunks(db: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Get source chunks that don't have embeddings yet."""
    rows = db.execute_fetchall(
        """
        SELECT sc.id, sc.source_id, sc.content, sc.content_hash
        FROM source_chunks sc
        LEFT JOIN source_chunk_embeddings sce ON sce.chunk_id = sc.id
        WHERE sce.chunk_id IS NULL
        ORDER BY sc.created_at
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(r) for r in rows]


def mark_source_chunk_embedding_skipped(
    db: sqlite3.Connection,
    chunk_id: str,
    source_id: str,
    content_hash: str,
    status: str = "skipped",
) -> None:
    """Write a sentinel row to source_chunk_embeddings so the chunk is excluded from future queries."""
    if status not in _VALID_EMBEDDING_STATUSES:
        raise ValueError(f"Invalid embedding status: {status!r}")
    now = _now()
    db.execute(
        "INSERT OR IGNORE INTO source_chunk_embeddings"
        " (chunk_id, source_id, content_hash, status, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (chunk_id, source_id, content_hash, status, now),
    )
    db.commit()


# --- Source Tags ---


def get_source_tags(db: sqlite3.Connection, source_id: str) -> list[dict[str, Any]]:
    rows = db.execute_fetchall(
        "SELECT t.* FROM tags t JOIN source_tags st ON st.tag_id = t.id WHERE st.source_id = ? ORDER BY t.name",
        (source_id,),
    )
    return [dict(r) for r in rows]


def add_tag_to_source(db: sqlite3.Connection, source_id: str, tag_id: str) -> bool:
    try:
        db.execute(
            "INSERT OR IGNORE INTO source_tags (source_id, tag_id) VALUES (?, ?)",
            (source_id, tag_id),
        )
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def remove_tag_from_source(db: sqlite3.Connection, source_id: str, tag_id: str) -> bool:
    db.execute(
        "DELETE FROM source_tags WHERE source_id = ? AND tag_id = ?",
        (source_id, tag_id),
    )
    db.commit()
    return True


# --- Source Groups ---


def create_source_group(
    db: sqlite3.Connection,
    name: str,
    description: str = "",
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    gid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO source_groups (id, name, description, user_id, user_display_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (gid, name, description, user_id, user_display_name, now, now),
    )
    db.commit()
    return {
        "id": gid,
        "name": name,
        "description": description,
        "user_id": user_id,
        "user_display_name": user_display_name,
        "created_at": now,
        "updated_at": now,
    }


def list_source_groups(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute_fetchall("SELECT * FROM source_groups ORDER BY name")
    return [dict(r) for r in rows]


def get_source_group(db: sqlite3.Connection, group_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM source_groups WHERE id = ?", (group_id,))
    if not row:
        return None
    return dict(row)


def update_source_group(
    db: sqlite3.Connection,
    group_id: str,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    group = get_source_group(db, group_id)
    if not group:
        return None
    now = _now()
    if name is not None:
        db.execute("UPDATE source_groups SET name = ?, updated_at = ? WHERE id = ?", (name, now, group_id))
    if description is not None:
        db.execute(
            "UPDATE source_groups SET description = ?, updated_at = ? WHERE id = ?",
            (description, now, group_id),
        )
    db.commit()
    return get_source_group(db, group_id)


def delete_source_group(db: sqlite3.Connection, group_id: str) -> bool:
    group = get_source_group(db, group_id)
    if not group:
        return False
    db.execute("DELETE FROM source_groups WHERE id = ?", (group_id,))
    db.commit()
    return True


def add_source_to_group(db: sqlite3.Connection, group_id: str, source_id: str) -> bool:
    try:
        db.execute(
            "INSERT OR IGNORE INTO source_group_members (group_id, source_id) VALUES (?, ?)",
            (group_id, source_id),
        )
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def remove_source_from_group(db: sqlite3.Connection, group_id: str, source_id: str) -> bool:
    db.execute(
        "DELETE FROM source_group_members WHERE group_id = ? AND source_id = ?",
        (group_id, source_id),
    )
    db.commit()
    return True


# --- Project Sources ---


def link_source_to_project(
    db: sqlite3.Connection,
    project_id: str,
    source_id: str | None = None,
    group_id: str | None = None,
    tag_filter: str | None = None,
) -> dict[str, Any]:
    non_null = sum(1 for v in (source_id, group_id, tag_filter) if v is not None)
    if non_null != 1:
        raise ValueError("Exactly one of source_id, group_id, or tag_filter must be provided")
    now = _now()
    db.execute(
        "INSERT OR IGNORE INTO project_sources"
        " (project_id, source_id, group_id, tag_filter, created_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, source_id, group_id, tag_filter, now),
    )
    db.commit()
    return {
        "project_id": project_id,
        "source_id": source_id,
        "group_id": group_id,
        "tag_filter": tag_filter,
        "created_at": now,
    }


def unlink_source_from_project(
    db: sqlite3.Connection,
    project_id: str,
    source_id: str | None = None,
    group_id: str | None = None,
    tag_filter: str | None = None,
) -> bool:
    if source_id:
        db.execute(
            "DELETE FROM project_sources WHERE project_id = ? AND source_id = ?",
            (project_id, source_id),
        )
    elif group_id:
        db.execute(
            "DELETE FROM project_sources WHERE project_id = ? AND group_id = ?",
            (project_id, group_id),
        )
    elif tag_filter:
        db.execute(
            "DELETE FROM project_sources WHERE project_id = ? AND tag_filter = ?",
            (project_id, tag_filter),
        )
    else:
        return False
    db.commit()
    return True


def get_project_sources(db: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """Resolve all project source links to a flat list of sources."""
    links = db.execute_fetchall(
        "SELECT * FROM project_sources WHERE project_id = ?",
        (project_id,),
    )
    seen: set[str] = set()
    sources: list[dict[str, Any]] = []

    for link in links:
        link = dict(link)
        if link["source_id"]:
            if link["source_id"] not in seen:
                row = db.execute_fetchone("SELECT * FROM sources WHERE id = ?", (link["source_id"],))
                if row:
                    seen.add(link["source_id"])
                    sources.append(dict(row))
        elif link["group_id"]:
            members = db.execute_fetchall(
                "SELECT s.* FROM sources s JOIN source_group_members sgm ON sgm.source_id = s.id"
                " WHERE sgm.group_id = ?",
                (link["group_id"],),
            )
            for m in members:
                m = dict(m)
                if m["id"] not in seen:
                    seen.add(m["id"])
                    sources.append(m)
        elif link["tag_filter"]:
            tagged = db.execute_fetchall(
                "SELECT s.* FROM sources s JOIN source_tags st ON st.source_id = s.id"
                " JOIN tags t ON t.id = st.tag_id WHERE t.name = ?",
                (link["tag_filter"],),
            )
            for t in tagged:
                t = dict(t)
                if t["id"] not in seen:
                    seen.add(t["id"])
                    sources.append(t)

    return sources


# --- Dual Citizenship ---


def create_source_from_attachment(
    db: sqlite3.Connection,
    attachment_id: str,
    data_dir: Path,
    user_id: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any] | None:
    """Create a source from an existing message attachment (dual citizenship)."""
    att = get_attachment(db, attachment_id)
    if not att:
        return None

    import hashlib

    # Read file content for text-based files
    content = None
    full_path = data_dir / att["storage_path"]
    if full_path.exists():
        mime = att["mime_type"]
        if mime.startswith("text/") or mime in (
            "application/json",
            "application/javascript",
            "application/x-yaml",
            "application/yaml",
            "application/x-python-code",
        ):
            try:
                content = full_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                pass

    content_hash = None
    if content:
        content_hash = hashlib.sha256(content.encode()).hexdigest()
    elif full_path.exists():
        content_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()

    source = create_source(
        db,
        source_type="file",
        title=att["filename"],
        content=content,
        mime_type=att["mime_type"],
        filename=att["filename"],
        storage_path=att["storage_path"],
        size_bytes=att["size_bytes"],
        content_hash=content_hash,
        user_id=user_id,
        user_display_name=user_display_name,
    )

    # Create the bridge record
    db.execute(
        "INSERT OR IGNORE INTO source_attachments (source_id, attachment_id) VALUES (?, ?)",
        (source["id"], attachment_id),
    )
    db.commit()

    return source


# --- Source Chunk Embeddings ---


def store_source_chunk_embedding(
    db: sqlite3.Connection,
    chunk_id: str,
    source_id: str,
    embedding: list[float],
    content_hash: str,
) -> None:
    """Store a source chunk embedding in both metadata and vec0 tables."""
    from ..db import has_vec_support

    raw_conn = db._conn if hasattr(db, "_conn") else db
    if not has_vec_support(raw_conn):
        return

    now = _now()
    embedding_bytes = _validate_embedding(embedding)

    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO source_chunk_embeddings (chunk_id, source_id, content_hash, created_at)"
            " VALUES (?, ?, ?, ?)",
            (chunk_id, source_id, content_hash, now),
        )
        conn.execute("DELETE FROM vec_source_chunks WHERE chunk_id = ?", (chunk_id,))
        conn.execute(
            "INSERT INTO vec_source_chunks (embedding, chunk_id, source_id) VALUES (?, ?, ?)",
            (embedding_bytes, chunk_id, source_id),
        )


def search_similar_source_chunks(
    db: sqlite3.Connection,
    embedding: list[float],
    limit: int = 20,
    source_id: str | None = None,
) -> list[dict[str, Any]]:
    """Search for semantically similar source chunks using vec0 cosine similarity."""
    from ..db import has_vec_support

    raw_conn = db._conn if hasattr(db, "_conn") else db
    if not has_vec_support(raw_conn):
        return []

    limit = max(1, min(limit, _MAX_SEARCH_LIMIT))
    embedding_bytes = _validate_embedding(embedding)

    if source_id:
        rows = db.execute_fetchall(
            """
            WITH knn AS (
                SELECT chunk_id, source_id, distance
                FROM vec_source_chunks
                WHERE embedding MATCH ? AND k = ? AND source_id = ?
            )
            SELECT knn.chunk_id, knn.source_id, knn.distance, sc.content, sc.chunk_index
            FROM knn
            LEFT JOIN source_chunks sc ON sc.id = knn.chunk_id
            """,
            (embedding_bytes, limit, source_id),
        )
    else:
        rows = db.execute_fetchall(
            """
            WITH knn AS (
                SELECT chunk_id, source_id, distance
                FROM vec_source_chunks
                WHERE embedding MATCH ? AND k = ?
            )
            SELECT knn.chunk_id, knn.source_id, knn.distance, sc.content, sc.chunk_index
            FROM knn
            LEFT JOIN source_chunks sc ON sc.id = knn.chunk_id
            """,
            (embedding_bytes, limit),
        )

    return [
        {
            "chunk_id": dict(r)["chunk_id"],
            "source_id": dict(r)["source_id"],
            "content": dict(r)["content"],
            "chunk_index": dict(r)["chunk_index"],
            "distance": dict(r)["distance"],
        }
        for r in rows
    ]
