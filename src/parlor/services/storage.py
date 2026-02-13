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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# --- Conversations ---


def create_conversation(db: sqlite3.Connection, title: str = "New Conversation") -> dict[str, Any]:
    cid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (cid, title, now, now),
    )
    db.commit()
    return {"id": cid, "title": title, "created_at": now, "updated_at": now}


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
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if search:
        safe_search = _sanitize_fts_query(search)
        rows = db.execute_fetchall(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) as message_count
            FROM conversations c
            JOIN conversations_fts fts ON fts.conversation_id = c.id
            WHERE conversations_fts MATCH ?
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (safe_search, limit, offset),
        )
    else:
        rows = db.execute_fetchall(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) as message_count
            FROM conversations c
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
    return [dict(r) for r in rows]


def update_conversation_title(db: sqlite3.Connection, conversation_id: str, title: str) -> dict[str, Any] | None:
    now = _now()
    db.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (title, now, conversation_id),
    )
    db.commit()
    return get_conversation(db, conversation_id)


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
            "INSERT INTO messages (id, conversation_id, role, content, created_at, position) VALUES (?, ?, ?, ?, ?, ?)",
            (mid, conversation_id, role, content, now, position),
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


# --- Tool Calls ---


def create_tool_call(
    db: sqlite3.Connection,
    message_id: str,
    tool_name: str,
    server_name: str,
    input_data: dict[str, Any],
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    tcid = tool_call_id or _uuid()
    now = _now()
    db.execute(
        "INSERT INTO tool_calls (id, message_id, tool_name, server_name, input_json, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tcid, message_id, tool_name, server_name, json.dumps(input_data), "pending", now),
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
