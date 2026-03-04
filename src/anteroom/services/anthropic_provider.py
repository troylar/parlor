"""Anthropic SDK provider for streaming chat completions."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator

from ..config import AIConfig
from .egress_allowlist import check_egress_allowed
from .token_provider import TokenProvider, TokenProviderError

logger = logging.getLogger(__name__)

try:
    import anthropic
    from anthropic import (
        APIConnectionError as AnthropicConnectionError,
    )
    from anthropic import (
        APIStatusError as AnthropicStatusError,
    )
    from anthropic import (
        APITimeoutError as AnthropicTimeoutError,
    )
    from anthropic import (
        AuthenticationError as AnthropicAuthError,
    )
    from anthropic import (
        BadRequestError as AnthropicBadRequestError,
    )
    from anthropic import (
        RateLimitError as AnthropicRateLimitError,
    )

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


def _convert_tools(openai_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI function-calling tool format to Anthropic tool format."""
    result = []
    for tool in openai_tools:
        if tool.get("type") != "function":
            continue
        func = tool["function"]
        result.append(
            {
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return result


def _convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Convert OpenAI-format messages to Anthropic format.

    Returns (system_prompt, anthropic_messages).
    Extracts system messages into a single system prompt string.
    Converts tool call/result messages to Anthropic content block format.
    """
    system_parts: list[str] = []
    anthropic_msgs: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")

        if role == "system":
            system_parts.append(msg["content"])
            continue

        if role == "assistant":
            content_blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                content_blocks.append({"type": "text", "text": msg["content"]})
            for tc in msg.get("tool_calls", []):
                func = tc.get("function", {})
                args_str = func.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    args = {}
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": func.get("name", ""),
                        "input": args,
                    }
                )
            if content_blocks:
                anthropic_msgs.append({"role": "assistant", "content": content_blocks})
            continue

        if role == "tool":
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", ""),
            }
            if anthropic_msgs and anthropic_msgs[-1]["role"] == "user":
                existing = anthropic_msgs[-1]["content"]
                if isinstance(existing, list):
                    existing.append(tool_result_block)
                else:
                    anthropic_msgs[-1]["content"] = [tool_result_block]
            else:
                anthropic_msgs.append({"role": "user", "content": [tool_result_block]})
            continue

        if role == "user":
            anthropic_msgs.append({"role": "user", "content": msg.get("content", "")})
            continue

    return "\n\n".join(system_parts), anthropic_msgs


class AnthropicService:
    """Anthropic SDK wrapper matching the AIService interface."""

    def __init__(self, config: AIConfig, token_provider: TokenProvider | None = None) -> None:
        if not HAS_ANTHROPIC:
            raise ImportError(
                "The anthropic package is not installed. Install it with: pip install anteroom[anthropic]"
            )
        self.config = config
        self._token_provider = token_provider
        self._validate_egress()
        self._build_client()

    def _validate_egress(self) -> None:
        if not check_egress_allowed(
            self.config.base_url,
            self.config.allowed_domains,
            block_localhost=self.config.block_localhost_api,
        ):
            raise ValueError("Egress blocked: the configured base_url is not permitted by the egress allowlist.")

    def _build_client(self) -> None:
        api_key = self._resolve_api_key()
        base_url = self.config.base_url
        if base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/")[:-3]

        self.client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url if base_url != "https://api.anthropic.com" else None,
            timeout=float(self.config.request_timeout),
        )

    def _resolve_api_key(self) -> str:
        if self._token_provider:
            return self._token_provider.get_token()
        return self.config.api_key

    def _try_refresh_token(self) -> bool:
        if not self._token_provider:
            return False
        try:
            self._token_provider.refresh()
            self._build_client()
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
        _token_refreshed: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        system_content = self.config.system_prompt
        if extra_system_prompt:
            system_content = extra_system_prompt + "\n\n" + system_content

        full_messages = [{"role": "system", "content": system_content}] + messages
        system_prompt, anthropic_messages = _convert_messages(full_messages)

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": anthropic_messages,
            "max_tokens": self.config.max_output_tokens,
            "system": system_prompt,
        }
        if tools:
            kwargs["tools"] = _convert_tools(tools)
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        if self.config.top_p is not None:
            kwargs["top_p"] = self.config.top_p

        max_attempts = max(1, self.config.retry_max_attempts + 1)
        # Timeouts — reuse the same config fields as the OpenAI provider
        connect_timeout = float(self.config.request_timeout)
        chunk_stall_timeout = float(getattr(self.config, "chunk_stall_timeout", 45))

        for attempt in range(max_attempts):
            if cancel_event and cancel_event.is_set():
                return

            try:
                _attempt_start = time.monotonic()
                yield {"event": "phase", "data": {"phase": "connecting"}}

                # --- Cancel-aware stream creation with hard timeout ---
                stream_mgr = self.client.messages.stream(**kwargs)
                enter_coro = stream_mgr.__aenter__()
                enter_task = asyncio.ensure_future(enter_coro)
                wait_tasks: list[asyncio.Future[Any]] = [enter_task]

                if cancel_event:
                    cancel_wait = asyncio.ensure_future(cancel_event.wait())
                    wait_tasks.append(cancel_wait)
                else:
                    cancel_wait = None

                try:
                    done, _ = await asyncio.wait(
                        wait_tasks,
                        timeout=connect_timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except Exception:
                    enter_task.cancel()
                    if cancel_wait:
                        cancel_wait.cancel()
                    raise

                if not done:
                    enter_task.cancel()
                    if cancel_wait:
                        cancel_wait.cancel()
                    logger.warning(
                        "Anthropic stream() timed out after %ds (attempt %d/%d)",
                        connect_timeout,
                        attempt + 1,
                        max_attempts,
                    )
                    raise AnthropicTimeoutError.__new__(AnthropicTimeoutError)

                if cancel_wait and cancel_wait in done:
                    enter_task.cancel()
                    logger.info("Cancelled during Anthropic connecting phase")
                    return

                if cancel_wait:
                    cancel_wait.cancel()

                response = enter_task.result()
                logger.debug(
                    "anthropic_provider connected attempt=%d elapsed=%.2fs",
                    attempt + 1,
                    time.monotonic() - _attempt_start,
                )

                try:
                    yield {"event": "phase", "data": {"phase": "waiting"}}

                    current_tool_calls: dict[int, dict[str, Any]] = {}
                    tool_index = 0
                    usage_data: dict[str, Any] | None = None
                    _last_chunk_time = time.monotonic()

                    # --- Cancel-aware iteration with chunk stall detection ---
                    response_iter = response.__aiter__()
                    while True:
                        next_task = asyncio.ensure_future(response_iter.__anext__())
                        iter_wait: list[asyncio.Future[Any]] = [next_task]

                        if cancel_event:
                            iter_cancel = asyncio.ensure_future(cancel_event.wait())
                            iter_wait.append(iter_cancel)
                        else:
                            iter_cancel = None

                        try:
                            iter_done, _ = await asyncio.wait(
                                iter_wait,
                                timeout=chunk_stall_timeout,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                        except Exception:
                            next_task.cancel()
                            if iter_cancel:
                                iter_cancel.cancel()
                            raise

                        if not iter_done:
                            # Chunk stall timeout
                            next_task.cancel()
                            if iter_cancel:
                                iter_cancel.cancel()
                            logger.warning("Anthropic stream stalled — no chunk for %ds", chunk_stall_timeout)
                            yield {
                                "event": "error",
                                "data": {
                                    "message": "Stream stalled — no data received",
                                    "code": "stream_stall",
                                    "retryable": True,
                                },
                            }
                            return

                        if iter_cancel and iter_cancel in iter_done:
                            next_task.cancel()
                            return

                        if iter_cancel:
                            iter_cancel.cancel()

                        try:
                            event = next_task.result()
                        except StopAsyncIteration:
                            break

                        _last_chunk_time = time.monotonic()
                        event_type = event.type

                        if event_type == "message_start":
                            msg = getattr(event, "message", None)
                            if msg and hasattr(msg, "usage"):
                                usage_data = {
                                    "prompt_tokens": msg.usage.input_tokens,
                                    "completion_tokens": 0,
                                    "total_tokens": msg.usage.input_tokens,
                                    "model": self.config.model,
                                }

                        elif event_type == "content_block_start":
                            block = event.content_block
                            if block.type == "tool_use":
                                current_tool_calls[tool_index] = {
                                    "id": block.id,
                                    "function_name": block.name,
                                    "arguments": "",
                                }
                                tool_index += 1

                        elif event_type == "content_block_delta":
                            delta = event.delta
                            if delta.type == "text_delta":
                                yield {"event": "token", "data": {"content": delta.text}}
                            elif delta.type == "input_json_delta":
                                idx = tool_index - 1
                                if idx in current_tool_calls:
                                    current_tool_calls[idx]["arguments"] += delta.partial_json
                                    yield {
                                        "event": "tool_call_args_delta",
                                        "data": {
                                            "index": idx,
                                            "tool_name": current_tool_calls[idx]["function_name"],
                                            "delta": delta.partial_json,
                                        },
                                    }

                        elif event_type == "message_delta":
                            delta = event.delta
                            if hasattr(event, "usage") and event.usage:
                                output_tokens = event.usage.output_tokens
                                if usage_data:
                                    usage_data["completion_tokens"] = output_tokens
                                    usage_data["total_tokens"] = usage_data["prompt_tokens"] + output_tokens

                            stop_reason = getattr(delta, "stop_reason", None)
                            if stop_reason == "tool_use":
                                for _idx, tc_data in sorted(current_tool_calls.items()):
                                    try:
                                        args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
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
                                if usage_data:
                                    yield {"event": "usage", "data": usage_data}
                                return

                            if stop_reason == "end_turn":
                                if usage_data:
                                    yield {"event": "usage", "data": usage_data}
                                yield {"event": "done", "data": {}}
                                return

                    # Stream ended without explicit stop
                    if usage_data:
                        yield {"event": "usage", "data": usage_data}
                    yield {"event": "done", "data": {}}
                    return
                finally:
                    # Always close the stream context manager to release the HTTP connection
                    try:
                        await stream_mgr.__aexit__(None, None, None)
                    except Exception:
                        pass

            except AnthropicAuthError:
                if not _token_refreshed and self._try_refresh_token():
                    async for event in self.stream_chat(
                        messages, tools, cancel_event, extra_system_prompt, _token_refreshed=True
                    ):
                        yield event
                else:
                    yield {
                        "event": "error",
                        "data": {
                            "message": "Authentication failed. Check your API key.",
                            "code": "auth_failed",
                            "retryable": False,
                        },
                    }
                return
            except AnthropicBadRequestError as e:
                err_msg = str(e).lower()
                if "context" in err_msg or "too long" in err_msg or "max_tokens" in err_msg:
                    yield {
                        "event": "error",
                        "data": {
                            "message": "Conversation too long for model context window.",
                            "code": "context_length_exceeded",
                            "retryable": False,
                        },
                    }
                elif "tool" in err_msg and "too many" in err_msg:
                    yield {
                        "event": "error",
                        "data": {
                            "message": "Too many tools for this API provider.",
                            "code": "too_many_tools",
                            "retryable": False,
                        },
                    }
                else:
                    yield {"event": "error", "data": {"message": "AI request error", "retryable": False}}
                return
            except AnthropicRateLimitError:
                if cancel_event and cancel_event.is_set():
                    return
                yield {
                    "event": "error",
                    "data": {"message": "Rate limited by API provider", "code": "rate_limit", "retryable": True},
                }
                return
            except AnthropicStatusError as e:
                if e.status_code >= 500:
                    self._build_client()
                    if attempt < max_attempts - 1:
                        delay = self.config.retry_backoff_base * (2**attempt)
                        yield {
                            "event": "retrying",
                            "data": {
                                "attempt": attempt + 2,
                                "max_attempts": max_attempts,
                                "delay": delay,
                                "reason": "transient_error",
                            },
                        }
                        if cancel_event:
                            try:
                                await asyncio.wait_for(cancel_event.wait(), timeout=delay)
                                return
                            except asyncio.TimeoutError:
                                pass
                        else:
                            await asyncio.sleep(delay)
                        continue
                else:
                    if cancel_event and cancel_event.is_set():
                        return
                    yield {
                        "event": "error",
                        "data": {
                            "message": f"API error (HTTP {e.status_code})",
                            "code": "api_error",
                            "retryable": False,
                        },
                    }
                    return
            except (AnthropicTimeoutError, AnthropicConnectionError) as e:
                self._build_client()
                if attempt < max_attempts - 1:
                    delay = self.config.retry_backoff_base * (2**attempt)
                    logger.warning(
                        "Transient error (attempt %d/%d): %s. Retrying in %.1fs...",
                        attempt + 1,
                        max_attempts,
                        type(e).__name__,
                        delay,
                    )
                    yield {
                        "event": "retrying",
                        "data": {
                            "attempt": attempt + 2,
                            "max_attempts": max_attempts,
                            "delay": delay,
                            "reason": "transient_error",
                        },
                    }
                    if cancel_event:
                        try:
                            await asyncio.wait_for(cancel_event.wait(), timeout=delay)
                            return
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(delay)
                    continue
            except Exception:
                logger.exception("AI stream error")
                yield {"event": "error", "data": {"message": "An internal error occurred", "retryable": False}}
                return

        # All retries exhausted
        yield {
            "event": "error",
            "data": {
                "message": f"API request failed ({max_attempts} attempts)",
                "code": "timeout",
                "retryable": True,
            },
        }

    async def generate_title(self, user_message: str) -> str:
        try:
            response = await self.client.messages.create(
                model=self.config.model,
                max_tokens=20,
                system=(
                    "Generate a short title (3-6 words) for a conversation that starts"
                    " with the following message. Return only the title, no quotes or punctuation."
                ),
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text if response.content else "New Conversation"
            return text.strip().strip('"').strip("'")
        except Exception:
            logger.exception("Failed to generate title")
            return "New Conversation"

    async def validate_connection(self, _token_refreshed: bool = False) -> tuple[bool, str, list[str]]:
        try:
            await self.client.messages.create(
                model=self.config.model,
                max_tokens=5,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True, "Connected successfully", [self.config.model]
        except AnthropicAuthError:
            if not _token_refreshed and self._try_refresh_token():
                return await self.validate_connection(_token_refreshed=True)
            return False, "Authentication failed. Check your API key.", []
        except AnthropicTimeoutError:
            self._build_client()
            return False, "Connection timed out.", []
        except AnthropicConnectionError:
            self._build_client()
            return False, f"Cannot connect to API at {self.config.base_url}.", []
        except Exception:
            logger.exception("Connection validation failed")
            return False, "Connection validation failed.", []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        max_completion_tokens: int = 1000,
        _token_refreshed: bool = False,
    ) -> str | None:
        try:
            _, anthropic_messages = _convert_messages(messages)
            response = await self.client.messages.create(
                model=self.config.model,
                max_tokens=max_completion_tokens,
                messages=anthropic_messages,
            )
            return response.content[0].text if response.content else None
        except AnthropicAuthError:
            if not _token_refreshed and self._try_refresh_token():
                return await self.complete(messages, max_completion_tokens, _token_refreshed=True)
            return None
        except Exception:
            logger.exception("Failed to generate completion")
            return None
