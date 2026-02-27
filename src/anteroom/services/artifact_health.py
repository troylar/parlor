"""Artifact health check engine.

Analyzes all loaded artifacts across every layer — built-in defaults,
global packs, project packs, local overrides — and reports quality issues,
conflicts, redundancies, and optimization opportunities.
"""

from __future__ import annotations

import enum
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import artifact_storage, pack_lock
from .artifacts import ArtifactSource, ArtifactType, validate_fqn

logger = logging.getLogger(__name__)

_SOURCE_PRECEDENCE: dict[str, int] = {s.value: i for i, s in enumerate(ArtifactSource)}


class HealthSeverity(str, enum.Enum):
    ERROR = "error"
    WARN = "warn"
    INFO = "info"


@dataclass(frozen=True)
class HealthIssue:
    severity: HealthSeverity
    category: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    fixable: bool = False


@dataclass
class HealthReport:
    issues: list[HealthIssue] = field(default_factory=list)
    artifact_count: int = 0
    pack_count: int = 0
    total_size_bytes: int = 0
    estimated_tokens: int = 0

    @property
    def healthy(self) -> bool:
        return not any(i.severity == HealthSeverity.ERROR for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == HealthSeverity.ERROR)

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == HealthSeverity.WARN)

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == HealthSeverity.INFO)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "artifact_count": self.artifact_count,
            "pack_count": self.pack_count,
            "total_size_bytes": self.total_size_bytes,
            "estimated_tokens": self.estimated_tokens,
            "error_count": self.error_count,
            "warn_count": self.warn_count,
            "info_count": self.info_count,
            "issues": [
                {
                    "severity": i.severity.value,
                    "category": i.category,
                    "message": i.message,
                    "details": i.details,
                    "fixable": i.fixable,
                }
                for i in self.issues
            ],
        }


def check_config_overlay_conflicts(db: sqlite3.Connection) -> list[HealthIssue]:
    """Detect config overlays from different sources that set the same field to different values."""
    overlays = artifact_storage.list_artifacts(db, artifact_type=ArtifactType.CONFIG_OVERLAY)
    if len(overlays) < 2:
        return []

    issues: list[HealthIssue] = []
    field_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for overlay in overlays:
        content = overlay.get("content", "")
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        for key, value in _flatten_dict(parsed):
            field_sources[key].append(
                {
                    "fqn": overlay["fqn"],
                    "source": overlay.get("source", ""),
                    "value": value,
                }
            )

    for key, sources in field_sources.items():
        values = [s["value"] for s in sources]
        if len(set(str(v) for v in values)) > 1:
            winner = max(sources, key=lambda s: _SOURCE_PRECEDENCE.get(s["source"], 0))
            issues.append(
                HealthIssue(
                    severity=HealthSeverity.ERROR,
                    category="config_conflict",
                    message=f"Config conflict on '{key}': {len(sources)} overlays set different values",
                    details={
                        "field": key,
                        "sources": [
                            {"fqn": s["fqn"], "source": s["source"], "value": str(s["value"])} for s in sources
                        ],
                        "winner_fqn": winner["fqn"],
                    },
                )
            )

    return issues


def check_skill_name_collisions(db: sqlite3.Connection) -> list[HealthIssue]:
    """Detect skills with the same name from different sources."""
    skills = artifact_storage.list_artifacts(db, artifact_type=ArtifactType.SKILL)
    if len(skills) < 2:
        return []

    issues: list[HealthIssue] = []
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for s in skills:
        by_name[s["name"]].append(s)

    for name, group in by_name.items():
        if len(group) < 2:
            continue
        winner = max(group, key=lambda g: _SOURCE_PRECEDENCE.get(g.get("source", ""), 0))
        shadowed = [g for g in group if g["fqn"] != winner["fqn"]]
        issues.append(
            HealthIssue(
                severity=HealthSeverity.WARN,
                category="skill_collision",
                message=f"Skill name '{name}' defined in {len(group)} artifacts",
                details={
                    "name": name,
                    "active_fqn": winner["fqn"],
                    "active_source": winner.get("source", ""),
                    "shadowed": [{"fqn": s["fqn"], "source": s.get("source", "")} for s in shadowed],
                },
            )
        )

    return issues


def check_shadow_warnings(db: sqlite3.Connection) -> list[HealthIssue]:
    """Detect when a higher-precedence artifact shadows a lower one of the same type+name."""
    all_artifacts = artifact_storage.list_artifacts(db)
    if len(all_artifacts) < 2:
        return []

    issues: list[HealthIssue] = []
    by_type_name: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for a in all_artifacts:
        by_type_name[(a["type"], a["name"])].append(a)

    for (art_type, name), group in by_type_name.items():
        if len(group) < 2:
            continue
        sorted_group = sorted(group, key=lambda g: _SOURCE_PRECEDENCE.get(g.get("source", ""), 0), reverse=True)
        active = sorted_group[0]
        for shadowed in sorted_group[1:]:
            issues.append(
                HealthIssue(
                    severity=HealthSeverity.INFO,
                    category="shadow",
                    message=f"{active['fqn']} shadows {shadowed['fqn']}",
                    details={
                        "active_fqn": active["fqn"],
                        "active_source": active.get("source", ""),
                        "shadowed_fqn": shadowed["fqn"],
                        "shadowed_source": shadowed.get("source", ""),
                    },
                )
            )

    return issues


