"""ArtifactRegistry: 6-layer namespaced artifact loader with precedence resolution.

Layers loaded in order (later wins on FQN conflict):
    built_in < global < team < project < local < inline

For the initial primitives, the registry loads from the DB and accepts
programmatic registration. Filesystem discovery hooks are added by later
issues in the artifact epic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..db import ThreadSafeConnection

from .artifact_storage import list_artifacts
from .artifacts import Artifact, ArtifactSource, ArtifactType
from .artifacts import content_hash as compute_hash

logger = logging.getLogger(__name__)

MAX_ARTIFACTS = 500

# Layer precedence order (index = priority, higher wins)
_LAYER_ORDER: list[ArtifactSource] = [
    ArtifactSource.BUILT_IN,
    ArtifactSource.GLOBAL,
    ArtifactSource.TEAM,
    ArtifactSource.PROJECT,
    ArtifactSource.LOCAL,
    ArtifactSource.INLINE,
]


class ArtifactRegistry:
    """In-memory artifact index with layered precedence."""

    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_db(self, db: ThreadSafeConnection) -> None:
        """Load all artifacts from the database, applying layer precedence.

        Artifacts are sorted by source layer so that higher-precedence
        sources overwrite lower ones when FQNs collide.
        """
        rows = list_artifacts(db)
        new_artifacts: dict[str, Artifact] = {}

        # Sort by layer precedence so later layers overwrite earlier
        layer_index = {s.value: i for i, s in enumerate(_LAYER_ORDER)}
        rows.sort(key=lambda r: layer_index.get(r["source"], 0))

        for row in rows:
            art = _artifact_from_row(row)
            if art.fqn in new_artifacts:
                prev = new_artifacts[art.fqn]
                if prev.source == ArtifactSource.BUILT_IN:
                    logger.warning("Artifact %s overrides built-in (source=%s)", art.fqn, art.source.value)
            new_artifacts[art.fqn] = art

        if len(new_artifacts) > MAX_ARTIFACTS:
            logger.warning("Artifact count (%d) exceeds recommended max (%d)", len(new_artifacts), MAX_ARTIFACTS)

        # Atomic swap
        self._artifacts = new_artifacts

    reload = load_from_db

    def register(self, artifact: Artifact) -> None:
        """Add or replace a single artifact in the registry."""
        if artifact.fqn in self._artifacts:
            prev = self._artifacts[artifact.fqn]
            if prev.source == ArtifactSource.BUILT_IN and artifact.source != ArtifactSource.BUILT_IN:
                logger.warning("Artifact %s overrides built-in (source=%s)", artifact.fqn, artifact.source.value)
        self._artifacts[artifact.fqn] = artifact

    def unregister(self, fqn: str) -> bool:
        """Remove an artifact by FQN. Returns True if it existed."""
        return self._artifacts.pop(fqn, None) is not None

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, fqn: str) -> Artifact | None:
        """Look up an artifact by its fully-qualified name."""
        return self._artifacts.get(fqn)

    def list_all(
        self,
        artifact_type: ArtifactType | None = None,
        namespace: str | None = None,
        source: ArtifactSource | None = None,
    ) -> list[Artifact]:
        """List artifacts with optional filtering."""
        filtered: list[Artifact] = [*self._artifacts.values()]
        if artifact_type is not None:
            filtered = [a for a in filtered if a.type == artifact_type]
        if namespace is not None:
            filtered = [a for a in filtered if a.namespace == namespace]
        if source is not None:
            filtered = [a for a in filtered if a.source == source]
        return filtered

    def search(self, name_pattern: str) -> list[Artifact]:
        """Find artifacts whose name contains the given substring (case-insensitive)."""
        pattern = name_pattern.lower()
        return [a for a in self._artifacts.values() if pattern in a.name.lower()]

    @property
    def count(self) -> int:
        return len(self._artifacts)

    def clear(self) -> None:
        self._artifacts = {}


def _artifact_from_row(row: dict[str, Any]) -> Artifact:
    """Convert a storage dict to an Artifact dataclass.

    Validates content hash if present — warns on mismatch (data corruption).
    """
    stored_hash = row.get("content_hash", "")
    content = row["content"]
    if stored_hash:
        actual = compute_hash(content)
        if actual != stored_hash:
            logger.warning(
                "Content hash mismatch for artifact %s: stored=%s, actual=%s (possible corruption)",
                row["fqn"],
                stored_hash[:12],
                actual[:12],
            )
    return Artifact(
        fqn=row["fqn"],
        type=ArtifactType(row["type"]),
        namespace=row["namespace"],
        name=row["name"],
        content=content,
        version=row.get("version", 1),
        source=ArtifactSource(row["source"]),
        metadata=row.get("metadata") or {},
        content_hash=row.get("content_hash", ""),
    )
