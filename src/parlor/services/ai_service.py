"""OpenAI SDK wrapper for streaming chat completions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from ..config import AIConfig

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self, config: AIConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        system_msg = {"role": "system", "content": self.config.system_prompt}
        full_messages = [system_msg] + messages

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            stream = await self.client.chat.completions.create(**kwargs)

            current_tool_calls: dict[int, dict[str, Any]] = {}

            async for chunk in stream:
                if cancel_event and cancel_event.is_set():
                    await stream.close()
                    yield {"event": "done", "data": {}}
                    return

                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue

                delta = choice.delta

                if delta.content:
                    yield {"event": "token", "data": {"content": delta.content}}

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in current_tool_calls:
                            current_tool_calls[idx] = {
                                "id": tc.id or "",
                                "function_name": "",
                                "arguments": "",
                            }
                        if tc.id:
                            current_tool_calls[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            current_tool_calls[idx]["function_name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            current_tool_calls[idx]["arguments"] += tc.function.arguments

                if choice.finish_reason == "tool_calls":
                    for _idx, tc_data in sorted(current_tool_calls.items()):
                        try:
                            args = json.loads(tc_data["arguments"])
                        except json.JSONDecodeError:
                            args = {}
                        yield {
                            "event": "tool_call",
                            "data": {
                                "id": tc_data["id"],
                                "function_name": tc_data["function_name"],
                                "arguments": args,
                            },
                        }
                    return

                if choice.finish_reason == "stop":
                    yield {"event": "done", "data": {}}
                    return

        except Exception:
            logger.exception("AI stream error")
            yield {"event": "error", "data": {"message": "An internal error occurred"}}

    async def generate_title(self, user_message: str) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Generate a short title (3-6 words) for a conversation that starts"
                            " with the following message. Return only the title, no quotes or punctuation."
                        ),
                    },
                    {"role": "user", "content": user_message},
                ],
                max_tokens=20,
            )
            title = response.choices[0].message.content or "New Conversation"
            return title.strip().strip('"').strip("'")
        except Exception:
            return "New Conversation"

    async def validate_connection(self) -> tuple[bool, str, list[str]]:
        try:
            models = await self.client.models.list()
            model_ids = [m.id for m in models.data]
            return True, "Connected successfully", model_ids
        except Exception as e:
            return False, f"Connection failed: {e}", []
