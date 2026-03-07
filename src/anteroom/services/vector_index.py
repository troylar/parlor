"""Vector index using usearch for fast approximate nearest neighbor search.

Replaces sqlite-vec with a file-based usearch index. SQLite metadata tables
(message_embeddings, source_chunk_embeddings) remain the source of truth;
the usearch index is a derived, rebuildable acceleration structure.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_EMBEDDING_DIMENSIONS = 4096
_MAX_SEARCH_LIMIT = 1000


def has_vector_support() -> bool:
    """Check if usearch is available."""
    try:
        from usearch.index import Index as _Index  # noqa: F401

        return True
    except ImportError:
        return False


def _validate_embedding(embedding: list[float], *, dimensions: int | None = None) -> None:
    """Validate embedding vector values.

    Raises ValueError if the embedding is empty, too large, or contains
    non-finite values.
    """
    if not embedding or len(embedding) > _MAX_EMBEDDING_DIMENSIONS:
        raise ValueError(f"Embedding must have 1-{_MAX_EMBEDDING_DIMENSIONS} dimensions, got {len(embedding)}")
    if dimensions is not None and len(embedding) != dimensions:
        raise ValueError(f"Embedding has {len(embedding)} dimensions, expected {dimensions}")
    for i, val in enumerate(embedding):
        if not isinstance(val, (int, float)) or (isinstance(val, float) and not math.isfinite(val)):
            raise ValueError(f"Embedding dimension {i} is not a finite number")


def _string_key_to_int(key: str) -> int:
    """Convert a string key (UUID) to a deterministic positive integer for usearch.

    Uses a truncated SHA-256 hash to produce a 63-bit positive integer,
    which avoids collisions for practical dataset sizes while staying
    within usearch's key range.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


