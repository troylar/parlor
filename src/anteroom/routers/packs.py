"""Pack API endpoints."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..services import packs
from ..services.pack_sources import list_cached_sources

logger = logging.getLogger(__name__)

router = APIRouter(tags=["packs"])

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_SAFE_ID_RE = re.compile(r"^[a-f0-9-]{32,36}$")


def _rebuild_config(request: Request, db: Any) -> tuple[bool, bool, str | None]:
    """Rebuild effective config after pack changes and update app state.

    Returns ``(success, compliance_failure, reason)`` tuple:

    - ``(True, False, None)`` — config rebuilt successfully.
    - ``(False, True, reason)`` — compliance violation; caller should rollback.
    - ``(False, False, None)`` — infrastructure error; previous config kept,
      but the pack mutation should NOT be rolled back (the pack itself
      is fine, it's the rebuild machinery that failed).
    """
    from ..services.config_overlays import ComplianceError, rebuild_effective_config

    previous_config = getattr(request.app.state, "config", None)
    previous_enforced = getattr(request.app.state, "enforced_fields", None)
    try:
        result = rebuild_effective_config(db, previous_config=previous_config)
        # Stage new values before committing — if derived state fails,
        # roll back both config and enforced_fields atomically.
        request.app.state.config = result.config
        request.app.state.enforced_fields = result.enforced_fields
        try:
            _refresh_derived_state(request, result.config)
        except Exception:
            # Roll back — derived state reconstruction failed
            request.app.state.config = previous_config
            request.app.state.enforced_fields = previous_enforced
            raise
        for warning in result.warnings:
            logger.warning(warning)
        return True, False, None
    except ComplianceError as exc:
        logger.warning(
            "Config rebuild blocked (compliance failure) — keeping previous config",
            exc_info=True,
        )
        return False, True, str(exc)
    except Exception:
        logger.warning(
            "Config rebuild failed — keeping previous config",
            exc_info=True,
        )
        return False, False, None


def _refresh_derived_state(request: Request, config: Any) -> None:
    """Refresh config-derived singletons on app.state after a config rebuild."""
    request.app.state.rate_limit_config = getattr(config, "rate_limit", None)

    safety = getattr(config, "safety", None)
    dlp_cfg = getattr(safety, "dlp", None) if safety else None
    if dlp_cfg is not None and dlp_cfg.enabled:
        from ..services.dlp import DlpScanner

        request.app.state.dlp_scanner = DlpScanner(dlp_cfg)
    else:
        request.app.state.dlp_scanner = None

    inj_cfg = getattr(safety, "prompt_injection", None) if safety else None
    if inj_cfg is not None and inj_cfg.enabled:
        from ..services.injection_detector import InjectionDetector

        request.app.state.injection_detector = InjectionDetector(inj_cfg)
    else:
        request.app.state.injection_detector = None


def _rollback_pack_mutation(
    db: Any,
    pack_id: str,
    project_path: str | None,
    action: str,
) -> None:
    """Undo a pack attachment/detachment after config rebuild failure."""
    from ..services.pack_attachments import attach_pack as _do_attach
    from ..services.pack_attachments import detach_pack as _do_detach

    try:
        if action == "detach":
            _do_detach(db, pack_id, project_path=project_path)
            logger.info("Rolled back attach for pack %s (detached)", pack_id)
        elif action == "attach":
            _do_attach(db, pack_id, project_path=project_path, check_overlay_conflicts=False)
            logger.info("Rolled back detach for pack %s (re-attached)", pack_id)
    except Exception:
        logger.error("Rollback failed for pack %s", pack_id, exc_info=True)


def _reload_registries_only(request: Request, db: Any) -> None:
    """Refresh artifact registry, rule enforcer, and skill registry.

    Assumes config has already been rebuilt. Use when rebuild was done separately.
    """
    registry = getattr(request.app.state, "artifact_registry", None)
    if registry is not None:
        registry.load_from_db(db)
    rule_enforcer = getattr(request.app.state, "rule_enforcer", None)
    if rule_enforcer is not None and registry is not None:
        from ..services.artifacts import ArtifactType

        rule_enforcer.load_rules(registry.list_all(artifact_type=ArtifactType.RULE))
    skill_registry = getattr(request.app.state, "skill_registry", None)
    if skill_registry is not None and registry is not None:
        skill_registry.load_from_artifacts(registry)


def _reload_registries(request: Request, db: Any) -> None:
    """Rebuild config and refresh all registries after pack changes.

    Used for endpoints where rollback is not applicable (remove, refresh).
    """
    _rebuild_config(request, db)
    _reload_registries_only(request, db)


def _validate_pack_path_params(namespace: str, name: str) -> None:
    """Validate namespace and name path parameters against safe name regex."""
    if not _SAFE_NAME_RE.match(namespace):
        raise HTTPException(status_code=400, detail=f"Invalid namespace: {namespace!r}")
    if not _SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid pack name: {name!r}")


def _resolve_or_409(db: Any, namespace: str, name: str) -> dict[str, Any]:
    """Resolve a pack by namespace/name, raising 409 on ambiguity or 404 if not found."""
    match, candidates = packs.resolve_pack(db, namespace, name)
    if match:
        return match
    if candidates:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Multiple packs match {namespace}/{name}",
                "candidates": [{"id": c["id"], "version": c.get("version", "")} for c in candidates],
            },
        )
    raise HTTPException(status_code=404, detail="Pack not found")


@router.get("/packs")
async def list_packs(request: Request) -> list[dict[str, Any]]:
    """List all installed packs with artifact counts."""
    db = request.app.state.db
    result = packs.list_packs(db)
    for p in result:
        p.pop("source_path", None)
    return result


@router.get("/packs/sources")
async def list_sources(request: Request) -> list[dict[str, Any]]:
    """List configured pack sources with cache status."""
    config = request.app.state.config
    sources = getattr(config, "pack_sources", [])
    data_dir = config.app.data_dir

    cached = list_cached_sources(data_dir)
    cached_urls = {c.url: c for c in cached}

    result: list[dict[str, Any]] = []
    for src in sources:
        cached_src = cached_urls.get(src.url)
        result.append(
            {
                "url": src.url,
                "branch": src.branch,
                "refresh_interval": src.refresh_interval,
                "cached": cached_src is not None,
                "ref": cached_src.ref[:12] if cached_src and cached_src.ref else None,
            }
        )
    return result


@router.post("/packs/refresh")
async def refresh_sources(request: Request) -> dict[str, Any]:
    """Manually trigger a refresh of all configured pack sources."""
    config = request.app.state.config
    sources = getattr(config, "pack_sources", [])
    if not sources:
        return {"sources": [], "quarantined": [], "quarantine_reason": None}

    db = request.app.state.db
    data_dir = config.app.data_dir

    from ..services.pack_refresh import PackRefreshWorker

    worker = PackRefreshWorker(db=db, data_dir=data_dir, sources=sources)
    results = worker.refresh_all()

    quarantined_ids: list[str] = []
    quarantine_reason: str | None = None

    if any(r.changed for r in results):
        success, compliance_failure, compliance_msg = _rebuild_config(request, db)
        if success:
            _reload_registries_only(request, db)
        elif compliance_failure:
            quarantine_reason = compliance_msg
            from ..services.pack_attachments import detach_pack as _q_detach

            for r in results:
                for pid in r.changed_pack_ids:
                    try:
                        _q_detach(db, pid)
                        quarantined_ids.append(pid)
                    except Exception:
                        logger.error("Failed to quarantine pack %s", pid, exc_info=True)
            if quarantined_ids:
                logger.warning(
                    "Quarantined %d pack(s) after refresh rebuild failure",
                    len(quarantined_ids),
                )
            # Second rebuild from the now-clean attachment set
            _rebuild_config(request, db)
            _reload_registries_only(request, db)
        else:
            _reload_registries_only(request, db)

    sources_out = [
        {
            "url": r.url,
            "success": r.success,
            "packs_installed": r.packs_installed,
            "packs_updated": r.packs_updated,
            "packs_attached": r.packs_attached,
            "changed": r.changed,
            "error": r.error,
        }
        for r in results
    ]
    return {
        "sources": sources_out,
        "quarantined": quarantined_ids,
        "quarantine_reason": quarantine_reason,
    }


class AttachRequest(BaseModel):
    project_path: str | None = None


# --- by-id routes MUST come before {namespace}/{name} wildcard routes ---
# FastAPI uses first-match routing; if the wildcard routes are first,
# "by-id" is captured as the namespace parameter.


@router.get("/packs/by-id/{pack_id}")
async def get_pack_by_id(request: Request, pack_id: str) -> dict[str, Any]:
    """Get a pack by its unique ID."""
    if not _SAFE_ID_RE.match(pack_id):
        raise HTTPException(status_code=400, detail="Invalid pack ID format")
    db = request.app.state.db
    result = packs.get_pack_by_id(db, pack_id)
    if not result:
        raise HTTPException(status_code=404, detail="Pack not found")
    result.pop("source_path", None)
    for art in result.get("artifacts", []):
        art.pop("content", None)
    return result


@router.delete("/packs/by-id/{pack_id}")
async def remove_pack_by_id(request: Request, pack_id: str) -> dict[str, str]:
    """Remove a pack by its unique ID."""
    if not _SAFE_ID_RE.match(pack_id):
        raise HTTPException(status_code=400, detail="Invalid pack ID format")
    db = request.app.state.db
    removed = packs.remove_pack_by_id(db, pack_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Pack not found")
    _reload_registries(request, db)
    return {"status": "removed"}


# --- {namespace}/{name} wildcard routes ---


@router.post("/packs/{namespace}/{name}/attach")
async def attach_pack(request: Request, namespace: str, name: str, body: AttachRequest) -> dict[str, Any]:
    """Attach a pack to global or project scope."""
    _validate_pack_path_params(namespace, name)
    from ..services.pack_attachments import attach_pack as do_attach

    db = request.app.state.db
    pack = _resolve_or_409(db, namespace, name)

    try:
        result = do_attach(db, pack["id"], project_path=body.project_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    success, compliance_failure, _ = _rebuild_config(request, db)
    if compliance_failure:
        _rollback_pack_mutation(db, pack["id"], body.project_path, "detach")
        raise HTTPException(
            status_code=409,
            detail="Pack attached but config rebuild failed (compliance violation). Attachment rolled back.",
        )
    if not success:
        raise HTTPException(
            status_code=500,
            detail="Pack attached but config rebuild failed."
            " The attachment is saved and will take effect on next restart.",
        )
    _reload_registries_only(request, db)
    return result


@router.delete("/packs/{namespace}/{name}/attach")
async def detach_pack(
    request: Request,
    namespace: str,
    name: str,
    project_path: str | None = Query(default=None),
) -> dict[str, str]:
    """Detach a pack from global or project scope."""
    _validate_pack_path_params(namespace, name)
    from ..services.pack_attachments import detach_pack as do_detach

    db = request.app.state.db
    pack = _resolve_or_409(db, namespace, name)

    removed = do_detach(db, pack["id"], project_path=project_path)
    if not removed:
        raise HTTPException(status_code=404, detail="Attachment not found")
    success, compliance_failure, _ = _rebuild_config(request, db)
    if compliance_failure:
        _rollback_pack_mutation(db, pack["id"], project_path, "attach")
        raise HTTPException(
            status_code=409,
            detail="Pack detached but config rebuild failed (compliance violation). Detachment rolled back.",
        )
    if not success:
        raise HTTPException(
            status_code=500,
            detail="Pack detached but config rebuild failed."
            " The detachment is saved and will take effect on next restart.",
        )
    _reload_registries_only(request, db)
    return {"status": "detached"}


@router.get("/packs/{namespace}/{name}/attachments")
async def list_pack_attachments(request: Request, namespace: str, name: str) -> list[dict[str, Any]]:
    """List attachments for a specific pack."""
    _validate_pack_path_params(namespace, name)
    from ..services.pack_attachments import list_attachments_for_pack

    db = request.app.state.db
    pack = _resolve_or_409(db, namespace, name)

    return list_attachments_for_pack(db, pack["id"])


@router.delete("/packs/{namespace}/{name}")
async def remove_pack(request: Request, namespace: str, name: str) -> dict[str, str]:
    """Remove an installed pack."""
    _validate_pack_path_params(namespace, name)
    db = request.app.state.db
    pack = _resolve_or_409(db, namespace, name)
    removed = packs.remove_pack_by_id(db, pack["id"])
    if not removed:
        raise HTTPException(status_code=404, detail="Pack not found")
    _reload_registries(request, db)
    return {"status": "removed"}


@router.get("/packs/{namespace}/{name}")
async def get_pack(request: Request, namespace: str, name: str) -> dict[str, Any]:
    """Get a pack with its full artifact list."""
    _validate_pack_path_params(namespace, name)
    db = request.app.state.db
    resolved = _resolve_or_409(db, namespace, name)
    pack = packs.get_pack_by_id(db, resolved["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")
    pack.pop("source_path", None)
    for art in pack.get("artifacts", []):
        art.pop("content", None)
    return pack
