"""LiteLLM provider for multi-provider streaming chat completions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

from ..config import AIConfig
from .egress_allowlist import check_egress_allowed
from .error_sanitizer import sanitize_provider_error
from .token_provider import TokenProvider, TokenProviderError

logger = logging.getLogger(__name__)

try:
    import litellm
    from litellm.exceptions import AuthenticationError as LiteLLMAuthError
    from litellm.exceptions import BadRequestError as LiteLLMBadRequestError
    from litellm.exceptions import ContextWindowExceededError as LiteLLMContextError
    from litellm.exceptions import RateLimitError as LiteLLMRateLimitError

    HAS_LITELLM = True
except ImportError:
    HAS_LITELLM = False


class LiteLLMService:
    """LiteLLM wrapper matching the AIService interface.

    Uses litellm.acompletion() for streaming, supporting 100+ providers
    via model name prefixes (e.g. openrouter/openai/gpt-4o).
    """

    def __init__(self, config: AIConfig, token_provider: TokenProvider | None = None) -> None:
        if not HAS_LITELLM:
            raise ImportError("The litellm package is not installed. Install it with: pip install anteroom[providers]")
        self.config = config
        self._token_provider = token_provider
        self._validate_egress()

    def _validate_egress(self) -> None:
        if self.config.base_url and not check_egress_allowed(
            self.config.base_url,
            self.config.allowed_domains,
            block_localhost=self.config.block_localhost_api,
        ):
            raise ValueError("Egress blocked: the configured base_url is not permitted by the egress allowlist.")

    def _resolve_api_key(self) -> str:
        if self._token_provider:
            return self._token_provider.get_token()
        return self.config.api_key

    def _try_refresh_token(self) -> bool:
        if not self._token_provider:
            return False
        try:
            self._token_provider.refresh()
            return True
        except TokenProviderError:
            logger.exception("Token refresh failed")
            return False

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        *,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        max_completion_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Build kwargs dict for litellm.acompletion()."""
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": stream,
            "timeout": float(self.config.request_timeout),
        }
        # Only pass api_key when explicitly configured. Omitting it lets
        # LiteLLM fall through to provider-native auth (e.g. boto3 for
        # Bedrock, GCP ADC for Vertex AI).
        api_key = self._resolve_api_key()
        if api_key:
            kwargs["api_key"] = api_key
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        if tools:
            kwargs["tools"] = tools
        if max_completion_tokens is not None:
            kwargs["max_completion_tokens"] = max_completion_tokens
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        if self.config.top_p is not None:
            kwargs["top_p"] = self.config.top_p
        if self.config.seed is not None:
            kwargs["seed"] = self.config.seed
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        # OpenRouter-specific headers
        if "openrouter" in self.config.model.lower() or "openrouter" in (self.config.base_url or "").lower():
            kwargs["extra_headers"] = {
                "HTTP-Referer": "https://anteroom.ai",
                "X-Title": "Anteroom",
            }
        return kwargs

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        cancel_event: asyncio.Event | None = None,
        extra_system_prompt: str | None = None,
        *,
        _retry_on_auth: bool = True,
    ) -> AsyncGenerator[dict[str, Any], None]:
        system_content = self.config.system_prompt
        if extra_system_prompt:
            system_content = extra_system_prompt + "\n\n" + system_content
        system_msg = {"role": "system", "content": system_content}
        full_messages = [system_msg] + messages

        max_attempts = max(1, self.config.retry_max_attempts + 1)
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            if cancel_event and cancel_event.is_set():
                return

            # Rebuild kwargs each attempt so api_key reflects any token refresh
            kwargs = self._build_kwargs(full_messages, stream=True, tools=tools)

            try:
                yield {"event": "phase", "data": {"phase": "connecting"}}

                stream = await litellm.acompletion(**kwargs)

                yield {"event": "phase", "data": {"phase": "waiting"}}

                current_tool_calls: dict[int, dict[str, Any]] = {}
                usage_data: dict[str, Any] | None = None

                async for chunk in stream:
                    if cancel_event and cancel_event.is_set():
                        return

                    if hasattr(chunk, "usage") and chunk.usage is not None:
                        usage_data = {
                            "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                            "completion_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                            "total_tokens": getattr(chunk.usage, "total_tokens", 0) or 0,
                            "model": self.config.model,
                        }

                    choice = chunk.choices[0] if chunk.choices else None
                    if not choice:
                        continue

                    delta = choice.delta

                    if hasattr(delta, "content") and delta.content:
                        yield {"event": "token", "data": {"content": delta.content}}

                    if hasattr(delta, "tool_calls") and delta.tool_calls:
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
                        if usage_data:
                            yield {"event": "usage", "data": usage_data}
                        return

                    if choice.finish_reason == "stop":
                        if usage_data:
                            yield {"event": "usage", "data": usage_data}
                        yield {"event": "done", "data": {}}
                        return

                # Stream ended without explicit finish_reason
                if usage_data:
                    yield {"event": "usage", "data": usage_data}
                yield {"event": "done", "data": {}}
                return

            except LiteLLMAuthError:
                if _retry_on_auth and self._try_refresh_token():
                    async for event in self.stream_chat(
                        messages, tools, cancel_event, extra_system_prompt, _retry_on_auth=False
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

            except LiteLLMContextError:
                yield {
                    "event": "error",
                    "data": {
                        "message": "Conversation too long for model context window.",
                        "code": "context_length_exceeded",
                        "retryable": False,
                    },
                }
                return

            except LiteLLMRateLimitError:
                if cancel_event and cancel_event.is_set():
                    return
                yield {
                    "event": "error",
                    "data": {
                        "message": "Rate limited by API provider",
                        "code": "rate_limit",
                        "retryable": True,
                    },
                }
                return

            except LiteLLMBadRequestError as e:
                err_msg = str(e).lower()
                if "too many" in err_msg and "tool" in err_msg:
                    yield {
                        "event": "error",
                        "data": {
                            "message": (
                                "Too many tools for this API provider. Reduce MCP tools or set ai.max_tools in config."
                            ),
                            "code": "too_many_tools",
                            "retryable": False,
                        },
                    }
                else:
                    user_msg = sanitize_provider_error(str(e))
                    logger.warning("AI bad request error: %s", e)
                    yield {
                        "event": "error",
                        "data": {
                            "message": user_msg,
                            "code": "bad_request",
                            "retryable": False,
                        },
                    }
                return

            except Exception as e:
                last_error = e
                # Transient errors — retry
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

        # All retries exhausted
        logger.error("All %d attempts failed: %s", max_attempts, type(last_error).__name__)
        yield {
            "event": "error",
            "data": {
                "message": f"API request failed after {max_attempts} attempts",
                "code": "timeout",
                "retryable": True,
            },
        }

    async def generate_title(self, user_message: str) -> str:
        try:
            kwargs = self._build_kwargs(
                [
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
            response = await litellm.acompletion(**kwargs)
            title = response.choices[0].message.content or "New Conversation"
            return title.strip().strip('"').strip("'")
        except Exception:
            logger.exception("Failed to generate title")
            return "New Conversation"

    async def validate_connection(self) -> tuple[bool, str, list[str]]:
        try:
            kwargs = self._build_kwargs(
                [{"role": "user", "content": "Hi"}],
                max_completion_tokens=5,
            )
            response = await litellm.acompletion(**kwargs)
            if response.choices:
                return True, "Connected successfully", [self.config.model]
            return False, "No response from API", []
        except Exception:
            logger.exception("Connection validation failed")
            return False, "Connection failed", []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        max_completion_tokens: int = 1000,
    ) -> str | None:
        try:
            kwargs = self._build_kwargs(messages, max_completion_tokens=max_completion_tokens)
            response = await litellm.acompletion(**kwargs)
            return response.choices[0].message.content if response.choices else None
        except Exception:
            logger.exception("Failed to generate completion")
            return None
