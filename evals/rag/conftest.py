"""Fixtures for RAG retrieval eval harness.

Seeds a real SQLite database with known corpus data and real local embeddings
(fastembed BAAI/bge-small-en-v1.5) so retrieval quality can be measured.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from anteroom.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA, _VEC_METADATA_SCHEMA
from anteroom.services.storage import (
    store_embedding,
    store_source_chunk_embedding,
)
from anteroom.services.vector_index import VectorIndexManager, has_vector_support

DATASET_PATH = Path(__file__).parent / "dataset.yaml"
DIMS = 384


class _FakeThreadSafe:
    """Minimal ThreadSafeConnection wrapper for testing."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, parameters)

    def execute_fetchone(self, sql: str, parameters: Any = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, parameters).fetchone()

    def execute_fetchall(self, sql: str, parameters: Any = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, parameters).fetchall()

    def commit(self) -> None:
        self._conn.commit()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    class _TxContext:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def __enter__(self) -> sqlite3.Connection:
            return self._conn

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
            return False

    def transaction(self) -> _TxContext:
        return self._TxContext(self._conn)


def _init_db() -> _FakeThreadSafe:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_VEC_METADATA_SCHEMA)
    except sqlite3.OperationalError:
        pass
    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return _FakeThreadSafe(conn)


def _load_dataset() -> dict[str, Any]:
    with open(DATASET_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def embedding_service():
    """Session-scoped local embedding service (loads model once).

    Eagerly probes the model to skip cleanly when fastembed is absent
    or the model cannot be downloaded.
    """
    try:
        from anteroom.services.embeddings import LocalEmbeddingService
    except ImportError:
        pytest.skip("fastembed not available")

    import asyncio

    svc = LocalEmbeddingService("BAAI/bge-small-en-v1.5")

    # Probe: force the lazy model load and first embedding so failures
    # surface here as a skip rather than as a hard error in seeded_env.
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(svc.embed("probe"))
        if not result:
            pytest.skip("embedding service returned empty result")
    except Exception as exc:
        pytest.skip(f"embedding service unavailable: {exc}")
    finally:
        loop.close()

    return svc


@pytest.fixture(scope="session")
def dataset() -> dict[str, Any]:
    return _load_dataset()


@pytest.fixture(scope="session")
def seeded_env(embedding_service: Any, dataset: dict[str, Any]) -> dict[str, Any]:
    """Session-scoped seeded database + vector indexes with real embeddings.

    Returns dict with keys: db, vec_manager, dataset, id_to_content.
    """
    if not has_vector_support():
        pytest.skip("usearch not available")

    import asyncio

    db = _init_db()
    tmp = tempfile.mkdtemp()
    vec_manager = VectorIndexManager(
        Path(tmp),
        dimensions=DIMS,
    )

    corpus = dataset["corpus"]
    now = "2025-01-01T00:00:00Z"
    id_to_content: dict[str, str] = {}

    loop = asyncio.new_event_loop()

    try:
        # Seed sources and chunks
        for source in corpus.get("sources", []):
            src_id = source["id"]
            db.execute(
                "INSERT INTO sources (id, type, title, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (src_id, source["type"], source["title"], "", now, now),
            )
            db.commit()

            for chunk in source.get("chunks", []):
                chunk_id = chunk["id"]
                content = chunk["content"]
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                id_to_content[chunk_id] = content

                db.execute(
                    "INSERT INTO source_chunks (id, source_id, chunk_index, content, content_hash, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (chunk_id, src_id, 0, content, content_hash, now),
                )
                db.commit()

                embedding = loop.run_until_complete(embedding_service.embed(content))
                if embedding:
                    store_source_chunk_embedding(
                        db,
                        chunk_id,
                        src_id,
                        embedding,
                        content_hash,
                        vec_index=vec_manager.source_chunks,
                    )

        # Seed conversations and messages
        for conv in corpus.get("conversations", []):
            conv_id = conv["id"]
            conv_type = conv.get("type", "chat")
            db.execute(
                "INSERT INTO conversations (id, title, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conv_id, conv["title"], conv_type, now, now),
            )
            db.commit()

            for msg_idx, msg in enumerate(conv.get("messages", [])):
                msg_id = msg["id"]
                content = msg["content"]
                id_to_content[msg_id] = content

                db.execute(
                    "INSERT INTO messages (id, conversation_id, role, content, created_at, position)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (msg_id, conv_id, msg["role"], content, now, msg_idx),
                )
                db.commit()

                embedding = loop.run_until_complete(embedding_service.embed(content))
                if embedding:
                    store_embedding(
                        db,
                        msg_id,
                        conv_id,
                        embedding,
                        hashlib.sha256(content.encode()).hexdigest(),
                        vec_index=vec_manager.messages,
                    )
    finally:
        loop.close()

        # Seed spaces and space_sources for scoping tests
        for space in corpus.get("spaces", []):
            space_id = space["id"]
            space_name = space["name"]
            db.execute(
                "INSERT INTO spaces (id, name, source_file, source_hash, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (space_id, space_name, f"/tmp/{space_name}", "fakehash", now, now),
            )
            db.commit()

            # Link sources to space
            for src_id in space.get("sources", []):
                db.execute(
                    "INSERT INTO space_sources (space_id, source_id, created_at) VALUES (?, ?, ?)",
                    (space_id, src_id, now),
                )
            db.commit()

            # Set space_id on conversations
            for conv_id in space.get("conversations", []):
                db.execute(
                    "UPDATE conversations SET space_id = ? WHERE id = ?",
                    (space_id, conv_id),
                )
            db.commit()

    return {
        "db": db,
        "vec_manager": vec_manager,
        "dataset": dataset,
        "id_to_content": id_to_content,
    }
