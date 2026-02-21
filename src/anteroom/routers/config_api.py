"""Config and MCP tools endpoints."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
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

    return AppConfigResponse(
        ai={
            "base_url": config.ai.base_url,
            "api_key_set": bool(config.ai.api_key),
            "model": config.ai.model,
            "system_prompt": config.ai.user_system_prompt,
        },
        mcp_servers=mcp_statuses,
        identity=identity_data,
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
async def update_config(body: ConfigUpdate, request: Request):
    from ..config import _DEFAULT_SYSTEM_PROMPT

    config = request.app.state.config
    changed = False

    if body.model is not None and body.model != config.ai.model:
        config.ai.model = body.model
        changed = True
    if body.system_prompt is not None and body.system_prompt != config.ai.user_system_prompt:
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


def _persist_config(config) -> None:
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
async def connect_mcp_server(name: str, request: Request):
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
async def disconnect_mcp_server(name: str, request: Request):
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
async def reconnect_mcp_server(name: str, request: Request):
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
async def list_databases(request: Request):
    if not hasattr(request.app.state, "db_manager"):
        return [{"name": "personal", "path": ""}]
    return request.app.state.db_manager.list_databases()


@router.post("/databases", status_code=201)
async def add_database(body: DatabaseAdd, request: Request):
    if body.name == "personal":
        raise HTTPException(status_code=400, detail="Cannot use reserved name 'personal'")
    if not hasattr(request.app.state, "db_manager"):
        raise HTTPException(status_code=400, detail="Database manager not available")

    db_manager = request.app.state.db_manager
    try:
        existing = db_manager.list_databases()
        if any(d["name"] == body.name for d in existing):
            raise HTTPException(status_code=409, detail=f"Database '{body.name}' already exists")
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
async def remove_database(name: str, request: Request):
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
    return Response(status_code=204)


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
async def browse_directory(path: str = "~"):
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
