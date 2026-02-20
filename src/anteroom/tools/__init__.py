"""Built-in tool registry for the agentic CLI."""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from ..config import SafetyConfig
from .safety import SafetyVerdict, check_bash_command, check_write_path
from .security import check_hard_block
from .tiers import ToolTier as ToolTier
from .tiers import get_tool_tier, parse_approval_mode, should_require_approval

logger = logging.getLogger(__name__)

ToolHandler = Callable[..., Coroutine[Any, Any, dict[str, Any]]]
ConfirmCallback = Callable[[SafetyVerdict], Coroutine[Any, Any, bool]]


class ToolRegistry:
    """Registry of built-in tools with OpenAI function-call format."""

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        self._definitions: dict[str, dict[str, Any]] = {}
        self._confirm_callback: ConfirmCallback | None = None
        self._safety_config: SafetyConfig | None = None
        self._working_dir: str | None = None
        self._session_allowed: set[str] = set()

    def set_confirm_callback(self, callback: ConfirmCallback | None) -> None:
        self._confirm_callback = callback

    def set_safety_config(self, config: SafetyConfig, working_dir: str | None = None) -> None:
        self._safety_config = config
        self._working_dir = working_dir

    def grant_session_permission(self, tool_name: str) -> None:
        import re

        if not re.match(r"^[a-zA-Z0-9_\-]{1,128}$", tool_name):
            logger.warning("Rejected invalid tool name for session permission: %r", tool_name)
            return
        self._session_allowed.add(tool_name)

    def clear_session_permissions(self) -> None:
        self._session_allowed.clear()

    def register(self, name: str, handler: ToolHandler, definition: dict[str, Any]) -> None:
        self._handlers[name] = handler
        self._definitions[name] = definition

    def has_tool(self, name: str) -> bool:
        return name in self._handlers

    def get_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": defn.get("description", ""),
                    "parameters": defn.get("parameters", {}),
                },
            }
            for name, defn in self._definitions.items()
        ]

    def check_safety(self, tool_name: str, arguments: dict[str, Any]) -> SafetyVerdict | None:
        """Check whether a tool call requires approval.

        Returns a SafetyVerdict if approval is needed/denied, or None if auto-allowed.
        A verdict with hard_denied=True means the tool is blocked by config (denied_tools
        or per-tool enabled=false) and must be blocked without prompting.
        """
        config = self._safety_config
        if not config or not config.enabled:
            return None

        # Per-tool enabled toggle: when false, hard-deny the tool entirely.
        if tool_name == "bash" and not config.bash.enabled:
            return SafetyVerdict(
                needs_approval=True,
                reason=f"Tool '{tool_name}' is disabled in safety config",
                tool_name=tool_name,
                hard_denied=True,
            )
        if tool_name == "write_file" and not config.write_file.enabled:
            return SafetyVerdict(
                needs_approval=True,
                reason=f"Tool '{tool_name}' is disabled in safety config",
                tool_name=tool_name,
                hard_denied=True,
            )

        tier = get_tool_tier(tool_name, tier_overrides=config.tool_tiers)
        mode = parse_approval_mode(config.approval_mode)

        result = should_require_approval(
            tool_name=tool_name,
            tool_tier=tier,
            mode=mode,
            allowed_tools=set(config.allowed_tools) if config.allowed_tools else None,
            denied_tools=set(config.denied_tools) if config.denied_tools else None,
            session_allowed=self._session_allowed or None,
        )

        if result is None:
            return SafetyVerdict(
                needs_approval=True,
                reason=f"Tool '{tool_name}' is in the denied tools list",
                tool_name=tool_name,
                hard_denied=True,
            )

        # Check tool-specific destructive patterns even when tier says auto-allow,
        # but NOT in auto mode (auto mode bypasses everything).
        from .tiers import ApprovalMode

        if result is False and mode != ApprovalMode.AUTO:
            if tool_name == "bash":
                verdict = check_bash_command(arguments.get("command", ""), custom_patterns=config.custom_patterns)
                if verdict.needs_approval:
                    return self._enrich_with_hard_block(verdict, arguments)
            if tool_name == "write_file":
                verdict = check_write_path(
                    arguments.get("path", ""), self._working_dir or ".", sensitive_paths=config.sensitive_paths
                )
                if verdict.needs_approval:
                    return verdict
            return None

        if result is False:
            return None

        # Tier-based approval required — return generic verdict for the tool
        if tool_name == "bash":
            verdict = SafetyVerdict(
                needs_approval=True,
                reason=f"Tool '{tool_name}' requires approval (mode: {config.approval_mode})",
                tool_name=tool_name,
                details={"command": arguments.get("command", "")},
            )
            return self._enrich_with_hard_block(verdict, arguments)

        if tool_name == "write_file":
            return SafetyVerdict(
                needs_approval=True,
                reason=f"Tool '{tool_name}' requires approval (mode: {config.approval_mode})",
                tool_name=tool_name,
                details={"path": arguments.get("path", "")},
            )

        # Generic approval for other tools (edit_file, MCP tools, etc.)
        return SafetyVerdict(
            needs_approval=True,
            reason=f"Tool '{tool_name}' requires approval (mode: {config.approval_mode})",
            tool_name=tool_name,
            details={},
        )

    @staticmethod
    def _enrich_with_hard_block(verdict: SafetyVerdict, arguments: dict[str, Any]) -> SafetyVerdict:
        """Check if a bash command matches a hard-block pattern and enrich the verdict."""
        command = arguments.get("command", "")
        description = check_hard_block(command)
        if description:
            verdict.is_hard_blocked = True
            verdict.hard_block_description = description
            verdict.reason = f"DESTRUCTIVE command ({description}): {command}"
        return verdict

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        confirm_callback: ConfirmCallback | None = None,
    ) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if not handler:
            raise ValueError(f"Unknown built-in tool: {name}")

        verdict = self.check_safety(name, arguments)
        approval_decision = "auto"
        user_approved_hard_block = False
        if verdict and verdict.needs_approval:
            if verdict.hard_denied:
                logger.warning("Tool hard-denied by config: %s", name)
                return {
                    "error": f"Tool '{name}' is blocked by configuration",
                    "safety_blocked": True,
                    "_approval_decision": "hard_denied",
                }
            # Hard-blocked commands with no approval channel: block silently
            # (safety net for auto mode / unattended agents).
            callback = confirm_callback or self._confirm_callback
            if callback is None:
                if verdict.is_hard_blocked:
                    logger.info("Hard-block safety net (no approval channel): %s", verdict.hard_block_description)
                else:
                    logger.warning("Safety gate blocked (no approval channel): %s", verdict.reason)
                return {
                    "error": "Operation blocked: no approval channel available",
                    "safety_blocked": True,
                    "_approval_decision": "denied",
                }
            confirmed = await callback(verdict)
            if not confirmed:
                return {"error": "Operation denied by user", "exit_code": -1, "_approval_decision": "denied"}
            approval_decision = "allowed_once"
            if verdict.is_hard_blocked:
                user_approved_hard_block = True

        extra_kwargs: dict[str, Any] = {}
        if user_approved_hard_block:
            extra_kwargs["_bypass_hard_block"] = True
        result = await handler(**arguments, **extra_kwargs)
        result["_approval_decision"] = approval_decision
        return result

    def list_tools(self) -> list[str]:
        return list(self._handlers.keys())


def register_default_tools(registry: ToolRegistry, working_dir: str | None = None) -> None:
    """Register all built-in tools."""
    from . import bash, edit, glob_tool, grep, read, subagent, write
    from .canvas import (
        CANVAS_CREATE_DEFINITION,
        CANVAS_PATCH_DEFINITION,
        CANVAS_UPDATE_DEFINITION,
        handle_create_canvas,
        handle_patch_canvas,
        handle_update_canvas,
    )

    for module in [read, write, edit, bash, glob_tool, grep]:
        handler = module.handle
        defn = module.DEFINITION
        if working_dir and hasattr(module, "set_working_dir"):
            module.set_working_dir(working_dir)
        registry.register(defn["name"], handler, defn)

    registry.register(CANVAS_CREATE_DEFINITION["name"], handle_create_canvas, CANVAS_CREATE_DEFINITION)
    registry.register(CANVAS_UPDATE_DEFINITION["name"], handle_update_canvas, CANVAS_UPDATE_DEFINITION)
    registry.register(CANVAS_PATCH_DEFINITION["name"], handle_patch_canvas, CANVAS_PATCH_DEFINITION)
    registry.register(subagent.DEFINITION["name"], subagent.handle, subagent.DEFINITION)
