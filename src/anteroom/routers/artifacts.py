"""Artifact API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..services import artifact_storage
from ..services.artifacts import validate_fqn

router = APIRouter(tags=["artifacts"])


@router.get("/artifacts")
async def list_artifacts(
    request: Request,
    type: str | None = Query(None, pattern="^(skill|rule|instruction|context|memory|mcp_server|config_overlay)$"),
    namespace: str | None = Query(None, max_length=64),
    source: str | None = Query(None, pattern="^(built_in|global|team|project|local|inline)$"),
) -> list[dict[str, Any]]:
    """List all artifacts with optional filtering by type, namespace, or source."""
    db = request.app.state.db
    results = artifact_storage.list_artifacts(db, artifact_type=type, namespace=namespace, source=source)
    # Batch-fetch latest version for all artifacts in a single query
    artifact_ids = [r["id"] for r in results]
    version_map: dict[str, int] = {}
    if artifact_ids:
        placeholders = ",".join("?" for _ in artifact_ids)
        rows = db.execute(
            f"SELECT artifact_id, MAX(version) as max_ver FROM artifact_versions"  # noqa: S608
            f" WHERE artifact_id IN ({placeholders}) GROUP BY artifact_id",
            artifact_ids,
        ).fetchall()
        for row in rows:
            aid = row[0] if isinstance(row, (tuple, list)) else row["artifact_id"]
            ver = row[1] if isinstance(row, (tuple, list)) else row["max_ver"]
            version_map[aid] = ver
    for r in results:
        r.pop("content", None)
        r["version"] = version_map.get(r["id"])
    return results


@router.get("/artifacts/{fqn:path}")
async def get_artifact(
    request: Request,
    fqn: str,
) -> dict[str, Any]:
    """Get a single artifact by FQN (e.g. @core/skill/greet)."""
    if not validate_fqn(fqn):
        raise HTTPException(status_code=400, detail="Invalid artifact FQN format")

    db = request.app.state.db
    art = artifact_storage.get_artifact_by_fqn(db, fqn)
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")

    versions = artifact_storage.list_artifact_versions(db, art["id"])
    art["versions"] = versions
    art["version"] = versions[0]["version"] if versions else None
    return art


@router.delete("/artifacts/{fqn:path}")
async def delete_artifact(
    request: Request,
    fqn: str,
) -> dict[str, str]:
    """Delete an artifact by FQN."""
    if not validate_fqn(fqn):
        raise HTTPException(status_code=400, detail="Invalid artifact FQN format")

    db = request.app.state.db
    art = artifact_storage.get_artifact_by_fqn(db, fqn)
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")

    if art.get("source") == "built_in":
        raise HTTPException(status_code=403, detail="Cannot delete built-in artifacts")

    artifact_storage.delete_artifact(db, art["id"])

    registry = getattr(request.app.state, "artifact_registry", None)
    if registry is not None:
        registry.remove(fqn)

    return {"status": "deleted", "fqn": fqn}
