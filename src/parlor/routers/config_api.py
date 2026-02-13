"""Config and MCP tools endpoints."""

from __future__ import annotations

import logging
from typing import Any

import yaml
from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..models import AppConfigResponse, ConnectionValidation, McpServerStatus, McpTool
from ..services.ai_service import AIService

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
                )
            )

    return AppConfigResponse(
        ai={
            "base_url": config.ai.base_url,
            "api_key_set": bool(config.ai.api_key),
            "model": config.ai.model,
            "system_prompt": config.ai.system_prompt,
        },
        mcp_servers=mcp_statuses,
    )


@router.patch("/config")
async def update_config(body: ConfigUpdate, request: Request):
    config = request.app.state.config
    changed = False

    if body.model is not None and body.model != config.ai.model:
        config.ai.model = body.model
        changed = True
    if body.system_prompt is not None and body.system_prompt != config.ai.system_prompt:
        config.ai.system_prompt = body.system_prompt
        changed = True

    if changed:
        _persist_config(config)

    return {
        "model": config.ai.model,
        "system_prompt": config.ai.system_prompt,
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
        raw["ai"]["system_prompt"] = config.ai.system_prompt

        with open(config_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
    except Exception:
        logger.exception("Failed to persist config to %s", config_path)


@router.post("/config/validate")
async def validate_connection(request: Request) -> ConnectionValidation:
    config = request.app.state.config
    ai_service = AIService(config.ai)
    valid, message, models = await ai_service.validate_connection()
    return ConnectionValidation(valid=valid, message=message, models=models)


@router.get("/mcp/tools")
async def list_mcp_tools(request: Request) -> list[McpTool]:
    mcp_manager = request.app.state.mcp_manager
    if not mcp_manager:
        return []
    return [
        McpTool(
            name=tool["name"],
            server_name=tool["server_name"],
            description=tool["description"],
            input_schema=tool["input_schema"],
        )
        for tool in mcp_manager.get_all_tools()
    ]
