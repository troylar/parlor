"""Universal artifact model and registry.

Artifacts are first-class, namespaced, versioned entities that unify all
extensibility pieces: skills, rules, instructions, context, memories,
MCP server configs, and config overlays.

FQN format: @namespace/type/name
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

_FQN_RE = re.compile(r"^@[a-z0-9][a-z0-9._-]{0,62}/[a-z0-9_]+/[a-z0-9_][a-z0-9_.-]{0,62}$")


class ArtifactType(str, enum.Enum):
    SKILL = "skill"
    RULE = "rule"
    INSTRUCTION = "instruction"
    CONTEXT = "context"
    MEMORY = "memory"
    MCP_SERVER = "mcp_server"
    CONFIG_OVERLAY = "config_overlay"


class ArtifactSource(str, enum.Enum):
    BUILT_IN = "built_in"
    GLOBAL = "global"
    TEAM = "team"
    PROJECT = "project"
    LOCAL = "local"
    INLINE = "inline"


def validate_fqn(fqn: str) -> bool:
    """Check if an FQN matches the required format: @namespace/type/name."""
    return bool(_FQN_RE.match(fqn))


def parse_fqn(fqn: str) -> tuple[str, str, str]:
    """Extract (namespace, type, name) from an FQN.

    Raises ValueError if the FQN is malformed.
    """
    if not validate_fqn(fqn):
        raise ValueError(f"Invalid FQN: {fqn!r} — expected @namespace/type/name")
    # Strip leading @, split on /
    parts = fqn[1:].split("/", 2)
    return parts[0], parts[1], parts[2]


def build_fqn(namespace: str, artifact_type: str, name: str) -> str:
    """Construct an FQN from parts. Validates the result."""
    fqn = f"@{namespace}/{artifact_type}/{name}"
    if not validate_fqn(fqn):
        raise ValueError(f"Invalid FQN components: namespace={namespace!r}, type={artifact_type!r}, name={name!r}")
    return fqn


def content_hash(content: str) -> str:
    """SHA-256 hash of content for deduplication."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Artifact:
    """A single versioned extensibility artifact."""

    fqn: str
    type: ArtifactType
    namespace: str
    name: str
    content: str
    version: int = 1
    source: ArtifactSource = ArtifactSource.LOCAL
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not validate_fqn(self.fqn):
            raise ValueError(f"Invalid FQN: {self.fqn!r}")
        if not self.content_hash:
            object.__setattr__(self, "content_hash", content_hash(self.content))
