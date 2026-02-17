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
            file_path = (data_dir / dict(att)["storage_path"]).resolve()
            if file_path.is_relative_to(data_dir.resolve()) and file_path.exists():
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
    "text/plain",
    "text/markdown",
    "text/css",
    "text/csv",
    "text/xml",
    "application/json",
    "application/pdf",
    "application/x-yaml",
    "application/yaml",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/javascript",
    "text/javascript",
    "application/x-python-code",
    "text/x-python",
    "application/octet-stream",
}

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB


def _sanitize_filename(filename: str) -> str:
    """Strip path components and dangerous characters from filename."""
    safe = os.path.basename(filename).replace("\x00", "")
    safe = re.sub(r"[^\w.\-]", "_", safe)
    return safe or "unnamed"


def save_attachment(
    db: sqlite3.Connection,
    message_id: str,
    conversation_id: str,
    filename: str,
    mime_type: str,
    data: bytes,
    data_dir: Path,
) -> dict[str, Any]:
    if len(data) > MAX_ATTACHMENT_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_ATTACHMENT_SIZE // (1024 * 1024)} MB")

    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError(f"Unsupported file type: {mime_type}")

    # Magic-byte verification for binary formats
    guess = filetype.guess(data)
    if guess is not None:
        if guess.mime != mime_type:
            logger.warning("MIME mismatch: claimed %s, detected %s for %s", mime_type, guess.mime, filename)
            raise ValueError("File content does not match declared type")
    elif not mime_type.startswith("text/") and mime_type not in (
        "application/json",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
        "application/x-python-code",
    ):
        logger.warning("Cannot verify binary MIME type %s for %s", mime_type, filename)
        raise ValueError("Cannot verify file content type")

    safe_filename = _sanitize_filename(filename)
    aid = _uuid()
    attachments_dir = data_dir / "attachments" / conversation_id
    attachments_dir.mkdir(parents=True, exist_ok=True)
    storage_path = f"attachments/{conversation_id}/{aid}_{safe_filename}"
    full_path = (data_dir / storage_path).resolve()
    if not full_path.is_relative_to(data_dir.resolve()):
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
