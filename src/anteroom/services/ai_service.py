"""OpenAI SDK wrapper for streaming chat completions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

import httpx
from openai import APITimeoutError, AsyncOpenAI, AuthenticationError, BadRequestError, RateLimitError

from ..config import AIConfig
from .token_provider import TokenProvider, TokenProviderError

logger = logging.getLogger(__name__)


def create_ai_service(config: AIConfig) -> "AIService":
    """Factory: create an AIService with TokenProvider if api_key_command is configured."""
    provider = TokenProvider(config.api_key_command) if config.api_key_command else None
    return AIService(config, token_provider=provider)


class AIService:
    def __init__(self, config: AIConfig, token_provider: TokenProvider | None = None) -> None:
        self.config = config
        self._token_provider = token_provider
        self._build_client()

    def _build_client(self) -> None:
        """Build (or rebuild) the AsyncOpenAI client with the current API key."""
        api_key = self._resolve_api_key()
        timeout = httpx.Timeout(
            connect=10.0,
            read=float(self.config.request_timeout),
            write=30.0,
            pool=10.0,
        )
        # SECURITY-REVIEW: verify=False only when user explicitly sets verify_ssl: false in config
        new_http_client = httpx.AsyncClient(
            verify=self.config.verify_ssl,
            timeout=timeout,
        )
        self.client = AsyncOpenAI(
            base_url=self.config.base_url,
            api_key=api_key,
            http_client=new_http_client,
        )

    def _resolve_api_key(self) -> str:
        """Get API key from token provider (if set) or static config."""
        if self._token_provider:
            return self._token_provider.get_token()
        return self.config.api_key

    def _try_refresh_token(self) -> bool:
        """Attempt to refresh the token. Returns True if successful."""
        if not self._token_provider:
            return False
        try:
            self._token_provider.refresh()
            self._build_client()
            logger.info("Token refreshed and client rebuilt successfully")
            return True
        except TokenProviderError:
            logger.exception("Token refresh failed")
            return False

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        cancel_event: asyncio.Event | None = None,
        extra_system_prompt: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        system_content = self.config.system_prompt
        if extra_system_prompt:
            system_content = extra_system_prompt + "\n\n" + system_content
        system_msg = {"role": "system", "content": system_content}
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
                            yield {
                                "event": "tool_call_args_delta",
                                "data": {
                                    "index": idx,
                                    "tool_name": current_tool_calls[idx]["function_name"],
                                    "delta": tc.function.arguments,
                                },
                            }

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

        except AuthenticationError:
            if self._try_refresh_token():
                logger.info("Retrying request with refreshed token")
                async for event in self.stream_chat(messages, tools, cancel_event, extra_system_prompt):
                    yield event
            else:
                logger.error("Authentication failed and token refresh unavailable")
                yield {
                    "event": "error",
                    "data": {
                        "message": "Authentication failed. Check your API key or api_key_command.",
                        "code": "auth_failed",
                    },
                }
        except BadRequestError as e:
            body = getattr(e, "body", {}) or {}
            err_code = body.get("error", {}).get("code", "") if isinstance(body, dict) else ""
            if err_code == "context_length_exceeded" or "context_length" in str(e).lower():
                logger.warning("Context length exceeded: %s", e)
                yield {
                    "event": "error",
                    "data": {
                        "message": "Conversation too long for model context window.",
                        "code": "context_length_exceeded",
                    },
                }
            else:
                logger.exception("AI bad request error")
                yield {"event": "error", "data": {"message": f"AI request error: {e.message}"}}
        except APITimeoutError:
            logger.warning("AI request timed out after %ds", self.config.request_timeout)
            yield {
                "event": "error",
                "data": {
                    "message": (
                        f"AI request timed out after {self.config.request_timeout}s. "
                        "The API may be slow or unreachable. Try again, or increase "
                        "`ai.request_timeout` in your config."
                    ),
                    "code": "timeout",
                },
            }
        except RateLimitError as e:
            logger.warning("Rate limited by AI provider: %s", e)
            yield {
                "event": "error",
                "data": {
                    "message": "AI provider rate limit reached. Please wait a moment and try again.",
                    "code": "rate_limit",
                },
            }
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
                max_completion_tokens=20,
            )
            title = response.choices[0].message.content or "New Conversation"
            return title.strip().strip('"').strip("'")
        except AuthenticationError:
            if self._try_refresh_token():
                return await self.generate_title(user_message)
            logger.error("Authentication failed during title generation")
            return "New Conversation"
        except Exception:
            logger.exception("Failed to generate title")
            return "New Conversation"

    async def validate_connection(self) -> tuple[bool, str, list[str]]:
        try:
            models = await self.client.models.list()
            model_ids = [m.id for m in models.data]
            return True, "Connected successfully", model_ids
        except AuthenticationError:
            if self._try_refresh_token():
                return await self.validate_connection()
            logger.error("Authentication failed during connection validation")
            return False, "Authentication failed. Check your API key or api_key_command.", []
        except Exception as e:
            logger.error("AI connection validation failed: %s", e)
            return False, "Connection to AI service failed", []
