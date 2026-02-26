"""Introspect tool — lets the AI examine its own runtime context.

The AI calls this tool to answer self-awareness questions like
"what instructions are you following?" or "why can't I run bash?"
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SENSITIVE_SUBSTRINGS = ("key", "secret", "password", "token", "passphrase")
_CHARS_PER_TOKEN = 4

DEFINITION: dict[str, Any] = {
    "name": "introspect",
    "description": (
        "Examine your own runtime context: loaded instructions, available tools, "
        "MCP servers, config settings, safety gates, skills, and token/context budget. "
        "Use this when the user asks about your setup, capabilities, context window usage, "
        "how much context is being used, remaining tokens, or why something is enabled/disabled. "
        "Use section='budget' for context window and token usage questions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": ["config", "instructions", "tools", "safety", "skills", "budget"],
                "description": (
                    "Which section to inspect. 'budget' shows context window usage and token counts. "
                    "Omit to get a summary of all sections."
                ),
            },
        },
        "required": [],
    },
}


def _redact(name: str, value: Any) -> Any:
    """Redact values for fields whose names suggest secrets."""
    name_lower = name.lower()
    if any(sub in name_lower for sub in _SENSITIVE_SUBSTRINGS):
        if isinstance(value, str) and value:
            return "****"
    return value


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _gather_config(config: Any) -> dict[str, Any]:
    """Gather config information with secret redaction."""
    result: dict[str, Any] = {}

    # AI config
    ai = config.ai
    ai_fields = {}
    for field_name in ("model", "base_url", "system_prompt", "verify_ssl", "request_timeout"):
        val = getattr(ai, field_name, None)
        if field_name == "system_prompt" and isinstance(val, str) and len(val) > 200:
            val = val[:200] + "... (truncated)"
        ai_fields[field_name] = _redact(field_name, val)
    # Always redact api_key
    ai_fields["api_key"] = "****" if getattr(ai, "api_key", None) else "(not set)"
    result["ai"] = ai_fields

    # App settings
    app = config.app
    result["app"] = {
        "host": app.host,
        "port": app.port,
        "data_dir": "~/.anteroom",
        "tls": app.tls,
    }

    # Config file paths
    config_files = []
    data_dir_path = app.data_dir
    if data_dir_path:
        from pathlib import Path

        personal = Path(data_dir_path) / "config.yaml"
        if personal.exists():
            config_files.append({"path": "~/.anteroom/config.yaml", "layer": "personal"})
    result["config_files"] = config_files

    return result


def _gather_instructions(instructions_info: dict[str, Any] | None) -> dict[str, Any]:
    """Gather loaded instruction information."""
    if not instructions_info:
        return {"loaded": False, "sources": []}

    return {
        "loaded": True,
        "sources": instructions_info.get("sources", []),
        "total_tokens": instructions_info.get("total_tokens", 0),
    }


def _gather_tools(
    tool_registry: Any | None,
    mcp_manager: Any | None,
    config: Any | None,
) -> dict[str, Any]:
    """Gather available tool information."""
    result: dict[str, Any] = {}

    # Built-in tools
    if tool_registry:
        builtin = tool_registry.list_tools()
        result["builtin"] = {"names": builtin, "count": len(builtin)}
    else:
        result["builtin"] = {"names": [], "count": 0}

    # MCP tools by server
    mcp_servers: list[dict[str, Any]] = []
    if mcp_manager:
        statuses = mcp_manager.get_server_statuses()
        all_tools = mcp_manager.get_all_tools()

        # Group tools by server
        tools_by_server: dict[str, list[str]] = {}
        for tool in all_tools:
            server = tool.get("server_name", "unknown")
            tools_by_server.setdefault(server, []).append(tool.get("name", "unknown"))

        for name, status in statuses.items():
            server_tools = tools_by_server.get(name, [])
            mcp_servers.append(
                {
                    "name": name,
                    "transport": status.get("transport", "unknown"),
                    "status": status.get("status", "unknown"),
                    "tool_count": status.get("tool_count", 0),
                    "total_tool_count": status.get("total_tool_count", status.get("tool_count", 0)),
                    "tools": server_tools,
                }
            )
    result["mcp_servers"] = mcp_servers

    # Denied/filtered tools
    if config and hasattr(config, "safety"):
        safety = config.safety
        result["denied_tools"] = list(safety.denied_tools) if safety.denied_tools else []
        result["allowed_tools"] = list(safety.allowed_tools) if safety.allowed_tools else []
    else:
        result["denied_tools"] = []
        result["allowed_tools"] = []

    return result


def _gather_safety(config: Any | None) -> dict[str, Any]:
    """Gather safety/approval settings."""
    if not config or not hasattr(config, "safety"):
        return {"available": False}

    safety = config.safety
    result: dict[str, Any] = {
        "available": True,
        "approval_mode": safety.approval_mode,
        "allowed_tools": list(safety.allowed_tools) if safety.allowed_tools else [],
        "denied_tools": list(safety.denied_tools) if safety.denied_tools else [],
        "custom_bash_patterns": len(safety.custom_patterns) if safety.custom_patterns else 0,
        "sensitive_paths": len(safety.sensitive_paths) if safety.sensitive_paths else 0,
    }

    # Tool tier overrides
    if safety.tool_tiers:
        result["tool_tier_overrides"] = dict(safety.tool_tiers)

    # Subagent limits
    if hasattr(safety, "subagent"):
        sa = safety.subagent
        result["subagent"] = {
            "max_concurrent": sa.max_concurrent,
            "max_total": sa.max_total,
            "max_depth": sa.max_depth,
            "max_iterations": sa.max_iterations,
            "timeout": sa.timeout,
        }

    return result


def _gather_skills(skill_registry: Any | None) -> dict[str, Any]:
    """Gather loaded skill information."""
    if not skill_registry:
        return {"available": False}

    skills = skill_registry.list_skills()
    by_source: dict[str, list[str]] = {}
    for skill in skills:
        source = getattr(skill, "source", "unknown")
        by_source.setdefault(source, []).append(skill.name)

    return {
        "available": True,
        "total": len(skills),
        "by_source": by_source,
    }


def _gather_budget(
    tools_openai: list[dict[str, Any]] | None,
    instructions_info: dict[str, Any] | None,
    config: Any | None,
) -> dict[str, Any]:
    """Estimate token budget consumed before conversation starts."""
    result: dict[str, Any] = {}

    # Tool definitions
    if tools_openai:
        tool_tokens = 0
        for tool in tools_openai:
            func = tool.get("function", {})
            serialized = json.dumps(func, separators=(",", ":"))
            tool_tokens += _estimate_tokens(serialized)
        result["tool_definitions"] = {
            "count": len(tools_openai),
            "estimated_tokens": tool_tokens,
        }
    else:
        result["tool_definitions"] = {"count": 0, "estimated_tokens": 0}

    # Instructions
    instruction_tokens = 0
    if instructions_info:
        instruction_tokens = instructions_info.get("total_tokens", 0)
    result["instructions"] = {"estimated_tokens": instruction_tokens}

    # System prompt
    system_prompt_tokens = 0
    if config and hasattr(config, "ai"):
        sp = getattr(config.ai, "system_prompt", "")
        if sp:
            system_prompt_tokens = _estimate_tokens(sp)
    result["system_prompt"] = {"estimated_tokens": system_prompt_tokens}

    # Total fixed cost
    total = result["tool_definitions"]["estimated_tokens"] + instruction_tokens + system_prompt_tokens
    result["total_fixed_tokens"] = total

    # Context window percentage
    context_window = 128000  # default
    if config and hasattr(config, "cli"):
        context_window = getattr(config.cli, "model_context_window", 128000) or 128000
    result["context_window"] = context_window
    result["percentage_used"] = round((total / context_window) * 100, 1) if context_window else 0

    return result


async def handle(
    section: str | None = None,
    _config: Any | None = None,
    _mcp_manager: Any | None = None,
    _tool_registry: Any | None = None,
    _skill_registry: Any | None = None,
    _instructions_info: dict[str, Any] | None = None,
    _tools_openai: list[dict[str, Any]] | None = None,
    _working_dir: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Inspect the current session's runtime context."""
    gatherers: dict[str, Any] = {
        "config": lambda: _gather_config(_config) if _config else {"available": False},
        "instructions": lambda: _gather_instructions(_instructions_info),
        "tools": lambda: _gather_tools(_tool_registry, _mcp_manager, _config),
        "safety": lambda: _gather_safety(_config),
        "skills": lambda: _gather_skills(_skill_registry),
        "budget": lambda: _gather_budget(_tools_openai, _instructions_info, _config),
    }

    if section:
        if section not in gatherers:
            return {"error": f"Unknown section: {section}. Valid: {', '.join(gatherers.keys())}"}
        return {
            "section": section,
            "working_dir": _working_dir,
            "data": gatherers[section](),
        }

    # Return summary of all sections
    result: dict[str, Any] = {"working_dir": _working_dir}
    for name, gatherer in gatherers.items():
        result[name] = gatherer()
    return result
