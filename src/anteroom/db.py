"""SQLite database initialization and connection management."""

from __future__ import annotations

import logging
import re
import sqlite3
import stat
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    public_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    model TEXT DEFAULT NULL,
    source_file TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL DEFAULT '',
    last_loaded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS space_paths (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    repo_url TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS folders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT DEFAULT NULL,
    space_id TEXT DEFAULT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    collapsed INTEGER NOT NULL DEFAULT 0,
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
    FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE SET NULL
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
    slug TEXT UNIQUE DEFAULT NULL,
    type TEXT NOT NULL DEFAULT 'chat' CHECK(type IN ('chat', 'note', 'document')),
    model TEXT DEFAULT NULL,
    space_id TEXT DEFAULT NULL,
    folder_id TEXT DEFAULT NULL,
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
    working_dir TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL,
    FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE SET NULL
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
    prompt_tokens INTEGER DEFAULT NULL,
    completion_tokens INTEGER DEFAULT NULL,
    total_tokens INTEGER DEFAULT NULL,
    model TEXT DEFAULT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

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

CREATE TABLE IF NOT EXISTS source_attachments (
    source_id TEXT NOT NULL,
    attachment_id TEXT NOT NULL,
    PRIMARY KEY (source_id, attachment_id),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY (attachment_id) REFERENCES attachments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    fqn TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL CHECK(type IN (
        'skill','rule','instruction','context','memory','mcp_server','config_overlay')),
    namespace TEXT NOT NULL,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('built_in', 'global', 'team', 'project', 'local', 'inline')),
    metadata TEXT NOT NULL DEFAULT '{}',
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_versions (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(artifact_id, version),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS packs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    namespace TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '0.0.0',
    description TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    installed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pack_artifacts (
    pack_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    PRIMARY KEY(pack_id, artifact_id),
    FOREIGN KEY(pack_id) REFERENCES packs(id) ON DELETE CASCADE,
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS pack_attachments (
    id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL,
    project_path TEXT,
    space_id TEXT DEFAULT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('global', 'project', 'space')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(pack_id) REFERENCES packs(id) ON DELETE CASCADE,
    FOREIGN KEY(space_id) REFERENCES spaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS space_sources (
    space_id TEXT NOT NULL,
    source_id TEXT,
    group_id TEXT,
    tag_filter TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES source_groups(id) ON DELETE CASCADE,
    CHECK (
        (source_id IS NOT NULL AND group_id IS NULL AND tag_filter IS NULL) OR
        (source_id IS NULL AND group_id IS NOT NULL AND tag_filter IS NULL) OR
        (source_id IS NULL AND group_id IS NULL AND tag_filter IS NOT NULL)
    )
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


_db_lock = threading.RLock()


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
            return cast(sqlite3.Row | None, self._conn.execute(sql, parameters).fetchone())

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
    def transaction(self) -> Generator[ThreadSafeConnection, None, None]:
        """Hold the lock for the entire transaction, auto-commit or rollback."""
        with self._lock:
            try:
                yield self
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @property
    def row_factory(self) -> Any:
        with self._lock:
            return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
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


def _create_indexes(conn: sqlite3.Connection) -> None:
    """Create all indexes.

    Called after _run_migrations() so that all columns and tables are
    guaranteed to exist — even on databases created before those columns
    were introduced.  Every statement uses IF NOT EXISTS, making this
    safe to run unconditionally on both fresh and migrated databases.
    """
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spaces_name ON spaces(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_space_paths_space ON space_paths(space_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, position)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_change_log_id ON change_log(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_chunks_source ON source_chunks(source_id, chunk_index)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_fqn ON artifacts(fqn)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_namespace ON artifacts(namespace)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_versions_artifact_id ON artifact_versions(artifact_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_packs_namespace ON packs(namespace)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pack_artifacts_artifact_id ON pack_artifacts(artifact_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pack_attachments_pack ON pack_attachments(pack_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pack_attachments_project ON pack_attachments(project_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pack_attachments_space ON pack_attachments(space_id)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pack_attachments_unique "
        "ON pack_attachments(pack_id, COALESCE(project_path, ''), COALESCE(space_id, ''))"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_space_sources_unique "
        "ON space_sources(space_id, COALESCE(source_id, ''), COALESCE(group_id, ''), COALESCE(tag_filter, ''))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_space ON conversations(space_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_folders_space ON folders(space_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_slug ON conversations(slug)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_type ON conversations(type)")


def init_db(
    db_path: Path,
    vec_dimensions: int = 384,
    encryption_key: bytes | None = None,
) -> ThreadSafeConnection:
    is_new = not db_path.exists()

    if encryption_key is not None:
        from .services.encryption import open_encrypted_db

        conn = open_encrypted_db(db_path, encryption_key)
    else:
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
    _create_indexes(conn)

    conn.commit()

    # Eradicate projects tables/columns — must run OUTSIDE a transaction
    # because PRAGMA foreign_keys=OFF is silently ignored inside transactions.
    # Table rebuilds drop indexes/triggers, so recreate them afterwards.
    _eradicate_projects(conn)
    _create_indexes(conn)
    try:
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass
    conn.commit()

    # Also restrict WAL/SHM sidecar files
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.parent / (db_path.name + suffix)
        if sidecar.exists():
            _restrict_file_permissions(sidecar)

    return ThreadSafeConnection(conn)


_CONVERSATIONS_CREATE = """CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    slug TEXT UNIQUE DEFAULT NULL,
    type TEXT NOT NULL DEFAULT 'chat' CHECK(type IN ('chat', 'note', 'document')),
    model TEXT DEFAULT NULL,
    space_id TEXT DEFAULT NULL,
    folder_id TEXT DEFAULT NULL,
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
    working_dir TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL,
    FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE SET NULL
)"""

_FOLDERS_CREATE = """CREATE TABLE folders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT DEFAULT NULL,
    space_id TEXT DEFAULT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    collapsed INTEGER NOT NULL DEFAULT 0,
    user_id TEXT DEFAULT NULL,
    user_display_name TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
    FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE SET NULL
)"""


def _eradicate_projects(conn: sqlite3.Connection) -> None:
    """Remove all projects tables and FK columns (v1.95.0 — #716).

    This MUST run outside any transaction because PRAGMA foreign_keys=OFF
    is silently ignored inside transactions.  Uses the SQLite 12-step table
    rebuild to drop ``project_id`` columns that have broken FK references.

    Order matters: conversations references folders, so we must rebuild
    conversations first (dropping it), then folders, then recreate
    conversations with clean FKs.  Also recovers from partial previous
    runs that left ``_xxx_old`` tables.
    """
    all_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    # Detect actual table names (recover from partial previous runs)
    conv_table = "conversations" if "conversations" in all_tables else None
    folder_table = "folders" if "folders" in all_tables else None

    # Recovery: if a previous run left _xxx_old tables, use those as source
    if not conv_table and "_conversations_old" in all_tables:
        conv_table = "_conversations_old"
    if not folder_table and "_folders_old" in all_tables:
        folder_table = "_folders_old"

    # Use lists to preserve column order from PRAGMA table_info (important
    # for matching SELECT column order with INSERT column order).
    conv_cols_ordered: list[str] = []
    folder_cols_ordered: list[str] = []
    if conv_table:
        conv_cols_ordered = [row[1] for row in conn.execute(f"PRAGMA table_info({conv_table})").fetchall()]  # noqa: S608
    if folder_table:
        folder_cols_ordered = [row[1] for row in conn.execute(f"PRAGMA table_info({folder_table})").fetchall()]  # noqa: S608
    conv_cols = set(conv_cols_ordered)
    folder_cols = set(folder_cols_ordered)

    # Check for corrupted FK references (previous partial migration renamed
    # folders to _folders_old but conversations FK captured the temp name)
    conv_sql = ""
    if conv_table:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (conv_table,)).fetchone()
        conv_sql = row[0] if row else ""

    has_corrupted_fk = "_folders_old" in conv_sql or "_conversations_old" in conv_sql

    need_work = (
        "project_id" in conv_cols
        or "project_id" in folder_cols
        or "_conversations_old" in all_tables
        or "_folders_old" in all_tables
        or "project_sources" in all_tables
        or "projects" in all_tables
        or has_corrupted_fk
    )
    if not need_work:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        # Step 1: Drop conversations (or its leftover) so folders can be
        #         rebuilt without anything referencing it.
        #         Only rebuild if conversations actually has project_id,
        #         has corrupted FKs, or is a recovery leftover.
        conv_needs_rebuild = (
            "project_id" in conv_cols or has_corrupted_fk or (conv_table and conv_table == "_conversations_old")
        )
        conv_data: list[tuple[str, ...]] = []
        conv_keep: list[str] = []
        if conv_table and conv_needs_rebuild:
            conv_keep = [c for c in conv_cols_ordered if c != "project_id"]
            keep = conv_keep
            cols_csv = ", ".join(keep)
            conv_data = conn.execute(f"SELECT {cols_csv} FROM {conv_table}").fetchall()  # noqa: S608
            conn.execute(f"DROP TABLE {conv_table}")  # noqa: S608
        # Clean up any leftover from a prior partial run
        if "_conversations_old" in all_tables and conv_table != "_conversations_old":
            conn.execute("DROP TABLE IF EXISTS _conversations_old")

        # Step 2: Rebuild folders without project_id
        if folder_table and "project_id" in folder_cols:
            keep = [c for c in folder_cols_ordered if c != "project_id"]
            cols_csv = ", ".join(keep)
            folder_data = conn.execute(f"SELECT {cols_csv} FROM {folder_table}").fetchall()  # noqa: S608
            conn.execute(f"DROP TABLE {folder_table}")  # noqa: S608
            conn.execute(_FOLDERS_CREATE)
            if folder_data:
                placeholders = ", ".join("?" * len(keep))
                conn.executemany(f"INSERT INTO folders ({cols_csv}) VALUES ({placeholders})", folder_data)  # noqa: S608
        elif "_folders_old" in all_tables and folder_table == "_folders_old":
            # Recovery: _folders_old exists but folders doesn't
            keep = [c for c in folder_cols_ordered if c != "project_id"]
            cols_csv = ", ".join(keep)
            folder_data = conn.execute(f"SELECT {cols_csv} FROM _folders_old").fetchall()
            conn.execute("DROP TABLE _folders_old")
            conn.execute(_FOLDERS_CREATE)
            if folder_data:
                placeholders = ", ".join("?" * len(keep))
                conn.executemany(f"INSERT INTO folders ({cols_csv}) VALUES ({placeholders})", folder_data)  # noqa: S608

        # Step 3: Recreate conversations with clean FKs (only if we dropped it)
        if conv_needs_rebuild:
            conn.execute(_CONVERSATIONS_CREATE)
            if conv_data:
                placeholders = ", ".join("?" * len(conv_keep))
                cols_csv = ", ".join(conv_keep)
                conn.executemany(f"INSERT INTO conversations ({cols_csv}) VALUES ({placeholders})", conv_data)  # noqa: S608

        # Step 4: Drop project tables
        if "project_sources" in all_tables:
            conn.execute("DROP TABLE IF EXISTS project_sources")
        if "projects" in all_tables:
            conn.execute("DROP TABLE IF EXISTS projects")

        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _run_migrations(conn: sqlite3.Connection, vec_dimensions: int = 384) -> None:
    """Apply schema migrations for existing databases."""
    cursor = conn.execute("PRAGMA table_info(conversations)")
    cols = {row[1] for row in cursor.fetchall()}

    if "model" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN model TEXT DEFAULT NULL")

    if "folder_id" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN folder_id TEXT DEFAULT NULL")

    if "type" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN type TEXT NOT NULL DEFAULT 'chat'")

    if "slug" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN slug TEXT DEFAULT NULL")

    if "working_dir" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN working_dir TEXT DEFAULT NULL")

    # Ensure folders table exists for existing databases
    conn.execute(
        """CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id TEXT DEFAULT NULL,
            space_id TEXT DEFAULT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            collapsed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE SET NULL
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
    # DDL cannot use parameterized placeholders for identifiers in SQLite;
    # table names are validated against this hardcoded set before interpolation.
    allowed_entity_tables = {"conversations", "messages", "folders", "tags"}
    for table in ("conversations", "messages", "folders", "tags"):
        assert table in allowed_entity_tables, f"Unexpected table in migration: {table}"
        table_cursor = conn.execute(f"PRAGMA table_info({table})")
        table_cols = {row[1] for row in table_cursor.fetchall()}
        if "user_id" not in table_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT NULL")
        if "user_display_name" not in table_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_display_name TEXT DEFAULT NULL")

    # Add token usage columns to messages table
    msg_cursor = conn.execute("PRAGMA table_info(messages)")
    msg_cols = {row[1] for row in msg_cursor.fetchall()}
    for col in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if col not in msg_cols:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {col} INTEGER DEFAULT NULL")
    if "model" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN model TEXT DEFAULT NULL")

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
    # DDL cannot use parameterized placeholders for identifiers in SQLite;
    # table names are validated against this hardcoded constant before interpolation.
    allowed_embedding_tables = {"message_embeddings", "source_chunk_embeddings"}
    embedding_tables = allowed_embedding_tables
    for emb_table in embedding_tables:
        assert emb_table in allowed_embedding_tables, f"Unexpected table in migration: {emb_table}"
        emb_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if emb_table in emb_tables:
            emb_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({emb_table})").fetchall()}
            if "status" not in emb_cols:
                conn.execute(f"ALTER TABLE {emb_table} ADD COLUMN status TEXT NOT NULL DEFAULT 'embedded'")

    # Artifacts tables (v1.67.0)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            fqn TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL CHECK(type IN (
        'skill','rule','instruction','context','memory','mcp_server','config_overlay')),
            namespace TEXT NOT NULL,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('built_in', 'global', 'team', 'project', 'local', 'inline')),
            metadata TEXT NOT NULL DEFAULT '{}',
            user_id TEXT DEFAULT NULL,
            user_display_name TEXT DEFAULT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS artifact_versions (
            id TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(artifact_id, version),
            FOREIGN KEY (artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE
        )"""
    )
    # Packs tables (v1.69.0)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS packs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            namespace TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT '0.0.0',
            description TEXT NOT NULL DEFAULT '',
            source_path TEXT NOT NULL DEFAULT '',
            installed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pack_artifacts (
            pack_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            PRIMARY KEY(pack_id, artifact_id),
            FOREIGN KEY(pack_id) REFERENCES packs(id) ON DELETE CASCADE,
            FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
        )"""
    )
    # Pack attachments table (v1.70.0)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pack_attachments (
            id TEXT PRIMARY KEY,
            pack_id TEXT NOT NULL,
            project_path TEXT,
            space_id TEXT DEFAULT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('global', 'project', 'space')),
            created_at TEXT NOT NULL,
            FOREIGN KEY(pack_id) REFERENCES packs(id) ON DELETE CASCADE
        )"""
    )
    # Add space_id column to pack_attachments if missing (v1.74.0)
    # Note: SQLite cannot add FK via ALTER TABLE. Fresh installs get the FK in the
    # CREATE TABLE statement. Migrated DBs rely on application-level validation in
    # attach_pack_to_space() to enforce referential integrity for space_id.
    pa_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "pack_attachments" in pa_tables:
        pa_cols = {row[1] for row in conn.execute("PRAGMA table_info(pack_attachments)").fetchall()}
        if "space_id" not in pa_cols:
            conn.execute("ALTER TABLE pack_attachments ADD COLUMN space_id TEXT DEFAULT NULL")

    # Spaces tables (v1.74.0)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS spaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_file TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL DEFAULT '',
            instructions TEXT NOT NULL DEFAULT '',
            model TEXT DEFAULT NULL,
            last_loaded_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    # Migrate spaces column renames: source_file→file_path, source_hash→file_hash (v1.94.5)
    space_cols = {row[1] for row in conn.execute("PRAGMA table_info(spaces)").fetchall()}
    if "source_file" in space_cols and "file_path" not in space_cols:
        conn.execute("ALTER TABLE spaces RENAME COLUMN source_file TO file_path")
        conn.execute("ALTER TABLE spaces RENAME COLUMN source_hash TO file_hash")
    # Repair broken FK references from v1.94.4 table-rebuild migration (v1.94.5)
    # The table-rebuild approach with foreign_keys=ON caused SQLite to rewrite FK targets in
    # messages, conversation_tags, and canvases to point to "_conversations_old" instead of
    # "conversations". Detect and repair by rebuilding affected tables with correct FKs.
    _repair_broken_fk_refs = []
    for _tbl in ("messages", "conversation_tags", "canvases"):
        _ddl_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (_tbl,)).fetchone()
        if _ddl_row and "_conversations_old" in (_ddl_row[0] if isinstance(_ddl_row, tuple) else _ddl_row["sql"]):
            _repair_broken_fk_refs.append(_tbl)
    if _repair_broken_fk_refs:
        logger.warning("Repairing broken FK references in: %s", _repair_broken_fk_refs)
        conn.execute("PRAGMA foreign_keys=OFF")
        if "messages" in _repair_broken_fk_refs:
            conn.execute(
                """CREATE TABLE messages_repaired (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL DEFAULT '', user_id TEXT DEFAULT NULL,
                    user_display_name TEXT DEFAULT NULL, created_at TEXT NOT NULL,
                    position INTEGER NOT NULL, prompt_tokens INTEGER DEFAULT NULL,
                    completion_tokens INTEGER DEFAULT NULL, total_tokens INTEGER DEFAULT NULL,
                    model TEXT DEFAULT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )"""
            )
            conn.execute("INSERT INTO messages_repaired SELECT * FROM messages")
            conn.execute("DROP TABLE messages")
            conn.execute("ALTER TABLE messages_repaired RENAME TO messages")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, position)")
        if "conversation_tags" in _repair_broken_fk_refs:
            conn.execute(
                """CREATE TABLE conversation_tags_repaired (
                    conversation_id TEXT NOT NULL, tag_id TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, tag_id),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
                )"""
            )
            conn.execute("INSERT INTO conversation_tags_repaired SELECT * FROM conversation_tags")
            conn.execute("DROP TABLE conversation_tags")
            conn.execute("ALTER TABLE conversation_tags_repaired RENAME TO conversation_tags")
        if "canvases" in _repair_broken_fk_refs:
            conn.execute(
                """CREATE TABLE canvases_repaired (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'Untitled', content TEXT NOT NULL DEFAULT '',
                    language TEXT DEFAULT NULL, version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    user_id TEXT DEFAULT NULL, user_display_name TEXT DEFAULT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )"""
            )
            conn.execute("INSERT INTO canvases_repaired SELECT * FROM canvases")
            conn.execute("DROP TABLE canvases")
            conn.execute("ALTER TABLE canvases_repaired RENAME TO canvases")
        # Recreate FTS triggers dropped with the messages table
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS fts_messages_insert AFTER INSERT ON messages BEGIN
                UPDATE conversations_fts SET content = content || ' ' || NEW.content
                WHERE conversation_id = NEW.conversation_id; END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS fts_messages_delete AFTER DELETE ON messages BEGIN
                UPDATE conversations_fts SET content = (
                    SELECT COALESCE(GROUP_CONCAT(content, ' '), '')
                    FROM messages WHERE conversation_id = OLD.conversation_id
                ) WHERE conversation_id = OLD.conversation_id; END"""
        )
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        logger.info("FK repair complete for: %s", _repair_broken_fk_refs)
    # Migrate spaces columns from v1.74.0 schema to v1.95.0 (rename + add)
    sp_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "spaces" in sp_tables:
        sp_cols = {row[1] for row in conn.execute("PRAGMA table_info(spaces)").fetchall()}
        # Rename file_path → source_file, file_hash → source_hash (SQLite 3.25+)
        if "file_path" in sp_cols and "source_file" not in sp_cols:
            conn.execute("ALTER TABLE spaces RENAME COLUMN file_path TO source_file")
        if "file_hash" in sp_cols and "source_hash" not in sp_cols:
            conn.execute("ALTER TABLE spaces RENAME COLUMN file_hash TO source_hash")
        # Add new columns
        if "instructions" not in sp_cols:
            conn.execute("ALTER TABLE spaces ADD COLUMN instructions TEXT NOT NULL DEFAULT ''")
        if "model" not in sp_cols:
            conn.execute("ALTER TABLE spaces ADD COLUMN model TEXT DEFAULT NULL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS space_paths (
            id TEXT PRIMARY KEY,
            space_id TEXT NOT NULL,
            repo_url TEXT NOT NULL DEFAULT '',
            local_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE
        )"""
    )
    # Space sources junction table (v1.74.0)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS space_sources (
            space_id TEXT NOT NULL,
            source_id TEXT,
            group_id TEXT,
            tag_filter TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE,
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES source_groups(id) ON DELETE CASCADE,
            CHECK (
                (source_id IS NOT NULL AND group_id IS NULL AND tag_filter IS NULL) OR
                (source_id IS NULL AND group_id IS NOT NULL AND tag_filter IS NULL) OR
                (source_id IS NULL AND group_id IS NULL AND tag_filter IS NOT NULL)
            )
        )"""
    )
    # Add space_id to conversations and folders (v1.74.0)
    # Note: SQLite ALTER TABLE cannot add FK constraints. Fresh installs get the FK in
    # CREATE TABLE. Migrated DBs rely on application-level cascade in space_storage.py.
    if "space_id" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN space_id TEXT DEFAULT NULL")
    if "space_id" not in folder_cols:
        conn.execute("ALTER TABLE folders ADD COLUMN space_id TEXT DEFAULT NULL")

    # Drop UNIQUE constraint on space names (v1.79.0)
    try:
        conn.execute("DROP INDEX IF EXISTS idx_spaces_name")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spaces_name ON spaces(name)")
    except sqlite3.OperationalError:
        logger.warning("Failed to migrate spaces index to non-unique", exc_info=True)

    # Drop UNIQUE(namespace, name) on packs (v1.79.0)
    try:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='packs'").fetchone()
        if row:
            ddl = row[0] if isinstance(row, (tuple, list)) else row["sql"]
            if ddl and "UNIQUE" in ddl:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS packs_new (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        namespace TEXT NOT NULL,
                        version TEXT NOT NULL DEFAULT '0.0.0',
                        description TEXT NOT NULL DEFAULT '',
                        source_path TEXT NOT NULL DEFAULT '',
                        installed_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                conn.execute(
                    "INSERT OR IGNORE INTO packs_new SELECT id, name, namespace, version, "
                    "description, source_path, installed_at, updated_at FROM packs"
                )
                conn.execute("DROP TABLE packs")
                conn.execute("ALTER TABLE packs_new RENAME TO packs")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_packs_namespace ON packs(namespace)")
    except sqlite3.OperationalError:
        logger.warning("Failed to migrate packs table to drop UNIQUE constraint", exc_info=True)


def has_vec_support(conn: sqlite3.Connection | ThreadSafeConnection) -> bool:
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
