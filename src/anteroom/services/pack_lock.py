"""Pack lock file generation and validation.

The lock file (``anteroom.lock.yaml``) records content hashes and source
paths for every artifact installed via packs, providing reproducibility
and tamper detection.  When packs are installed from git sources, the
lock records ``source_url`` and ``source_ref`` (commit SHA) so that new
team members can ``aroom pack restore`` to clone the exact revisions.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from .pack_sources import _SOURCE_URL_FILE, get_source_ref

logger = logging.getLogger(__name__)

_LOCK_FILE = "anteroom.lock.yaml"
_ANTEROOM_DIR = ".anteroom"


def generate_lock(db: sqlite3.Connection) -> dict[str, Any]:
    """Generate lock data from the current installed packs and artifacts.

    Returns a dict suitable for serializing to YAML.
    """
    packs_rows = db.execute(
        """SELECT p.id, p.name, p.namespace, p.version, p.source_path
           FROM packs p ORDER BY p.namespace, p.name"""
    ).fetchall()

    packs: list[dict[str, Any]] = []
    for p in packs_rows:
        p_dict = (
            dict(p)
            if hasattr(p, "keys")
            else {
                "id": p[0],
                "name": p[1],
                "namespace": p[2],
                "version": p[3],
                "source_path": p[4],
            }
        )
        pack_id = p_dict["id"]

        art_rows = db.execute(
            """SELECT a.fqn, a.content_hash, a.type, a.name
               FROM artifacts a
               JOIN pack_artifacts pa ON a.id = pa.artifact_id
               WHERE pa.pack_id = ?
               ORDER BY a.fqn""",
            (pack_id,),
        ).fetchall()

        artifacts = []
        for a in art_rows:
            a_dict = (
                dict(a)
                if hasattr(a, "keys")
                else {
                    "fqn": a[0],
                    "content_hash": a[1],
                    "type": a[2],
                    "name": a[3],
                }
            )
            artifacts.append(
                {
                    "fqn": a_dict["fqn"],
                    "content_hash": a_dict["content_hash"],
                    "type": a_dict["type"],
                    "name": a_dict["name"],
                }
            )

        entry: dict[str, Any] = {
            "name": p_dict["name"],
            "namespace": p_dict["namespace"],
            "version": p_dict["version"],
            "source_path": p_dict["source_path"],
            "artifacts": artifacts,
        }

        # Enrich with git source info when the pack came from a cached source
        source_path = p_dict.get("source_path", "")
        if source_path:
            source_dir = Path(source_path)
            url_file = source_dir / _SOURCE_URL_FILE
            if url_file.is_file():
                entry["source_url"] = url_file.read_text(encoding="utf-8").strip()
                ref = get_source_ref(source_dir)
                if ref:
                    entry["source_ref"] = ref

        packs.append(entry)

    return {
        "version": 1,
        "packs": packs,
    }


def write_lock(project_dir: Path, lock_data: dict[str, Any]) -> Path:
    """Write the lock file to ``.anteroom/anteroom.lock.yaml``.

    Returns the path to the written file.
    """
    lock_dir = project_dir / _ANTEROOM_DIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / _LOCK_FILE

    with open(lock_path, "w", encoding="utf-8") as f:
        yaml.dump(lock_data, f, default_flow_style=False, sort_keys=True)

    logger.info("Wrote lock file: %s", lock_path)
    return lock_path


def read_lock(project_dir: Path) -> dict[str, Any] | None:
    """Read and parse the lock file.

    Returns ``None`` if the file does not exist.
    """
    lock_path = project_dir / _ANTEROOM_DIR / _LOCK_FILE
    if not lock_path.is_file():
        return None

    with open(lock_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        logger.warning("Lock file is not a valid YAML mapping: %s", lock_path)
        return None

    return data


def validate_lock(db: sqlite3.Connection, project_dir: Path) -> list[str]:
    """Validate the lock file against current DB state.

    Returns a list of warning messages. Empty list means lock is valid.
    """
    lock_data = read_lock(project_dir)
    if lock_data is None:
        return ["Lock file not found"]

    warnings: list[str] = []
    lock_packs = lock_data.get("packs", [])
    if not isinstance(lock_packs, list):
        return ["Lock file has invalid format: 'packs' is not a list"]

    for pack_entry in lock_packs:
        if not isinstance(pack_entry, dict):
            warnings.append("Lock file has invalid pack entry (not a mapping)")
            continue

        ns = pack_entry.get("namespace", "")
        name = pack_entry.get("name", "")

        # Check pack exists in DB
        pack_row = db.execute(
            "SELECT id FROM packs WHERE namespace = ? AND name = ?",
            (ns, name),
        ).fetchone()
        if not pack_row:
            warnings.append(f"Pack {ns}/{name} in lock file but not installed")
            continue

        # Check artifact content hashes
        for art_entry in pack_entry.get("artifacts", []):
            if not isinstance(art_entry, dict):
                continue
            fqn = art_entry.get("fqn", "")
            expected_hash = art_entry.get("content_hash", "")
            if not fqn or not expected_hash:
                continue

            art_row = db.execute(
                "SELECT content_hash FROM artifacts WHERE fqn = ?",
                (fqn,),
            ).fetchone()

            if not art_row:
                warnings.append(f"Artifact {fqn} in lock file but not in DB")
            else:
                actual_hash = art_row[0] if isinstance(art_row, (tuple, list)) else art_row["content_hash"]
                if actual_hash != expected_hash:
                    warnings.append(
                        f"Content hash mismatch for {fqn}: lock={expected_hash[:12]}... db={actual_hash[:12]}..."
                    )

    return warnings
