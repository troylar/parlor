"""Sub-agent tool: spawns isolated child AI sessions for parallel execution."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any, Callable, Coroutine

from ..services.agent_loop import AgentEvent, run_agent_loop
from ..services.ai_service import AIService

logger = logging.getLogger(__name__)

MAX_SUBAGENT_DEPTH = 3
MAX_CONCURRENT_SUBAGENTS = 5
MAX_TOTAL_SUBAGENTS = 20
MAX_OUTPUT_CHARS = 4000
SUBAGENT_MAX_ITERATIONS = 25

EventSink = Callable[[str, AgentEvent], Coroutine[Any, Any, None]]

_SUBAGENT_SYSTEM_PROMPT = (
    "You are a sub-agent executing a specific task. Follow these rules strictly:\n"
    "- Complete the task described in the user message. Do not deviate.\n"
    "- You have access to file and shell tools. Use them to accomplish your task.\n"
    "- All safety policies apply. Do not attempt to circumvent security controls.\n"
    "- Do not execute destructive operations (rm -rf, DROP TABLE, etc.) unless explicitly instructed.\n"
    "- Keep your response concise and focused on results."
)

DEFINITION: dict[str, Any] = {
    "name": "run_agent",
    "description": (
        "Launch an autonomous sub-agent to handle a complex or independent task. "
        "The sub-agent runs its own AI session with access to tools (read, write, edit, bash, glob, grep) "
        "and returns a summary of its work. Use this to parallelize independent tasks — "
        "the parent AI can issue multiple run_agent calls simultaneously. "
        "Each sub-agent has its own conversation context and cannot see the parent's history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "A detailed, self-contained instruction for the sub-agent. "
                    "Include all necessary context since the sub-agent cannot see the parent conversation. "
                    "Be specific about what files to read, what to search for, or what to produce."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "Optional model override for this sub-agent (e.g. 'gpt-4o-mini' for fast tasks, "
                    "'gpt-4o' for complex reasoning). Defaults to the parent's model."
                ),
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
}


class SubagentLimiter:
    """Tracks concurrent and total sub-agent usage per root request."""

    def __init__(
        self,
        max_concurrent: int = MAX_CONCURRENT_SUBAGENTS,
        max_total: int = MAX_TOTAL_SUBAGENTS,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._total_spawned = 0
        self._max_total = max_total
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Try to acquire a slot. Returns False if total cap exceeded."""
        async with self._lock:
            if self._total_spawned >= self._max_total:
                return False
            self._total_spawned += 1
        await self._semaphore.acquire()
        return True

    def release(self) -> None:
        self._semaphore.release()

    @property
    def total_spawned(self) -> int:
        return self._total_spawned


async def handle(
    prompt: str,
    model: str | None = None,
    *,
    _ai_service: AIService | None = None,
    _tool_registry: Any | None = None,
    _cancel_event: Any | None = None,
    _depth: int = 0,
    _event_sink: EventSink | None = None,
    _agent_id: str = "",
    _limiter: SubagentLimiter | None = None,
) -> dict[str, Any]:
    """Execute a sub-agent with an isolated conversation context."""
    if _ai_service is None:
        return {"error": "Sub-agent requires AI service context"}

    if _depth >= MAX_SUBAGENT_DEPTH:
        return {"error": f"Maximum sub-agent depth ({MAX_SUBAGENT_DEPTH}) reached"}

    if _tool_registry is None:
        return {"error": "Sub-agent requires tool registry context"}

    # Enforce concurrency and total limits
    if _limiter is None:
        return {"error": "Sub-agent requires a limiter context"}

    acquired = await _limiter.acquire()
    if not acquired:
        return {
            "error": f"Maximum total sub-agents ({_limiter._max_total}) reached for this request. "
            "Reuse existing sub-agent results or reduce parallelism."
        }

    try:
        return await _run_subagent(
            prompt=prompt,
            model=model,
            _ai_service=_ai_service,
            _tool_registry=_tool_registry,
            _cancel_event=_cancel_event,
            _depth=_depth,
            _event_sink=_event_sink,
            _agent_id=_agent_id,
            _limiter=_limiter,
        )
    finally:
        _limiter.release()


