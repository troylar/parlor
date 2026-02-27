"""Artifact API endpoints (read-only for Phase 1)."""

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
    for r in results:
        r.pop("content", None)
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
    return art
