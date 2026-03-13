"""Config and MCP tools endpoints."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..cli.instructions import discover_conventions
from ..models import AppConfigResponse, ConnectionValidation, ConventionsResponse, DatabaseAdd, McpServerStatus, McpTool
from ..services.ai_service import create_ai_service
from ..tools.path_utils import safe_resolve_pathlib

logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"])


class ConfigUpdate(BaseModel):
    model: str | None = None
    system_prompt: str | None = None


@router.get("/config")
async def get_config(request: Request) -> AppConfigResponse:
    config = request.app.state.config
    mcp_statuses: list[McpServerStatus] = []

    mcp_manager = request.app.state.mcp_manager
    if mcp_manager:
        for name, status in mcp_manager.get_server_statuses().items():
            mcp_statuses.append(
                McpServerStatus(
                    name=status["name"],
                    transport=status["transport"],
                    status=status["status"],
                    tool_count=status["tool_count"],
                    error_message=status.get("error_message"),
                )
            )

    identity_data: dict[str, str] | None = None
    if config.identity:
        identity_data = {
            "user_id": config.identity.user_id,
            "display_name": config.identity.display_name,
        }

    enforced_fields: list[str] = getattr(request.app.state, "enforced_fields", [])

    return AppConfigResponse(
        ai={
            "base_url": config.ai.base_url,
            "api_key_set": bool(config.ai.api_key),
            "model": config.ai.model,
            "system_prompt": config.ai.user_system_prompt,
        },
        mcp_servers=mcp_statuses,
        identity=identity_data,
        enforced_fields=enforced_fields,
        read_only=config.safety.read_only,
    )


@router.get("/config/conventions")
async def get_conventions() -> ConventionsResponse:
    info = discover_conventions()
    return ConventionsResponse(
        path=str(info.path) if info.path else None,
        content=info.content,
        source=info.source,
        estimated_tokens=info.estimated_tokens,
        warning=info.warning,
    )


@router.patch("/config")
async def update_config(body: ConfigUpdate, request: Request) -> Any:
    from ..config import _DEFAULT_SYSTEM_PROMPT

    config = request.app.state.config
    enforced_fields: list[str] = getattr(request.app.state, "enforced_fields", [])
    changed = False

    if body.model is not None and body.model != config.ai.model:
        if "ai.model" in enforced_fields:
            raise HTTPException(status_code=403, detail="'ai.model' is enforced by team config and cannot be changed")
        config.ai.model = body.model
        changed = True
    if body.system_prompt is not None and body.system_prompt != config.ai.user_system_prompt:
        if "ai.system_prompt" in enforced_fields:
            raise HTTPException(
                status_code=403, detail="'ai.system_prompt' is enforced by team config and cannot be changed"
            )
        config.ai.user_system_prompt = body.system_prompt
        if body.system_prompt:
            config.ai.system_prompt = (
                _DEFAULT_SYSTEM_PROMPT + "\n\n<user_instructions>\n" + body.system_prompt + "\n</user_instructions>"
            )
        else:
            config.ai.system_prompt = _DEFAULT_SYSTEM_PROMPT
        changed = True

    if changed:
        _persist_config(config)

    return {
        "model": config.ai.model,
        "system_prompt": config.ai.user_system_prompt,
    }


def _persist_config(config: Any) -> None:
    from ..config import _get_config_path

    config_path = _get_config_path()
    if not config_path.exists():
        return

    try:
        with open(config_path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        if "ai" not in raw:
            raw["ai"] = {}
        raw["ai"]["model"] = config.ai.model
        if config.ai.user_system_prompt:
            raw["ai"]["system_prompt"] = config.ai.user_system_prompt
        else:
            raw["ai"].pop("system_prompt", None)

        with open(config_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
    except Exception:
        logger.exception("Failed to persist config to %s", config_path)


# ---------------------------------------------------------------------------
# Scoped config editing API (#933)
# ---------------------------------------------------------------------------


class ConfigFieldSetBody(BaseModel):
    """Request body for setting a scoped config field."""

    dot_path: str
    value: str
    scope: str = "personal"  # personal | space | project


class ConfigFieldResetBody(BaseModel):
    """Request body for resetting a scoped config field."""

    dot_path: str
    scope: str = "personal"


@router.get("/config/fields")
async def list_config_fields(request: Request) -> list[dict[str, Any]]:
    """List all settable config fields with type info."""
    from ..services.config_editor import list_settable_fields

    fields = list_settable_fields(include_sensitive=False)
    return [
        {
            "dot_path": f.dot_path,
            "field_type": f.field_type,
            "default": f.default,
            "allowed_values": list(f.allowed_values) if f.allowed_values else None,
            "min_val": f.min_val,
            "max_val": f.max_val,
        }
        for f in fields
    ]


@router.get("/config/fields/{dot_path:path}")
async def get_config_field(dot_path: str, request: Request) -> dict[str, Any]:
    """Get a single config field with source attribution."""
    from dataclasses import asdict

    from ..services.config_editor import (
        _SENSITIVE_FIELDS,
        get_field,
    )

    if dot_path in _SENSITIVE_FIELDS:
        raise HTTPException(status_code=403, detail="Sensitive field cannot be read via API")

    config = request.app.state.config
    enforced: list[str] = getattr(request.app.state, "enforced_fields", [])

    source_map, _ = _build_api_context(request)

    try:
        result = get_field(config, dot_path, source_map, enforced)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "dot_path": result.dot_path,
        "effective_value": result.effective_value,
        "source_layer": result.source_layer,
        "is_enforced": result.is_enforced,
        "field_info": asdict(result.field_info) if result.field_info else None,
    }


@router.put("/config/fields")
async def set_config_field(body: ConfigFieldSetBody, request: Request) -> dict[str, Any]:
    """Set a config field in a specific scope."""
    from ..services.config_editor import (
        _SENSITIVE_FIELDS,
        apply_field_to_config,
        check_write_allowed,
        validate_field_value,
        write_personal_field,
        write_project_field,
        write_space_field,
    )

    if body.dot_path in _SENSITIVE_FIELDS:
        raise HTTPException(status_code=403, detail="Sensitive field cannot be set via API")

    config = request.app.state.config
    enforced: list[str] = getattr(request.app.state, "enforced_fields", [])

    allowed, reason = check_write_allowed(body.dot_path, enforced)
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    parsed, errors = validate_field_value(body.dot_path, body.value)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    if body.scope not in ("personal", "space", "project"):
        raise HTTPException(status_code=400, detail="scope must be personal, space, or project")

    try:
        if body.scope == "personal":
            path = write_personal_field(body.dot_path, parsed)
        elif body.scope == "space":
            space = _get_active_space(request)
            if not space or not space.get("source_file"):
                raise HTTPException(status_code=400, detail="No active space with YAML file")
            db = request.app.state.db
            path = write_space_field(
                body.dot_path,
                parsed,
                Path(space["source_file"]),
                db=db,
                space_id=space["id"],
            )
        elif body.scope == "project":
            project_dir = _resolve_project_dir(request)
            path = write_project_field(body.dot_path, parsed, working_dir=project_dir)
        else:
            raise HTTPException(status_code=400, detail="Invalid scope")

        # Apply to live config (best-effort — file is already saved)
        try:
            apply_field_to_config(config, body.dot_path, parsed)
        except (AttributeError, TypeError):
            pass  # field doesn't map 1:1 to AppConfig attrs — restart needed

        return {
            "dot_path": body.dot_path,
            "value": parsed,
            "scope": body.scope,
            "path": str(path),
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to set config field %s", body.dot_path)
        raise HTTPException(status_code=500, detail="Failed to update config field")


@router.delete("/config/fields")
async def reset_config_field(body: ConfigFieldResetBody, request: Request) -> dict[str, Any]:
    """Reset (remove) a config field from a specific scope."""
    from ..services.config_editor import (
        _SENSITIVE_FIELDS,
        reset_personal_field,
        reset_project_field,
        reset_space_field,
    )

    if body.dot_path in _SENSITIVE_FIELDS:
        raise HTTPException(status_code=403, detail="Sensitive field cannot be reset via API")

    if body.scope not in ("personal", "space", "project"):
        raise HTTPException(status_code=400, detail="scope must be personal, space, or project")

    try:
        deleted = False
        if body.scope == "personal":
            deleted = reset_personal_field(body.dot_path)
        elif body.scope == "space":
            space = _get_active_space(request)
            if not space or not space.get("source_file"):
                raise HTTPException(status_code=400, detail="No active space with YAML file")
            db = request.app.state.db
            deleted = reset_space_field(
                body.dot_path,
                Path(space["source_file"]),
                db=db,
                space_id=space["id"],
            )
        elif body.scope == "project":
            project_dir = _resolve_project_dir(request)
            deleted = reset_project_field(body.dot_path, working_dir=project_dir)

        return {"dot_path": body.dot_path, "scope": body.scope, "deleted": deleted}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to reset config field %s", body.dot_path)
        raise HTTPException(status_code=500, detail="Failed to reset config field")


@router.get("/config/scopes")
async def get_available_scopes(request: Request) -> list[dict[str, Any]]:
    """Return which config scopes are available for the current session."""
    scopes = [{"name": "personal", "available": True, "label": "Personal"}]

    space = _get_active_space(request)
    if space and space.get("source_file"):
        scopes.append(
            {
                "name": "space",
                "available": True,
                "label": "Space (%s)" % space["name"],
            }
        )
    else:
        scopes.append({"name": "space", "available": False, "label": "Space (none active)"})

    # Project scope requires a discoverable project directory
    try:
        _resolve_project_dir(request)
        scopes.append({"name": "project", "available": True, "label": "Project"})
    except HTTPException:
        scopes.append({"name": "project", "available": False, "label": "Project (no context)"})

    return scopes


def _get_active_space(request: Request) -> dict[str, Any] | None:
    """Get the active space from app state, if any."""
    return getattr(request.app.state, "active_space", None)


def _resolve_project_dir(request: Request) -> Path:
    """Resolve the project directory for project-scoped config writes.

    Uses the active space's source file parent directory as the project root.
    Falls back to discovery from the space root (not process cwd).
    Raises HTTPException if no project context can be determined.
    """
    space = _get_active_space(request)
    if space and space.get("source_file"):
        return Path(space["source_file"]).parent

    # Discover from space root, not process cwd
    from ..services.project_config import discover_project_config

    start = _get_working_dir(request)
    proj = discover_project_config(start)
    if proj:
        return proj.parent.parent  # e.g. .anteroom/config.yaml -> project root

    raise HTTPException(
        status_code=400,
        detail="Cannot determine project directory. Load a space or use the CLI.",
    )


def _get_working_dir(request: Request) -> str:
    """Derive the effective working directory from the active space.

    The web server's process cwd is meaningless for context discovery —
    use the space source file's parent directory instead.
    """
    space = _get_active_space(request)
    if space and space.get("source_file"):
        return str(Path(space["source_file"]).parent)
    return os.getcwd()


def _build_api_context(request: Request) -> tuple[dict[str, str], list[str]]:
    """Build source map and enforced fields for API requests."""
    from ..config import _get_config_path
    from ..services.config_editor import _read_yaml, build_full_source_map, collect_env_overrides
    from ..services.project_config import discover_project_config
    from ..services.team_config import discover_team_config

    enforced: list[str] = getattr(request.app.state, "enforced_fields", [])
    working_dir = _get_working_dir(request)
    team_raw: dict[str, Any] = {}

    # Read team config from disk — rooted at the space dir, not process cwd
    try:
        team_path = discover_team_config(cwd=working_dir)
        if team_path:
            from ..services.team_config import load_team_config

            team_raw, enforced = load_team_config(team_path, interactive=False)
    except Exception:
        pass

    # Read personal config from disk
    personal_raw: dict[str, Any] = _read_yaml(_get_config_path())

    # Pack overlays from DB — pass project_path for directory-scoped attachments
    pack_raw: dict[str, Any] = {}
    db = getattr(request.app.state, "db", None)
    space = _get_active_space(request)
    if db is not None:
        try:
            from ..services.config_overlays import collect_pack_overlays, merge_pack_overlays
            from ..services.pack_attachments import (
                get_active_pack_ids,
                get_active_pack_ids_for_space,
                get_attachment_priorities,
            )

            space_id = space["id"] if space else None
            if space_id:
                active_ids = get_active_pack_ids_for_space(db, space_id, project_path=working_dir)
            else:
                active_ids = get_active_pack_ids(db, project_path=working_dir)
            if active_ids:
                overlays = collect_pack_overlays(db, active_ids)
                if overlays:
                    priorities = get_attachment_priorities(db, active_ids)
                    pack_raw = merge_pack_overlays(overlays, priorities) or {}
        except Exception:
            pass  # graceful degradation

    space_raw: dict[str, Any] = {}
    project_raw: dict[str, Any] = {}

    if space and space.get("source_file"):
        sp_path = Path(space["source_file"])
        if sp_path.exists():
            try:
                from ..services.spaces import parse_space_file

                sc = parse_space_file(sp_path)
                space_raw = sc.config or {}
            except Exception:
                pass

    proj_path = discover_project_config(working_dir)
    if proj_path:
        project_raw = _read_yaml(proj_path)
        project_raw.pop("required", None)

    source_map = build_full_source_map(
        team_raw=team_raw,
        pack_raw=pack_raw,
        personal_raw=personal_raw,
        space_raw=space_raw,
        project_raw=project_raw,
        env_overrides=collect_env_overrides(),
    )
    return source_map, enforced


@router.post("/config/validate")
async def validate_connection(request: Request) -> ConnectionValidation:
    config = request.app.state.config
    ai_service = create_ai_service(config.ai)
    valid, message, models = await ai_service.validate_connection()
    return ConnectionValidation(valid=valid, message=message, models=models)


@router.get("/models")
async def list_models(request: Request) -> list[str]:
    config = request.app.state.config
    ai_service = create_ai_service(config.ai)
    try:
        _, _, models = await ai_service.validate_connection()
        return sorted(models)
    except Exception:
        return []


@router.get("/mcp/tools")
async def list_mcp_tools(request: Request) -> list[McpTool]:
    result: list[McpTool] = []

    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_registry:
        for tool_def in tool_registry.get_openai_tools():
            func = tool_def.get("function", {})
            result.append(
                McpTool(
                    name=func.get("name", ""),
                    server_name="builtin",
                    description=func.get("description", ""),
                    input_schema=func.get("parameters", {}),
                )
            )

    mcp_manager = request.app.state.mcp_manager
    if mcp_manager:
        for tool in mcp_manager.get_all_tools():
            result.append(
                McpTool(
                    name=tool["name"],
                    server_name=tool["server_name"],
                    description=tool["description"],
                    input_schema=tool["input_schema"],
                )
            )

    return result


# --- MCP Server Management ---


@router.post("/mcp/servers/{name}/connect")
async def connect_mcp_server(name: str, request: Request) -> Any:
    mcp_manager = request.app.state.mcp_manager
    if not mcp_manager:
        raise HTTPException(status_code=400, detail="No MCP servers configured")
    try:
        await mcp_manager.connect_server(name)
        statuses = mcp_manager.get_server_statuses()
        return statuses.get(name, {"name": name, "status": "unknown"})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/mcp/servers/{name}/disconnect")
async def disconnect_mcp_server(name: str, request: Request) -> Any:
    mcp_manager = request.app.state.mcp_manager
    if not mcp_manager:
        raise HTTPException(status_code=400, detail="No MCP servers configured")
    try:
        await mcp_manager.disconnect_server(name)
        statuses = mcp_manager.get_server_statuses()
        return statuses.get(name, {"name": name, "status": "disconnected"})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/mcp/servers/{name}/reconnect")
async def reconnect_mcp_server(name: str, request: Request) -> Any:
    mcp_manager = request.app.state.mcp_manager
    if not mcp_manager:
        raise HTTPException(status_code=400, detail="No MCP servers configured")
    try:
        await mcp_manager.reconnect_server(name)
        statuses = mcp_manager.get_server_statuses()
        return statuses.get(name, {"name": name, "status": "unknown"})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Databases ---


@router.get("/databases")
async def list_databases(request: Request) -> Any:
    if not hasattr(request.app.state, "db_manager"):
        return [{"name": "personal", "path": ""}]
    return request.app.state.db_manager.list_databases()


@router.post("/databases", status_code=201)
async def add_database(body: DatabaseAdd, request: Request) -> Any:
    if body.name == "personal":
        raise HTTPException(status_code=400, detail="Cannot use reserved name 'personal'")
    if not hasattr(request.app.state, "db_manager"):
        raise HTTPException(status_code=400, detail="Database manager not available")

    db_manager = request.app.state.db_manager
    try:
        existing = db_manager.list_databases()
        if any(d["name"] == body.name for d in existing):
            raise HTTPException(status_code=409, detail=f"Database '{body.name}' already exists")
    except HTTPException:
        raise
    except Exception:
        pass

    try:
        # SECURITY-REVIEW: path validated below — extension allowlist + is_relative_to(home)
        db_path = safe_resolve_pathlib(Path(os.path.expanduser(body.path)))
        # Only allow .db/.sqlite/.sqlite3 extensions
        if db_path.suffix.lower() not in (".db", ".sqlite", ".sqlite3"):
            raise HTTPException(
                status_code=400,
                detail="Database path must end with .db, .sqlite, or .sqlite3",
            )
        # Restrict to user's home directory
        home = safe_resolve_pathlib(Path.home())
        if not db_path.is_relative_to(home):
            raise HTTPException(
                status_code=400,
                detail="Database path must be within your home directory",
            )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_manager.add(body.name, db_path)
        _persist_database(body.name, str(db_path))
        return {"name": body.name, "path": str(db_path)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to add database %s", body.name)
        raise HTTPException(status_code=400, detail="Failed to add database")


@router.delete("/databases/{name}", status_code=204)
async def remove_database(name: str, request: Request) -> None:
    if name == "personal":
        raise HTTPException(status_code=400, detail="Cannot remove personal database")
    if not hasattr(request.app.state, "db_manager"):
        raise HTTPException(status_code=400, detail="Database manager not available")

    db_manager = request.app.state.db_manager
    existing = db_manager.list_databases()
    if not any(d["name"] == name for d in existing):
        raise HTTPException(status_code=404, detail=f"Database '{name}' not found")

    db_manager.remove(name)
    _remove_database_from_config(name)
    return None


def _persist_database(name: str, path: str) -> None:
    from ..config import _get_config_path

    config_path = _get_config_path()
    try:
        raw: dict[str, Any] = {}
        if config_path.exists():
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}

        if "shared_databases" not in raw:
            raw["shared_databases"] = []

        if not any(d.get("name") == name for d in raw["shared_databases"]):
            raw["shared_databases"].append({"name": name, "path": path})
            with open(config_path, "w") as f:
                yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
    except Exception:
        logger.exception("Failed to persist database to config")


@router.get("/browse")
async def browse_directory(path: str = "~") -> Any:
    """List directories and .db files at the given path for file browsing."""
    try:
        resolved = safe_resolve_pathlib(Path(os.path.expanduser(path)))
        home = safe_resolve_pathlib(Path.home())
        if not resolved.is_relative_to(home):
            raise HTTPException(status_code=403, detail="Access denied: path must be within home directory")
        if not resolved.is_dir():
            raise HTTPException(status_code=400, detail="Not a directory")

        entries = []
        try:
            for entry in sorted(resolved.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    entries.append({"name": entry.name, "type": "dir"})
                elif entry.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                    entries.append({"name": entry.name, "type": "file"})
        except PermissionError:
            pass

        parent = str(resolved.parent) if resolved != resolved.parent else None
        return {"current": str(resolved), "parent": parent, "entries": entries}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Browse directory error for path=%s", path)
        raise HTTPException(status_code=400, detail="Unable to browse the requested path")


def _remove_database_from_config(name: str) -> None:
    from ..config import _get_config_path

    config_path = _get_config_path()
    try:
        if not config_path.exists():
            return
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        dbs = raw.get("shared_databases", [])
        raw["shared_databases"] = [d for d in dbs if d.get("name") != name]
        with open(config_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
    except Exception:
        logger.exception("Failed to remove database from config")
