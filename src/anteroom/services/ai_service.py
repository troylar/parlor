"""OpenAI SDK wrapper for streaming chat completions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from ..config import AIConfig
from .token_provider import TokenProvider, TokenProviderError

logger = logging.getLogger(__name__)


class _FirstTokenTimeoutError(Exception):
    """Raised when the first token does not arrive within first_token_timeout."""


class _StreamTimeoutError(Exception):
    """Raised when the stream stalls mid-response after first token was received."""


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
        """Build (or rebuild) the AsyncOpenAI client with the current API key.

        Closes the old client's HTTP connection pool to prevent resource leaks.
        """
        old_client = getattr(self, "client", None)
        if old_client is not None:
            try:
                old_http = getattr(old_client, "_client", None)
                if old_http and hasattr(old_http, "close"):
                    # Schedule async close without blocking; best-effort cleanup
                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(old_http.close())
                        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
                    except RuntimeError:
                        pass  # No running loop (e.g. during __init__)
            except Exception:
                logger.debug("Failed to close old HTTP client", exc_info=True)

        api_key = self._resolve_api_key()
        timeout = httpx.Timeout(
            connect=float(self.config.connect_timeout),
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

    @staticmethod
    async def _iter_stream(
        stream_iter: Any,
        cancel_event: asyncio.Event | None,
        total_timeout: float,
    ) -> AsyncGenerator[Any, None]:
        """Iterate an async stream with cancel-awareness and a hard total timeout.

        Yields chunks from the stream. Stops if:
        - cancel_event is set (user pressed Escape / disconnected)
        - total_timeout seconds elapse since iteration started
        - the stream is exhausted (StopAsyncIteration)
        """
        deadline = asyncio.get_running_loop().time() + total_timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                logger.warning("Stream deadline exceeded before next chunk (%.0fs)", total_timeout)
                try:
                    await stream_iter.aclose()
                except Exception:
                    pass
                raise _StreamTimeoutError()

            next_chunk = asyncio.ensure_future(stream_iter.__anext__())
            wait_tasks: list[asyncio.Future[Any]] = [next_chunk]

            if cancel_event:
                cancel_wait = asyncio.ensure_future(cancel_event.wait())
                wait_tasks.append(cancel_wait)
            else:
                cancel_wait = None

            try:
                done, _pending = await asyncio.wait(
                    wait_tasks,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except Exception:
                next_chunk.cancel()
                if cancel_wait:
                    cancel_wait.cancel()
                raise

            if not done:
                # Timeout with no completion
                next_chunk.cancel()
                if cancel_wait:
                    cancel_wait.cancel()
                logger.warning("Stream wait timed out after %.0fs total", total_timeout)
                try:
                    await stream_iter.aclose()
                except Exception:
                    pass
                raise _StreamTimeoutError()

            # Cancel was triggered
            if cancel_wait and cancel_wait in done:
                next_chunk.cancel()
                try:
                    await stream_iter.aclose()
                except Exception:
                    pass
                return

            # Stream produced a chunk (or ended)
            if cancel_wait and cancel_wait not in done:
                cancel_wait.cancel()

            try:
                chunk = next_chunk.result()
            except StopAsyncIteration:
                if cancel_wait:
                    cancel_wait.cancel()
                return

            yield chunk

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

        max_attempts = max(1, self.config.retry_max_attempts + 1)  # +1: first attempt is not a "retry"
        last_transient_error: Exception | None = None

        for attempt in range(max_attempts):
            try:
                yield {"event": "phase", "data": {"phase": "connecting"}}
                stream = await self.client.chat.completions.create(**kwargs)
                yield {"event": "phase", "data": {"phase": "waiting"}}

                # --- First-token timeout ---
                stream_iter = stream.__aiter__()
                first_chunk: Any = None
                try:
                    first_chunk = await asyncio.wait_for(
                        stream_iter.__anext__(),
                        timeout=float(self.config.first_token_timeout),
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "No first token within %ds (attempt %d/%d)",
                        self.config.first_token_timeout,
                        attempt + 1,
                        max_attempts,
                    )
                    try:
                        if hasattr(stream, "close"):
                            await stream.close()
                    except Exception:
                        pass
                    raise _FirstTokenTimeoutError()

                # --- Stream with full request_timeout (first chunk already received) ---
                current_tool_calls: dict[int, dict[str, Any]] = {}
                total_timeout = float(self.config.request_timeout)

                async def _prepended_stream() -> AsyncGenerator[Any, None]:
                    """Yield the first chunk, then remaining chunks via _iter_stream."""
                    yield first_chunk
                    async for c in AIService._iter_stream(stream_iter, cancel_event, total_timeout):
                        yield c

                try:
                    async for chunk in _prepended_stream():
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
                finally:
                    if hasattr(stream, "close"):
                        await stream.close()

                # If we get here without returning, the stream ended without finish_reason
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
                return
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
                return
            except RateLimitError as e:
                logger.warning("Rate limited by AI provider: %s", e)
                yield {
                    "event": "error",
                    "data": {
                        "message": "AI provider rate limit reached. Please wait a moment and try again.",
                        "code": "rate_limit",
                    },
                }
                return
            except _StreamTimeoutError:
                logger.warning("Stream timed out mid-response after first token")
                self._build_client()
                yield {
                    "event": "error",
                    "data": {
                        "message": (
                            f"AI response timed out after {self.config.request_timeout}s. "
                            "The API may be slow or unreachable. Try again, or increase "
                            "`ai.request_timeout` in your config."
                        ),
                        "code": "timeout",
                    },
                }
                return
            except (APITimeoutError, APIConnectionError, _FirstTokenTimeoutError) as e:
                last_transient_error = e
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
                            "attempt": attempt + 2,  # next attempt number (1-indexed)
                            "max_attempts": max_attempts,
                            "delay": delay,
                            "reason": type(e).__name__,
                        },
                    }
                    # Sleep with cancel awareness
                    if cancel_event:
                        try:
                            await asyncio.wait_for(cancel_event.wait(), timeout=delay)
                            return  # cancelled during retry wait
                        except asyncio.TimeoutError:
                            pass  # delay elapsed, proceed with retry
                    else:
                        await asyncio.sleep(delay)
                    continue
                # Last attempt exhausted — fall through to yield error
            except Exception:
                logger.exception("AI stream error")
                yield {"event": "error", "data": {"message": "An internal error occurred"}}
                return

        # All retries exhausted — yield appropriate error for the last transient error
        if isinstance(last_transient_error, APITimeoutError):
            yield {
                "event": "error",
                "data": {
                    "message": (
                        f"AI request timed out after {max_attempts} attempts. "
                        "The API may be slow or unreachable. Try again, or increase "
                        "`ai.request_timeout` in your config."
                    ),
                    "code": "timeout",
                },
            }
        elif isinstance(last_transient_error, _FirstTokenTimeoutError):
            yield {
                "event": "error",
                "data": {
                    "message": (
                        f"No response from API within {self.config.first_token_timeout}s "
                        f"after {max_attempts} attempts. The API may be overloaded. "
                        "Try again, or increase `ai.first_token_timeout` in your config."
                    ),
                    "code": "timeout",
                },
            }
        elif isinstance(last_transient_error, APIConnectionError):
            yield {
                "event": "error",
                "data": {
                    "message": (
                        f"Cannot connect to API at {self.config.base_url} "
                        f"after {max_attempts} attempts. Check the URL and your network connection."
                    ),
                    "code": "connection_error",
                },
            }

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
        except APITimeoutError:
            logger.warning("Title generation timed out")
            self._build_client()
            return "New Conversation"
        except APIConnectionError:
            logger.warning("Cannot connect to API at %s during title generation", self.config.base_url)
            self._build_client()
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
        except APITimeoutError:
            logger.warning("Connection validation timed out")
            self._build_client()
            return False, "Connection timed out. The API may be slow or unreachable.", []
        except APIConnectionError:
            logger.warning("Cannot connect to API at %s", self.config.base_url)
            self._build_client()
            return (
                False,
                f"Cannot connect to API at {self.config.base_url}. Check the URL and your network connection.",
                [],
            )
        except Exception as e:
            logger.error("AI connection validation failed: %s", e)
            return False, "Connection to AI service failed", []
