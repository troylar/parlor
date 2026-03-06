"""Built-in tool registry for the agentic CLI."""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from ..config import SafetyConfig
from ..services.rule_enforcer import RuleEnforcer
from ..services.tool_rate_limit import ToolRateLimiter
from .safety import SafetyVerdict, check_bash_command, check_write_path
from .security import check_hard_block
from .tiers import ToolTier as ToolTier
from .tiers import get_tool_tier, parse_approval_mode, should_require_approval

logger = logging.getLogger(__name__)

ToolHandler = Callable[..., Coroutine[Any, Any, dict[str, Any]]]
ConfirmCallback = Callable[[SafetyVerdict], Coroutine[Any, Any, bool]]

# Tools whose output contains external/filesystem content — tagged untrusted for prompt injection defense.
_UNTRUSTED_TOOLS = {
    "read_file",
    "grep",
    "glob_files",
    "bash",
    "write_file",
    "edit_file",
    "run_agent",
    "docx",
    "xlsx",
    "pptx",
}


class ToolRegistry:
    """Registry of built-in tools with OpenAI function-call format."""

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        self._definitions: dict[str, dict[str, Any]] = {}
        self._confirm_callback: ConfirmCallback | None = None
        self._safety_config: SafetyConfig | None = None
        self._working_dir: str | None = None
        self._session_allowed: set[str] = set()
        self._rate_limiter: ToolRateLimiter | None = None
        self._rule_enforcer: RuleEnforcer | None = None

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

    def set_rate_limiter(self, limiter: ToolRateLimiter | None) -> None:
        self._rate_limiter = limiter

    def set_rule_enforcer(self, enforcer: RuleEnforcer | None) -> None:
        self._rule_enforcer = enforcer

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
        # Hard rule enforcement runs unconditionally — even when safety is
        # disabled — so that ``enforce: hard`` pack rules cannot be bypassed.
        if self._rule_enforcer is not None:
            blocked, reason, rule_fqn = self._rule_enforcer.check_tool_call(tool_name, arguments)
            if blocked:
                return SafetyVerdict(
                    needs_approval=True,
                    reason=f"Blocked by rule {rule_fqn}: {reason}",
                    tool_name=tool_name,
                    hard_denied=True,
                )

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

        # Read-only mode: hard-deny any tool above READ tier as defense-in-depth.
        # The tool list is already filtered at assembly time, but this backstop
        # catches any tool call that bypasses the filtered list (e.g. via prompt
        # injection or a misbehaving model emitting unlisted tool calls).
        if config.read_only and tier != ToolTier.READ:
            return SafetyVerdict(
                needs_approval=True,
                reason=f"Tool '{tool_name}' blocked: read-only mode is active",
                tool_name=tool_name,
                hard_denied=True,
            )
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
                logger.warning("Tool hard-denied by config: %s — %s", name, verdict.reason)
                return {
                    "error": verdict.reason,
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

        # Rate limiting check (after safety, before execution)
        if self._rate_limiter:
            rl_verdict = self._rate_limiter.check(name)
            if rl_verdict and rl_verdict.exceeded:
                if self._rate_limiter.config.action == "block":
                    logger.warning("Tool rate-limited: %s — %s", name, rl_verdict.reason)
                    return {
                        "error": rl_verdict.reason,
                        "safety_blocked": True,
                        "rate_limited": True,
                        "_approval_decision": "rate_limited",
                    }
                logger.warning("Tool rate limit warning: %s — %s", name, rl_verdict.reason)

        extra_kwargs: dict[str, Any] = {}
        if user_approved_hard_block:
            extra_kwargs["_bypass_hard_block"] = True
        if name == "bash" and self._safety_config is not None:
            extra_kwargs["_sandbox_config"] = self._safety_config.bash
        result = await handler(**arguments, **extra_kwargs)
        result["_approval_decision"] = approval_decision
        result["_context_trust"] = "untrusted" if name in _UNTRUSTED_TOOLS else "trusted"
        if name in _UNTRUSTED_TOOLS:
            result["_context_origin"] = f"builtin:{name}"

        # Record the call for rate limiting
        if self._rate_limiter:
            self._rate_limiter.record_call(success="error" not in result)

        return result

    def list_tools(self) -> list[str]:
        return list(self._handlers.keys())


def cap_tools(
    tools: list[dict[str, Any]],
    builtin_names: set[str],
    limit: int = 128,
) -> list[dict[str, Any]]:
    """Cap the tools list to *limit*, prioritising built-in tools over MCP.

    Returns the (possibly truncated) list.  Logs a warning when tools are dropped.
    A *limit* of 0 means unlimited (no cap applied).
    """
    if limit <= 0 or len(tools) <= limit:
        return tools

    builtin: list[dict[str, Any]] = []
    mcp: list[dict[str, Any]] = []
    for t in tools:
        name = t.get("function", {}).get("name", "")
        if name in builtin_names:
            builtin.append(t)
        else:
            mcp.append(t)

    mcp.sort(key=lambda t: t.get("function", {}).get("name", ""))
    remaining = limit - len(builtin)
    if remaining < 0:
        remaining = 0
    kept_mcp = mcp[:remaining]
    dropped = mcp[remaining:]

    if dropped:
        names = [t.get("function", {}).get("name", "?") for t in dropped]
        logger.warning(
            "Tool limit (%d) exceeded — dropped %d MCP tool(s): %s",
            limit,
            len(dropped),
            ", ".join(names),
        )

    return builtin + kept_mcp


def register_default_tools(registry: ToolRegistry, working_dir: str | None = None) -> None:
    """Register all built-in tools."""
    from . import ask_user, bash, edit, glob_tool, grep, introspect, read, subagent, write
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
    registry.register(ask_user.DEFINITION["name"], ask_user.handle, ask_user.DEFINITION)
    registry.register(introspect.DEFINITION["name"], introspect.handle, introspect.DEFINITION)

    _register_optional_office_tools(registry, working_dir)


def _register_optional_office_tools(registry: ToolRegistry, working_dir: str | None = None) -> None:
    """Register office tools if their optional dependencies are installed."""
    import importlib

    for mod_name in ("office_docx", "office_xlsx", "office_pptx"):
        try:
            module = importlib.import_module(f".{mod_name}", package=__package__)
        except ImportError:
            continue
        if not getattr(module, "AVAILABLE", False):
            continue
        if working_dir and hasattr(module, "set_working_dir"):
            module.set_working_dir(working_dir)
        registry.register(module.DEFINITION["name"], module.handle, module.DEFINITION)