def check_empty_artifacts(db: sqlite3.Connection) -> list[HealthIssue]:
    """Flag artifacts with trivially small content."""
    all_artifacts = artifact_storage.list_artifacts(db)
    issues: list[HealthIssue] = []

    for a in all_artifacts:
        content = a.get("content", "")
        word_count = len(content.split())
        if word_count < 10:
            issues.append(
                HealthIssue(
                    severity=HealthSeverity.WARN,
                    category="empty_artifact",
                    message=f"{a['fqn']} has only {word_count} words (likely too brief)",
                    details={"fqn": a["fqn"], "word_count": word_count},
                )
            )

    return issues


def check_malformed_artifacts(db: sqlite3.Connection) -> list[HealthIssue]:
    """Validate FQN format and YAML syntax for all artifacts."""
    all_artifacts = artifact_storage.list_artifacts(db)
    issues: list[HealthIssue] = []

    for a in all_artifacts:
        fqn = a.get("fqn", "")
        if not validate_fqn(fqn):
            issues.append(
                HealthIssue(
                    severity=HealthSeverity.ERROR,
                    category="malformed",
                    message=f"Invalid FQN format: {fqn!r}",
                    details={"fqn": fqn},
                )
            )

        art_type = a.get("type", "")
        try:
            ArtifactType(art_type)
        except ValueError:
            issues.append(
                HealthIssue(
                    severity=HealthSeverity.ERROR,
                    category="malformed",
                    message=f"Invalid artifact type '{art_type}' for {fqn}",
                    details={"fqn": fqn, "type": art_type},
                )
            )

        source = a.get("source", "")
        try:
            ArtifactSource(source)
        except ValueError:
            issues.append(
                HealthIssue(
                    severity=HealthSeverity.ERROR,
                    category="malformed",
                    message=f"Invalid artifact source '{source}' for {fqn}",
                    details={"fqn": fqn, "source": source},
                )
            )

        if art_type in ("config_overlay", "mcp_server"):
            content = a.get("content", "")
            try:
                parsed = yaml.safe_load(content)
                if not isinstance(parsed, dict):
                    issues.append(
                        HealthIssue(
                            severity=HealthSeverity.WARN,
                            category="malformed",
                            message=f"{fqn} content is not a YAML mapping",
                            details={"fqn": fqn, "type": art_type},
                        )
                    )
            except yaml.YAMLError:
                issues.append(
                    HealthIssue(
                        severity=HealthSeverity.ERROR,
                        category="malformed",
                        message=f"Invalid YAML in {fqn}",
                        details={"fqn": fqn},
                    )
                )

    return issues


def check_lock_drift(db: sqlite3.Connection, project_dir: Path | None = None) -> list[HealthIssue]:
    """Check lock file matches current DB state."""
    if project_dir is None:
        return []

    warnings = pack_lock.validate_lock(db, project_dir)
    issues: list[HealthIssue] = []

    for w in warnings:
        severity = HealthSeverity.ERROR if "mismatch" in w.lower() else HealthSeverity.WARN
        issues.append(
            HealthIssue(
                severity=severity,
                category="lock_drift",
                message=w,
                details={},
            )
        )

    return issues


