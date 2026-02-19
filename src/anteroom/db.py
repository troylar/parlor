"""SQLite database initialization and connection management."""

from __future__ import annotations

import logging
import re
import sqlite3
import stat
import threading
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    public_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    model TEXT DEFAULT NULL,
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
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
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#3b82f6',
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
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
    type TEXT NOT NULL DEFAULT 'chat' CHECK(type IN ('chat', 'note', 'document')),
    model TEXT DEFAULT NULL,
    project_id TEXT DEFAULT NULL,
    folder_id TEXT DEFAULT NULL,
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
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
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
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
    approval_decision TEXT DEFAULT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS canvases (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'Untitled',
    content TEXT NOT NULL DEFAULT '',
    language TEXT DEFAULT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_change_log_id ON change_log(id);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN ('file', 'text', 'url')),
    title TEXT NOT NULL,
    content TEXT,
    mime_type TEXT,
    filename TEXT,
    url TEXT,
    storage_path TEXT,
    size_bytes INTEGER,
    content_hash TEXT,
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_chunks (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_source_chunks_source
    ON source_chunks(source_id, chunk_index);

CREATE TABLE IF NOT EXISTS source_tags (
    source_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (source_id, tag_id),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_group_members (
    group_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    PRIMARY KEY (group_id, source_id),
    FOREIGN KEY (group_id) REFERENCES source_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_sources (
    project_id TEXT NOT NULL,
    source_id TEXT,
    group_id TEXT,
    tag_filter TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES source_groups(id) ON DELETE CASCADE,
    CHECK (
        (source_id IS NOT NULL AND group_id IS NULL AND tag_filter IS NULL) OR
        (source_id IS NULL AND group_id IS NOT NULL AND tag_filter IS NULL) OR
        (source_id IS NULL AND group_id IS NULL AND tag_filter IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS source_attachments (
    source_id TEXT NOT NULL,
    attachment_id TEXT NOT NULL,
    PRIMARY KEY (source_id, attachment_id),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY (attachment_id) REFERENCES attachments(id) ON DELETE CASCADE
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

_VEC_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_embeddings (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'embedded',
    created_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_chunk_embeddings (
    chunk_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'embedded',
    created_at TEXT NOT NULL,
    FOREIGN KEY (chunk_id) REFERENCES source_chunks(id) ON DELETE CASCADE
);
"""


def _make_vec_schema(dimensions: int = 384) -> str:
    """Build vec_messages DDL with the configured embedding dimensions."""
    if not isinstance(dimensions, int) or not (1 <= dimensions <= 4096):
        raise ValueError(f"Invalid embedding dimensions: {dimensions!r}")
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_messages USING vec0(
    embedding float[{dimensions}],
    +message_id TEXT,
    conversation_id TEXT
);
"""


def _make_source_vec_schema(dimensions: int = 384) -> str:
    """Build vec_source_chunks DDL with the configured embedding dimensions."""
    if not isinstance(dimensions, int) or not (1 <= dimensions <= 4096):
        raise ValueError(f"Invalid embedding dimensions: {dimensions!r}")
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_source_chunks USING vec0(
    embedding float[{dimensions}],
    +chunk_id TEXT,
    source_id TEXT
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


def _ensure_vec_table_dimensions(
    conn: sqlite3.Connection,
    vec_table: str,
    metadata_table: str,
    dimensions: int,
) -> None:
    """Recreate a vec0 table if the configured dimensions differ from the existing table."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if vec_table not in tables:
        return
    try:
        ddl_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (vec_table,)).fetchone()
        if ddl_row and ddl_row[0]:
            m = re.search(r"float\[(\d+)\]", ddl_row[0])
            if m:
                existing_dims = int(m.group(1))
                if existing_dims != dimensions:
                    logger.warning(
                        "Embedding dimensions changed (%d -> %d). Dropping %s — "
                        "existing embeddings will be regenerated.",
                        existing_dims,
                        dimensions,
                        vec_table,
                    )
                    conn.execute(f"DROP TABLE {vec_table}")
                    conn.execute(f"DELETE FROM {metadata_table}")
    except (sqlite3.OperationalError, Exception):
        logger.debug("Could not check %s dimensions", vec_table, exc_info=True)


def _ensure_vec_dimensions(conn: sqlite3.Connection, dimensions: int) -> None:
    """Recreate vec tables if the configured dimensions differ from existing tables."""
    if not has_vec_support(conn):
        return
    _ensure_vec_table_dimensions(conn, "vec_messages", "message_embeddings", dimensions)
    _ensure_vec_table_dimensions(conn, "vec_source_chunks", "source_chunk_embeddings", dimensions)


def init_db(db_path: Path, vec_dimensions: int = 384) -> ThreadSafeConnection:
    is_new = not db_path.exists()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)

    if is_new:
        _restrict_file_permissions(db_path)

    # Load sqlite-vec extension if available
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, Exception):
        pass  # sqlite-vec not available; vector search disabled

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript(_SCHEMA)

    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass

    # Create vec tables (metadata + virtual table)
    try:
        conn.executescript(_VEC_METADATA_SCHEMA)
    except sqlite3.OperationalError:
        pass
    _ensure_vec_dimensions(conn, vec_dimensions)
    try:
        conn.executescript(_make_vec_schema(vec_dimensions))
    except sqlite3.OperationalError:
        pass  # sqlite-vec not loaded
    try:
        conn.executescript(_make_source_vec_schema(vec_dimensions))
    except sqlite3.OperationalError:
        pass  # sqlite-vec not loaded

    _run_migrations(conn, vec_dimensions=vec_dimensions)

    conn.commit()

    # Also restrict WAL/SHM sidecar files
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.parent / (db_path.name + suffix)
        if sidecar.exists():
            _restrict_file_permissions(sidecar)

    return ThreadSafeConnection(conn)


def _run_migrations(conn: sqlite3.Connection, vec_dimensions: int = 384) -> None:
    """Apply schema migrations for existing databases."""
    cursor = conn.execute("PRAGMA table_info(conversations)")
    cols = {row[1] for row in cursor.fetchall()}

    if "model" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN model TEXT DEFAULT NULL")

    if "project_id" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT DEFAULT NULL")

    if "folder_id" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN folder_id TEXT DEFAULT NULL")

    if "type" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN type TEXT NOT NULL DEFAULT 'chat'")

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

    # Ensure users table exists
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            public_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )

    # Add user_id / user_display_name columns to all entity tables
    for table in ("conversations", "messages", "projects", "folders", "tags"):
        table_cursor = conn.execute(f"PRAGMA table_info({table})")
        table_cols = {row[1] for row in table_cursor.fetchall()}
        if "user_id" not in table_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT NULL")
        if "user_display_name" not in table_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_display_name TEXT DEFAULT NULL")

    # Ensure change_log table exists for cross-process event polling
    conn.execute(
        """CREATE TABLE IF NOT EXISTS change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            process_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_change_log_id ON change_log(id)")

    # Ensure canvases table exists for existing databases
    conn.execute(
        """CREATE TABLE IF NOT EXISTS canvases (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'Untitled',
            content TEXT NOT NULL DEFAULT '',
            language TEXT DEFAULT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            user_id TEXT DEFAULT NULL,
            user_display_name TEXT DEFAULT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )"""
    )

    # Add approval_decision column to tool_calls (table may not exist in very old schemas)
    tc_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "tool_calls" in tc_tables:
        tc_cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
        if "approval_decision" not in tc_cols:
            conn.execute("ALTER TABLE tool_calls ADD COLUMN approval_decision TEXT DEFAULT NULL")

    # Ensure message_embeddings metadata table exists
    try:
        conn.executescript(_VEC_METADATA_SCHEMA)
    except sqlite3.OperationalError:
        pass

    # Ensure vec_messages virtual table exists (requires sqlite-vec)
    try:
        conn.executescript(_make_vec_schema(vec_dimensions))
    except sqlite3.OperationalError:
        pass

    # Ensure source tables exist for existing databases
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK(type IN ('file', 'text', 'url')),
            title TEXT NOT NULL,
            content TEXT,
            mime_type TEXT,
            filename TEXT,
            url TEXT,
            storage_path TEXT,
            size_bytes INTEGER,
            content_hash TEXT,
            user_id TEXT DEFAULT NULL,
            user_display_name TEXT DEFAULT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS source_chunks (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_chunks_source ON source_chunks(source_id, chunk_index)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS source_tags (
            source_id TEXT NOT NULL,
            tag_id TEXT NOT NULL,
            PRIMARY KEY (source_id, tag_id),
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS source_groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            user_id TEXT DEFAULT NULL,
            user_display_name TEXT DEFAULT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS source_group_members (
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            PRIMARY KEY (group_id, source_id),
            FOREIGN KEY (group_id) REFERENCES source_groups(id) ON DELETE CASCADE,
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS project_sources (
            project_id TEXT NOT NULL,
            source_id TEXT,
            group_id TEXT,
            tag_filter TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES source_groups(id) ON DELETE CASCADE,
            CHECK (
                (source_id IS NOT NULL AND group_id IS NULL AND tag_filter IS NULL) OR
                (source_id IS NULL AND group_id IS NOT NULL AND tag_filter IS NULL) OR
                (source_id IS NULL AND group_id IS NULL AND tag_filter IS NOT NULL)
            )
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS source_attachments (
            source_id TEXT NOT NULL,
            attachment_id TEXT NOT NULL,
            PRIMARY KEY (source_id, attachment_id),
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
            FOREIGN KEY (attachment_id) REFERENCES attachments(id) ON DELETE CASCADE
        )"""
    )

    # Ensure source chunk embeddings metadata + vec table exist
    conn.execute(
        """CREATE TABLE IF NOT EXISTS source_chunk_embeddings (
            chunk_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (chunk_id) REFERENCES source_chunks(id) ON DELETE CASCADE
        )"""
    )
    _ensure_vec_table_dimensions(conn, "vec_source_chunks", "source_chunk_embeddings", vec_dimensions)
    try:
        conn.executescript(_make_source_vec_schema(vec_dimensions))
    except sqlite3.OperationalError:
        pass

    # Add status column to embedding metadata tables for skip/fail tracking
    embedding_tables = {"message_embeddings", "source_chunk_embeddings"}
    for emb_table in embedding_tables:
        assert emb_table in embedding_tables  # guard: DDL cannot be parameterized in SQLite
        emb_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if emb_table in emb_tables:
            emb_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({emb_table})").fetchall()}
            if "status" not in emb_cols:
                conn.execute(f"ALTER TABLE {emb_table} ADD COLUMN status TEXT NOT NULL DEFAULT 'embedded'")


def has_vec_support(conn: sqlite3.Connection) -> bool:
    """Check if sqlite-vec extension is loaded and available."""
    try:
        conn.execute("SELECT vec_version()")
        return True
    except sqlite3.OperationalError:
        return False


def get_db(db_path: Path) -> ThreadSafeConnection:
    return init_db(db_path)


class DatabaseManager:
    """Manages personal + shared database connections."""

    def __init__(self) -> None:
        self._databases: dict[str, ThreadSafeConnection] = {}
        self._paths: dict[str, Path] = {}
        self._passphrase_hashes: dict[str, str] = {}
        self._personal_name = "personal"

    def add(self, name: str, db_path: Path, passphrase_hash: str = "") -> None:
        self._paths[name] = db_path
        self._databases[name] = init_db(db_path)
        if passphrase_hash:
            self._passphrase_hashes[name] = passphrase_hash

    def get(self, name: str | None = None) -> ThreadSafeConnection:
        key = name or self._personal_name
        if key not in self._databases:
            raise KeyError(f"Database '{key}' not found")
        return self._databases[key]

    @property
    def personal(self) -> ThreadSafeConnection:
        return self._databases[self._personal_name]

    def get_passphrase_hash(self, name: str) -> str:
        return self._passphrase_hashes.get(name, "")

    def requires_auth(self, name: str) -> bool:
        return bool(self._passphrase_hashes.get(name))

    def list_databases(self) -> list[dict[str, str]]:
        return [
            {
                "name": name,
                "path": str(self._paths[name]),
                "requires_auth": str(self.requires_auth(name)).lower(),
            }
            for name in self._databases
        ]

    def remove(self, name: str) -> None:
        if name in self._databases:
            self._databases[name].close()
            del self._databases[name]
            del self._paths[name]
            self._passphrase_hashes.pop(name, None)

    def close_all(self) -> None:
        for db in self._databases.values():
            db.close()
        self._databases.clear()
        self._passphrase_hashes.clear()
