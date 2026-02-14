"""Built-in tool registry for the agentic CLI."""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

ToolHandler = Callable[..., Coroutine[Any, Any, dict[str, Any]]]
ConfirmCallback = Callable[[str], Coroutine[Any, Any, bool]]

# Destructive command patterns that need confirmation
_DESTRUCTIVE_PATTERNS = (
    "rm ",
    "rm\t",
    "rmdir",
    "git push --force",
    "git push -f",
    "git reset --hard",
    "git clean",
    "git checkout .",
    "drop table",
    "drop database",
    "truncate ",
    "> /dev/",
    "chmod 777",
    "kill -9",
)


class ToolRegistry:
    """Registry of built-in tools with OpenAI function-call format."""

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        self._definitions: dict[str, dict[str, Any]] = {}
        self._confirm_callback: ConfirmCallback | None = None

    def set_confirm_callback(self, callback: ConfirmCallback) -> None:
        """Set callback for confirming destructive operations."""
        self._confirm_callback = callback

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

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if not handler:
            raise ValueError(f"Unknown built-in tool: {name}")

        # Check for destructive operations
        if self._confirm_callback and name == "bash":
            command = arguments.get("command", "")
            cmd_lower = command.lower().strip()
            for pattern in _DESTRUCTIVE_PATTERNS:
                if pattern in cmd_lower:
                    confirmed = await self._confirm_callback(f"Destructive command: {command}")
                    if not confirmed:
                        return {"error": "Command cancelled by user", "exit_code": -1}
                    break

        return await handler(**arguments)

    def list_tools(self) -> list[str]:
        return list(self._handlers.keys())


def register_default_tools(registry: ToolRegistry, working_dir: str | None = None) -> None:
    """Register all built-in tools."""
    from . import bash, edit, glob_tool, grep, read, write

    for module in [read, write, edit, bash, glob_tool, grep]:
        handler = module.handle
        defn = module.DEFINITION
        if working_dir and hasattr(module, "set_working_dir"):
            module.set_working_dir(working_dir)
        registry.register(defn["name"], handler, defn)