async def _run_subagent(
    prompt: str,
    model: str | None,
    *,
    _ai_service: AIService,
    _tool_registry: Any,
    _cancel_event: Any | None,
    _depth: int,
    _event_sink: EventSink | None,
    _agent_id: str,
    _limiter: SubagentLimiter,
) -> dict[str, Any]:
    """Internal: run the sub-agent after limiter acquisition."""
    child_depth = _depth + 1
    start_time = time.monotonic()

    # Build child AI service with optional model override (deep copy for isolation)
    child_config = copy.deepcopy(_ai_service.config)
    if model:
        child_config.model = model

    child_ai = AIService(child_config, token_provider=_ai_service._token_provider)

    # Build child tool list — exclude run_agent at max depth
    child_tools = _tool_registry.get_openai_tools()
    if child_depth >= MAX_SUBAGENT_DEPTH:
        child_tools = [t for t in child_tools if t["function"]["name"] != "run_agent"]

    # Child tool executor wraps the registry, injecting depth and limiter for nested sub-agents
    _child_counter = 0

    async def child_tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal _child_counter
        if tool_name == "run_agent":
            _child_counter += 1
            arguments = dict(arguments)
            arguments["_ai_service"] = _ai_service
            arguments["_tool_registry"] = _tool_registry
            arguments["_cancel_event"] = _cancel_event
            arguments["_depth"] = child_depth
            arguments["_agent_id"] = f"{_agent_id}.{_child_counter}"
            arguments["_event_sink"] = _event_sink
            arguments["_limiter"] = _limiter
        return await _tool_registry.call_tool(tool_name, arguments)

    # Isolated message history for the child
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": prompt},
    ]

    # Notify parent of sub-agent start
    if _event_sink:
        await _event_sink(
            _agent_id,
            AgentEvent(
                kind="subagent_start",
                data={
                    "agent_id": _agent_id,
                    "prompt": prompt[:200],
                    "model": model or child_config.model,
                    "depth": child_depth,
                },
            ),
        )

    # Collect output
    output_parts: list[str] = []
    tool_calls_made: list[str] = []
    error_message: str | None = None

    try:
        async for event in run_agent_loop(
            ai_service=child_ai,
            messages=messages,
            tool_executor=child_tool_executor,
            tools_openai=child_tools,
            cancel_event=_cancel_event,
            extra_system_prompt=_SUBAGENT_SYSTEM_PROMPT,
            max_iterations=SUBAGENT_MAX_ITERATIONS,
        ):
            # Forward events to parent for UI rendering
            if _event_sink:
                await _event_sink(_agent_id, event)

            if event.kind == "token":
                content = event.data.get("content", "")
                if content:
                    output_parts.append(content)
            elif event.kind == "tool_call_start":
                tool_calls_made.append(event.data.get("tool_name", "unknown"))
            elif event.kind == "error":
                error_message = event.data.get("message", "Unknown error")

    except Exception:
        logger.exception("Sub-agent execution failed")
        error_message = "Sub-agent execution failed"

    elapsed = round(time.monotonic() - start_time, 1)
    output = "".join(output_parts)

    # Truncate if too long
    truncated = False
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n\n... [output truncated]"
        truncated = True

    # Notify parent of sub-agent completion
    if _event_sink:
        await _event_sink(
            _agent_id,
            AgentEvent(
                kind="subagent_end",
                data={
                    "agent_id": _agent_id,
                    "elapsed_seconds": elapsed,
                    "tool_calls": tool_calls_made,
                    "truncated": truncated,
                    "error": error_message,
                },
            ),
        )

    result: dict[str, Any] = {
        "output": output,
        "elapsed_seconds": elapsed,
        "tool_calls_made": tool_calls_made,
        "model_used": model or child_config.model,
    }
    if truncated:
        result["truncated"] = True
    if error_message:
        result["error"] = error_message

    return result
