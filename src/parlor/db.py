"""SQLite database initialization and connection management."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    position INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, position);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    server_name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending', 'success', 'error')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
    conversation_id UNINDEXED,
    title,
    content,
    tokenize='porter unicode61'
);
"""

_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS fts_conversations_insert
AFTER INSERT ON conversations
BEGIN
    INSERT INTO conversations_fts(conversation_id, title, content)
    VALUES (NEW.id, NEW.title, '');
END;

CREATE TRIGGER IF NOT EXISTS fts_conversations_update
AFTER UPDATE OF title ON conversations
BEGIN
    UPDATE conversations_fts SET title = NEW.title
    WHERE conversation_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS fts_conversations_delete
AFTER DELETE ON conversations
BEGIN
    DELETE FROM conversations_fts WHERE conversation_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS fts_messages_insert
AFTER INSERT ON messages
BEGIN
    UPDATE conversations_fts
    SET content = content || ' ' || NEW.content
    WHERE conversation_id = NEW.conversation_id;
END;

CREATE TRIGGER IF NOT EXISTS fts_messages_delete
AFTER DELETE ON messages
BEGIN
    UPDATE conversations_fts
    SET content = (
        SELECT COALESCE(GROUP_CONCAT(content, ' '), '')
        FROM messages WHERE conversation_id = OLD.conversation_id
    )
    WHERE conversation_id = OLD.conversation_id;
END;
"""


_db_lock = threading.Lock()


class ThreadSafeConnection:
    """Wrapper around sqlite3.Connection that serializes all access with a lock."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = _db_lock

    def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, parameters)

    def executescript(self, sql: str) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executescript(sql)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value


def init_db(db_path: Path) -> ThreadSafeConnection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript(_SCHEMA)

    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass

    conn.commit()
    return ThreadSafeConnection(conn)


def get_db(db_path: Path) -> ThreadSafeConnection:
    return init_db(db_path)