def check_bloat(db: sqlite3.Connection) -> list[HealthIssue]:
    """Report total artifact count, content size, and estimated token impact."""
    all_artifacts = artifact_storage.list_artifacts(db)
    if not all_artifacts:
        return []

    total_size = sum(len(a.get("content", "")) for a in all_artifacts)
    estimated_tokens = total_size // 4

    issues: list[HealthIssue] = []

    sorted_by_size = sorted(all_artifacts, key=lambda a: len(a.get("content", "")), reverse=True)
    top_n = sorted_by_size[:5]

    issues.append(
        HealthIssue(
            severity=HealthSeverity.INFO,
            category="bloat",
            message=(
                f"{len(all_artifacts)} artifacts, {total_size:,} bytes, ~{estimated_tokens:,} tokens in system prompt"
            ),
            details={
                "artifact_count": len(all_artifacts),
                "total_size_bytes": total_size,
                "estimated_tokens": estimated_tokens,
                "top_by_size": [
                    {"fqn": a["fqn"], "size": len(a.get("content", "")), "tokens": len(a.get("content", "")) // 4}
                    for a in top_n
                ],
            },
        )
    )

    return issues


def check_orphaned_artifacts(db: sqlite3.Connection) -> list[HealthIssue]:
    """Find artifacts not linked to any pack and not from built_in/local/inline sources."""
    pack_count = db.execute("SELECT COUNT(*) AS cnt FROM packs").fetchone()
    pack_count_val = pack_count[0] if isinstance(pack_count, (tuple, list)) else pack_count["cnt"]
    if pack_count_val == 0:
        return []

    excluded_sources = ("built_in", "local", "inline")
    placeholders = ",".join("?" for _ in excluded_sources)
    rows = db.execute(
        f"""SELECT a.fqn, a.source FROM artifacts a
           WHERE a.id NOT IN (SELECT artifact_id FROM pack_artifacts)
           AND a.source NOT IN ({placeholders})""",
        excluded_sources,
    ).fetchall()

    issues: list[HealthIssue] = []
    for r in rows:
        r_dict = dict(r) if hasattr(r, "keys") else {"fqn": r[0], "source": r[1]}
        issues.append(
            HealthIssue(
                severity=HealthSeverity.WARN,
                category="orphaned",
                message=f"{r_dict['fqn']} not linked to any pack (source: {r_dict['source']})",
                details={"fqn": r_dict["fqn"], "source": r_dict["source"]},
            )
        )

    return issues


def check_duplicate_content(db: sqlite3.Connection) -> list[HealthIssue]:
    """Detect artifacts with identical content (exact duplicates by hash)."""
    all_artifacts = artifact_storage.list_artifacts(db)
    if len(all_artifacts) < 2:
        return []

    issues: list[HealthIssue] = []
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for a in all_artifacts:
        chash = a.get("content_hash", "")
        if chash:
            by_hash[chash].append(a)

    for chash, group in by_hash.items():
        if len(group) < 2:
            continue
        issues.append(
            HealthIssue(
                severity=HealthSeverity.WARN,
                category="duplicate_content",
                message=f"{len(group)} artifacts share identical content (hash {chash[:12]}...)",
                details={
                    "content_hash": chash,
                    "artifacts": [{"fqn": a["fqn"], "source": a.get("source", "")} for a in group],
                },
                fixable=True,
            )
        )

    return issues


def fix_duplicate_content(db: sqlite3.Connection) -> int:
    """Remove exact duplicate artifacts, keeping the highest-precedence copy.

    Skips artifacts referenced by packs (via pack_artifacts junction table).
    All deletions run in a single transaction.
    Returns the number of artifacts deleted.
    """
    all_artifacts = artifact_storage.list_artifacts(db)
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for a in all_artifacts:
        chash = a.get("content_hash", "")
        if chash:
            by_hash[chash].append(a)

    # Collect IDs referenced by packs so we never delete them
    pack_ref_rows = db.execute("SELECT DISTINCT artifact_id FROM pack_artifacts").fetchall()
    pack_artifact_ids: set[str] = {r[0] if isinstance(r, (tuple, list)) else r["artifact_id"] for r in pack_ref_rows}

    deleted = 0
    for group in by_hash.values():
        if len(group) < 2:
            continue
        sorted_group = sorted(group, key=lambda g: _SOURCE_PRECEDENCE.get(g.get("source", ""), 0), reverse=True)
        for dup in sorted_group[1:]:
            if dup["id"] in pack_artifact_ids:
                logger.debug("Skipping duplicate %s — referenced by a pack", dup["fqn"])
                continue
            artifact_storage.delete_artifact(db, dup["id"], commit=False)
            deleted += 1
            logger.info("Removed duplicate artifact: %s (kept %s)", dup["fqn"], sorted_group[0]["fqn"])

    if deleted > 0:
        db.commit()

    return deleted


def run_health_check(
    db: sqlite3.Connection,
    *,
    project_dir: Path | None = None,
    fix: bool = False,
) -> HealthReport:
    """Run all structural health checks and return a report."""
    report = HealthReport()

    all_artifacts = artifact_storage.list_artifacts(db)
    report.artifact_count = len(all_artifacts)
    report.total_size_bytes = sum(len(a.get("content", "")) for a in all_artifacts)
    report.estimated_tokens = report.total_size_bytes // 4

    pack_count = db.execute("SELECT COUNT(*) AS cnt FROM packs").fetchone()
    report.pack_count = pack_count[0] if isinstance(pack_count, (tuple, list)) else pack_count["cnt"]

    # Run all diagnostic checks first
    report.issues.extend(check_config_overlay_conflicts(db))
    report.issues.extend(check_skill_name_collisions(db))
    report.issues.extend(check_shadow_warnings(db))
    report.issues.extend(check_empty_artifacts(db))
    report.issues.extend(check_malformed_artifacts(db))
    report.issues.extend(check_lock_drift(db, project_dir))
    report.issues.extend(check_orphaned_artifacts(db))
    report.issues.extend(check_duplicate_content(db))
    report.issues.extend(check_bloat(db))

    # Apply fixes after diagnostics so checks see pre-fix state
    if fix:
        deleted = fix_duplicate_content(db)
        if deleted > 0:
            report.issues.append(
                HealthIssue(
                    severity=HealthSeverity.INFO,
                    category="fix_applied",
                    message=f"Removed {deleted} duplicate artifact(s)",
                    details={"deleted_count": deleted},
                )
            )

    return report


def _flatten_dict(d: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten a nested dict into dotted key paths."""
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, key))
        else:
            items.append((key, v))
    return items
