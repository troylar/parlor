"""Trust store for project-level ANTEROOM.md files.

Prevents prompt injection by gating untrusted project instruction files
behind user consent. Trust decisions are persisted to ~/.anteroom/trusted_folders.json
with content hash verification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TrustDecision:
    path: str
    content_hash: str
    trusted_at: str
    recursive: bool = False


def _trust_file_path(data_dir: Path | None = None) -> Path:
    """Return path to the trust store JSON file."""
    if data_dir is None:
        from ..config import _resolve_data_dir

        data_dir = _resolve_data_dir()
    return data_dir / "trusted_folders.json"


def compute_content_hash(content: str) -> str:
    """SHA-256 hex digest of file content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_trust_store(data_dir: Path | None = None) -> list[TrustDecision]:
    """Read trusted folders from the JSON store."""
    path = _trust_file_path(data_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        trusted = raw.get("trusted", [])
        return [
            TrustDecision(
                path=entry["path"],
                content_hash=entry.get("content_hash", ""),
                trusted_at=entry.get("trusted_at", ""),
                recursive=entry.get("recursive", False),
            )
            for entry in trusted
            if isinstance(entry, dict) and "path" in entry
        ]
    except (json.JSONDecodeError, OSError, KeyError):
        logger.warning("Could not read trust store at %s; treating as empty", path)
        return []


def _acquire_lock(data_dir: Path | None = None):
    """Acquire an exclusive file lock for the trust store. Returns (lock_file, fcntl_mod) or (None, None)."""
    try:
        import fcntl as _fcntl
    except ImportError:
        return None, None

    path = _trust_file_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    lock_f = open(lock_path, "w")  # noqa: SIM115
    _fcntl.flock(lock_f, _fcntl.LOCK_EX)
    return lock_f, _fcntl


def _release_lock(lock_f, fcntl_mod) -> None:
    """Release the file lock."""
    if lock_f is not None and fcntl_mod is not None:
        try:
            fcntl_mod.flock(lock_f, fcntl_mod.LOCK_UN)
        finally:
            lock_f.close()


def _save_trust_store(decisions: list[TrustDecision], data_dir: Path | None = None) -> None:
    """Write the full trust store to disk with 0600 perms. Caller must hold the lock."""
    path = _trust_file_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(
        {"trusted": [asdict(d) for d in decisions]},
        indent=2,
        ensure_ascii=False,
    )
    path.write_text(payload, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def save_trust_decision(
    folder_path: str,
    content_hash: str,
    recursive: bool = False,
    data_dir: Path | None = None,
) -> None:
    """Add or update a trust decision for a folder path.

    The full read-modify-write cycle is protected by an exclusive file lock
    to prevent concurrent processes from losing each other's trust entries.
    """
    lock_f, fcntl_mod = _acquire_lock(data_dir)
    try:
        resolved = os.path.realpath(folder_path)
        decisions = load_trust_store(data_dir)

        # Update existing entry or append new one
        for i, d in enumerate(decisions):
            if os.path.realpath(d.path) == resolved:
                decisions[i] = TrustDecision(
                    path=resolved,
                    content_hash=content_hash,
                    trusted_at=datetime.now(timezone.utc).isoformat(),
                    recursive=recursive,
                )
                _save_trust_store(decisions, data_dir)
                return

        decisions.append(
            TrustDecision(
                path=resolved,
                content_hash=content_hash,
                trusted_at=datetime.now(timezone.utc).isoformat(),
                recursive=recursive,
            )
        )
        _save_trust_store(decisions, data_dir)
    finally:
        _release_lock(lock_f, fcntl_mod)


def check_trust(
    folder_path: str,
    content_hash: str,
    data_dir: Path | None = None,
) -> str:
    """Check trust status for a folder path.

    Returns:
        "trusted" — folder is trusted and content hash matches
        "changed" — folder is trusted but content hash differs
        "untrusted" — folder has no trust record
    """
    resolved = os.path.realpath(folder_path)
    decisions = load_trust_store(data_dir)

    for d in decisions:
        d_resolved = os.path.realpath(d.path)

        # Direct match
        if d_resolved == resolved:
            if d.content_hash == content_hash:
                return "trusted"
            return "changed"

        # Recursive match: trusted parent covers subdirectories unconditionally
        # (the parent's hash is for the parent's ANTEROOM.md, not the child's)
        if d.recursive and _is_subpath(resolved, d_resolved):
            return "trusted"

    return "untrusted"


def _is_subpath(child: str, parent: str) -> bool:
    """Check if child is a subdirectory of parent."""
    try:
        Path(child).relative_to(parent)
        return True
    except ValueError:
        return False