class VectorIndex:
    """Manages a usearch index backed by a file on disk.

    Each index stores vectors keyed by string IDs (UUIDs). A reverse mapping
    from integer keys back to string IDs is maintained in memory and rebuilt
    from SQLite metadata on load.

    Thread-safe: all mutating operations are serialized via a lock.
    """

    def __init__(self, index_path: Path, dimensions: int = 384) -> None:
        if not isinstance(dimensions, int) or not (1 <= dimensions <= _MAX_EMBEDDING_DIMENSIONS):
            raise ValueError(f"Invalid embedding dimensions: {dimensions!r}")

        self._path = index_path
        self._dimensions = dimensions
        self._lock = threading.Lock()
        self._int_to_str: dict[int, str] = {}

        from usearch.index import Index

        self._index: Index
        if index_path.exists():
            restored = Index.restore(str(index_path), view=False)
            if restored is None:
                self._index = Index(ndim=dimensions, metric="cos", dtype="f32")
            elif restored.ndim != dimensions:
                logger.warning(
                    "Index dimensions changed (%d -> %d). Rebuilding index.",
                    restored.ndim,
                    dimensions,
                )
                self._index = Index(ndim=dimensions, metric="cos", dtype="f32")
            else:
                self._index = restored
        else:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            self._index = Index(ndim=dimensions, metric="cos", dtype="f32")

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def register_key(self, string_key: str) -> None:
        """Register a string key -> int key mapping for reverse lookups."""
        int_key = _string_key_to_int(string_key)
        self._int_to_str[int_key] = string_key

    def add(self, key: str, embedding: list[float]) -> None:
        """Add or replace a vector in the index."""
        import numpy as np

        _validate_embedding(embedding, dimensions=self._dimensions)
        int_key = _string_key_to_int(key)
        vector = np.array(embedding, dtype=np.float32)

        with self._lock:
            if int_key in self._index:
                self._index.remove(int_key)
            self._index.add(int_key, vector)
            self._int_to_str[int_key] = key

    def remove(self, key: str) -> None:
        """Remove a vector from the index. No-op if key not present."""
        int_key = _string_key_to_int(key)

        with self._lock:
            if int_key in self._index:
                self._index.remove(int_key)
            self._int_to_str.pop(int_key, None)

    def search(self, query: list[float], limit: int = 10) -> list[dict[str, Any]]:
        """Search for the most similar vectors. Returns list of {key, distance}.

        Over-fetches when stale (unmapped) vectors are encountered so that
        the caller always receives up to *limit* mapped results.
        """
        import numpy as np

        _validate_embedding(query, dimensions=self._dimensions)
        limit = max(1, min(limit, _MAX_SEARCH_LIMIT))

        query_vector = np.array(query, dtype=np.float32)

        with self._lock:
            index_size = len(self._index)
            if index_size == 0:
                return []

            fetch_k = min(limit, index_size)
            matches: list[dict[str, Any]] = []

            while len(matches) < limit:
                actual_k = min(fetch_k, index_size)
                results = self._index.search(query_vector, actual_k)

                matches.clear()
                for int_key, distance in zip(results.keys, results.distances):
                    int_key_val = int(int_key)
                    string_key = self._int_to_str.get(int_key_val)
                    if string_key is None:
                        continue
                    matches.append({"key": string_key, "distance": float(distance)})
                    if len(matches) >= limit:
                        break

                # If we got enough, or we've already fetched everything, stop.
                if len(matches) >= limit or fetch_k >= index_size:
                    break
                # Widen: double fetch_k to get past stale entries.
                fetch_k = min(fetch_k * 2, index_size)

        return matches

    def contains(self, key: str) -> bool:
        """Check if a key exists in the index."""
        int_key = _string_key_to_int(key)
        with self._lock:
            return int_key in self._index

    def count(self) -> int:
        """Return the number of vectors in the index."""
        return len(self._index)

    def save(self) -> None:
        """Persist the index to disk."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._index.save(str(self._path))

    def clear(self) -> None:
        """Remove all vectors and reset the index."""
        from usearch.index import Index

        with self._lock:
            self._index = Index(ndim=self._dimensions, metric="cos", dtype="f32")
            self._int_to_str.clear()

    def rebuild_key_map(self, key_pairs: list[tuple[str, str]]) -> None:
        """Rebuild the int->str key map from a list of (string_key, ...) pairs.

        Called during startup to populate the reverse mapping from SQLite
        metadata rows. The second element of each tuple is ignored (allows
        passing (key, conversation_id) or (key, source_id) directly).
        """
        with self._lock:
            self._int_to_str.clear()
            for string_key, _ in key_pairs:
                int_key = _string_key_to_int(string_key)
                self._int_to_str[int_key] = string_key


class VectorIndexManager:
    """Manages the two vector indexes (messages and source chunks).

    Provides a single point of initialization and access for both indexes,
    with methods to check availability and rebuild from SQLite metadata.
    """

    def __init__(self, data_dir: Path, dimensions: int = 384) -> None:
        self._data_dir = data_dir
        self._dimensions = dimensions
        self._messages: VectorIndex | None = None
        self._source_chunks: VectorIndex | None = None
        self._enabled = False

        if not has_vector_support():
            logger.info("usearch not installed; vector search disabled")
            return

        try:
            vec_dir = data_dir / "vectors"
            self._messages = VectorIndex(vec_dir / "messages.usearch", dimensions)
            self._source_chunks = VectorIndex(vec_dir / "source_chunks.usearch", dimensions)
            self._enabled = True
        except Exception:
            logger.warning("Failed to initialize vector indexes", exc_info=True)
            self._messages = None
            self._source_chunks = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def messages(self) -> VectorIndex | None:
        return self._messages

    @property
    def source_chunks(self) -> VectorIndex | None:
        return self._source_chunks

    def save_all(self) -> None:
        """Persist both indexes to disk."""
        if self._messages:
            self._messages.save()
        if self._source_chunks:
            self._source_chunks.save()

    def rebuild_from_db(self, db: Any) -> None:
        """Rebuild key maps and repair index/metadata divergence.

        This is the crash/startup recovery path. For each "embedded" metadata
        row, it verifies the corresponding key actually exists in the usearch
        index. Rows whose keys are missing from the index are reset to
        "pending" so the embedding worker re-embeds them. This handles full
        index loss, partial index loss, and key-set divergence (same count
        but different keys) after an unsaved mutation sequence.
        """
        if not self._enabled:
            return

        if self._messages:
            try:
                rows = db.execute_fetchall(
                    "SELECT message_id, conversation_id FROM message_embeddings WHERE status = ?",
                    ("embedded",),
                )
                if not rows:
                    logger.info("No embedded message rows; nothing to rebuild")
                else:
                    present = []
                    missing_ids = []
                    for r in rows:
                        if self._messages.contains(r["message_id"]):
                            present.append((r["message_id"], r["conversation_id"]))
                        else:
                            missing_ids.append(r["message_id"])

                    if missing_ids:
                        logger.warning(
                            "%d of %d message embeddings missing from usearch index; "
                            "resetting to 'pending' for re-embedding.",
                            len(missing_ids),
                            len(rows),
                        )
                        placeholders = ",".join("?" * len(missing_ids))
                        db.execute(
                            f"UPDATE message_embeddings SET status = 'pending'"
                            f" WHERE message_id IN ({placeholders})",
                            tuple(missing_ids),
                        )
                        db.commit()

                    self._messages.rebuild_key_map(present)
                    logger.info(
                        "Rebuilt message vector key map: %d present, %d reset",
                        len(present),
                        len(missing_ids),
                    )
            except Exception:
                logger.warning("Failed to rebuild message vector key map", exc_info=True)

        if self._source_chunks:
            try:
                rows = db.execute_fetchall(
                    "SELECT chunk_id, source_id FROM source_chunk_embeddings WHERE status = ?",
                    ("embedded",),
                )
                if not rows:
                    logger.info("No embedded source chunk rows; nothing to rebuild")
                else:
                    present = []
                    missing_ids = []
                    for r in rows:
                        if self._source_chunks.contains(r["chunk_id"]):
                            present.append((r["chunk_id"], r["source_id"]))
                        else:
                            missing_ids.append(r["chunk_id"])

                    if missing_ids:
                        logger.warning(
                            "%d of %d source chunk embeddings missing from usearch index; "
                            "resetting to 'pending' for re-embedding.",
                            len(missing_ids),
                            len(rows),
                        )
                        placeholders = ",".join("?" * len(missing_ids))
                        db.execute(
                            f"UPDATE source_chunk_embeddings SET status = 'pending'"
                            f" WHERE chunk_id IN ({placeholders})",
                            tuple(missing_ids),
                        )
                        db.commit()

                    self._source_chunks.rebuild_key_map(present)
                    logger.info(
                        "Rebuilt source chunk vector key map: %d present, %d reset",
                        len(present),
                        len(missing_ids),
                    )
            except Exception:
                logger.warning("Failed to rebuild source chunk vector key map", exc_info=True)
