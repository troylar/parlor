"""Shared agentic loop for web and CLI chat interfaces."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from .ai_service import AIService

logger = logging.getLogger(__name__)


@dataclass
class AgentEvent:
    kind: str
    data: dict[str, Any]


async def run_agent_loop(
    ai_service: AIService,
    messages: list[dict[str, Any]],
    tool_executor: Any,
    tools_openai: list[dict[str, Any]] | None,
    cancel_event: asyncio.Event | None = None,
    extra_system_prompt: str | None = None,
    max_iterations: int = 50,
) -> AsyncGenerator[AgentEvent, None]:
    """Run the agentic tool-call loop, yielding events.

    tool_executor must be an async callable: (tool_name, arguments) -> dict
    """
    iteration = 0
    while iteration < max_iterations:
        iteration += 1
        tool_calls_pending: list[dict[str, Any]] = []
        assistant_content = ""

        async for event in ai_service.stream_chat(
            messages,
            tools=tools_openai,
            cancel_event=cancel_event,
            extra_system_prompt=extra_system_prompt,
        ):
            etype = event["event"]
            if etype == "token":
                assistant_content += event["data"]["content"]
                yield AgentEvent(kind="token", data=event["data"])
            elif etype == "tool_call":
                tool_calls_pending.append(event["data"])
                yield AgentEvent(
                    kind="tool_call_start",
                    data={
                        "id": event["data"]["id"],
                        "tool_name": event["data"]["function_name"],
                        "arguments": event["data"]["arguments"],
                    },
                )
            elif etype == "error":
                yield AgentEvent(kind="error", data=event["data"])
                return
            elif etype == "done":
                break

        if not tool_calls_pending:
            if assistant_content:
                yield AgentEvent(kind="assistant_message", data={"content": assistant_content})
            yield AgentEvent(kind="done", data={})
            return

        # Save assistant message with tool calls into message history
        yield AgentEvent(kind="assistant_message", data={"content": assistant_content})
        messages.append(
            {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function_name"],
                            "arguments": json.dumps(tc["arguments"]),
                        },
                    }
                    for tc in tool_calls_pending
                ],
            }
        )

        # Execute each tool call
        for tc in tool_calls_pending:
            try:
                result = await tool_executor(tc["function_name"], tc["arguments"])
                yield AgentEvent(
                    kind="tool_call_end",
                    data={"id": tc["id"], "tool_name": tc["function_name"], "output": result, "status": "success"},
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result),
                    }
                )
            except Exception as e:
                error_result = {"error": str(e)}
                yield AgentEvent(
                    kind="tool_call_end",
                    data={"id": tc["id"], "tool_name": tc["function_name"], "output": error_result, "status": "error"},
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(error_result),
                    }
                )

        assistant_content = ""

    yield AgentEvent(kind="error", data={"message": f"Max iterations ({max_iterations}) reached"})
