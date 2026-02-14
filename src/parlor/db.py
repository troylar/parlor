"""SQLite database initialization and connection management."""

from __future__ import annotations

import sqlite3
import stat
import threading
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    model TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT DEFAULT NULL,
    project_id TEXT DEFAULT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    collapsed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#3b82f6',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_tags (
    conversation_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (conversation_id, tag_id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    model TEXT DEFAULT NULL,
    project_id TEXT DEFAULT NULL,
    folder_id TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
    FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL
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

    def execute_fetchone(self, sql: str, parameters: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, parameters).fetchone()

    def execute_fetchall(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, parameters).fetchall()

    def executescript(self, sql: str) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executescript(sql)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self):
        """Hold the lock for the entire transaction, auto-commit or rollback."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @property
    def row_factory(self):
        with self._lock:
            return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        with self._lock:
            self._conn.row_factory = value


def _restrict_file_permissions(path: Path) -> None:
    """Set file to owner-only read/write (0o600)."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def init_db(db_path: Path) -> ThreadSafeConnection:
    is_new = not db_path.exists()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)

    if is_new:
        _restrict_file_permissions(db_path)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript(_SCHEMA)

    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass

    _run_migrations(conn)

    conn.commit()

    # Also restrict WAL/SHM sidecar files
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.parent / (db_path.name + suffix)
        if sidecar.exists():
            _restrict_file_permissions(sidecar)

    return ThreadSafeConnection(conn)


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply schema migrations for existing databases."""
    cursor = conn.execute("PRAGMA table_info(conversations)")
    cols = {row[1] for row in cursor.fetchall()}

    if "model" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN model TEXT DEFAULT NULL")

    if "project_id" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT DEFAULT NULL")

    if "folder_id" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN folder_id TEXT DEFAULT NULL")

    # Ensure folders table exists for existing databases
    conn.execute(
        """CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id TEXT DEFAULT NULL,
            project_id TEXT DEFAULT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            collapsed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
        )"""
    )

    # Add parent_id to folders if missing
    folder_cursor = conn.execute("PRAGMA table_info(folders)")
    folder_cols = {row[1] for row in folder_cursor.fetchall()}
    if "parent_id" not in folder_cols:
        conn.execute("ALTER TABLE folders ADD COLUMN parent_id TEXT DEFAULT NULL")

    # Ensure tags tables exist
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tags (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            color TEXT NOT NULL DEFAULT '#3b82f6',
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS conversation_tags (
            conversation_id TEXT NOT NULL,
            tag_id TEXT NOT NULL,
            PRIMARY KEY (conversation_id, tag_id),
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )"""
    )


def get_db(db_path: Path) -> ThreadSafeConnection:
    return init_db(db_path)


class DatabaseManager:
    """Manages personal + shared database connections."""

    def __init__(self) -> None:
        self._databases: dict[str, ThreadSafeConnection] = {}
        self._paths: dict[str, Path] = {}
        self._personal_name = "personal"

    def add(self, name: str, db_path: Path) -> None:
        self._paths[name] = db_path
        self._databases[name] = init_db(db_path)

    def get(self, name: str | None = None) -> ThreadSafeConnection:
        key = name or self._personal_name
        if key not in self._databases:
            raise KeyError(f"Database '{key}' not found")
        return self._databases[key]

    @property
    def personal(self) -> ThreadSafeConnection:
        return self._databases[self._personal_name]

    def list_databases(self) -> list[dict[str, str]]:
        return [
            {"name": name, "path": str(self._paths[name])}
            for name in self._databases
        ]

    def remove(self, name: str) -> None:
        if name in self._databases:
            self._databases[name].close()
            del self._databases[name]
            del self._paths[name]

    def close_all(self) -> None:
        for db in self._databases.values():
            db.close()
        self._databases.clear()
