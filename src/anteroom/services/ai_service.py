"""OpenAI SDK wrapper for streaming chat completions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
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
            write=float(self.config.write_timeout),
            pool=float(self.config.pool_timeout),
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
        stall_timeout: float | None = None,
    ) -> AsyncGenerator[Any, None]:
        """Iterate an async stream with cancel-awareness and a hard total timeout.

        Yields chunks from the stream. Stops if:
        - cancel_event is set (user pressed Escape / disconnected)
        - total_timeout seconds elapse since iteration started
        - stall_timeout seconds elapse with no chunk (mid-stream silence)
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

            wait_limit = min(remaining, stall_timeout) if stall_timeout else remaining

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
                    timeout=wait_limit,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except Exception:
                next_chunk.cancel()
                if cancel_wait:
                    cancel_wait.cancel()
                raise

            if not done:
                # Timeout with no completion — stall or total deadline
                next_chunk.cancel()
                if cancel_wait:
                    cancel_wait.cancel()
                remaining_now = deadline - asyncio.get_running_loop().time()
                if remaining_now <= 0:
                    logger.warning("Stream total deadline exceeded after %.0fs", total_timeout)
                else:
                    logger.warning("Stream stalled — no chunk for %.0fs (%.0fs remaining)", wait_limit, remaining_now)
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
            # Check cancel before (re-)entering create() — avoids blocking on a stale cancel
            if cancel_event and cancel_event.is_set():
                return

            try:
                yield {"event": "phase", "data": {"phase": "connecting"}}

                # --- Cancel-aware create() with hard timeout ---
                # The bare `await create()` is not interruptible by cancel_event and
                # httpx per-read timeouts can reset, so we race the create task against
                # cancel_event and a hard request_timeout deadline.
                create_coro = self.client.chat.completions.create(**kwargs)
                create_task = asyncio.ensure_future(create_coro)
                wait_tasks: list[asyncio.Future[Any]] = [create_task]

                if cancel_event:
                    cancel_wait = asyncio.ensure_future(cancel_event.wait())
                    wait_tasks.append(cancel_wait)
                else:
                    cancel_wait = None

                try:
                    done, _pending = await asyncio.wait(
                        wait_tasks,
                        timeout=float(self.config.request_timeout),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except Exception:
                    create_task.cancel()
                    if cancel_wait:
                        cancel_wait.cancel()
                    raise

                if not done:
                    # Hard timeout exceeded — create() never returned
                    create_task.cancel()
                    if cancel_wait:
                        cancel_wait.cancel()
                    logger.warning(
                        "API create() timed out after %ds (attempt %d/%d)",
                        self.config.request_timeout,
                        attempt + 1,
                        max_attempts,
                    )
                    raise _FirstTokenTimeoutError()

                if cancel_wait and cancel_wait in done:
                    # User pressed Escape during connecting — clean exit
                    create_task.cancel()
                    logger.info("Cancelled during connecting phase")
                    return

                # create() completed — clean up cancel_wait
                if cancel_wait:
                    cancel_wait.cancel()

                stream = create_task.result()
                yield {"event": "phase", "data": {"phase": "waiting"}}

                # --- First-token timeout (cancel-aware) ---
                stream_iter = stream.__aiter__()
                first_chunk: Any = None

                first_token_task = asyncio.ensure_future(stream_iter.__anext__())
                ft_wait_tasks: list[asyncio.Future[Any]] = [first_token_task]

                if cancel_event:
                    ft_cancel_wait = asyncio.ensure_future(cancel_event.wait())
                    ft_wait_tasks.append(ft_cancel_wait)
                else:
                    ft_cancel_wait = None

                try:
                    ft_done, _ = await asyncio.wait(
                        ft_wait_tasks,
                        timeout=float(self.config.first_token_timeout),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except Exception:
                    first_token_task.cancel()
                    if ft_cancel_wait:
                        ft_cancel_wait.cancel()
                    raise

                if not ft_done:
                    # First-token timeout
                    first_token_task.cancel()
                    if ft_cancel_wait:
                        ft_cancel_wait.cancel()
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

                if ft_cancel_wait and ft_cancel_wait in ft_done:
                    # User cancelled during first-token wait
                    first_token_task.cancel()
                    try:
                        if hasattr(stream, "close"):
                            await stream.close()
                    except Exception:
                        pass
                    return

                if ft_cancel_wait:
                    ft_cancel_wait.cancel()

                try:
                    first_chunk = first_token_task.result()
                except StopAsyncIteration:
                    # Stream ended immediately (empty response)
                    try:
                        if hasattr(stream, "close"):
                            await stream.close()
                    except Exception:
                        pass
                    yield {"event": "done", "data": {}}
                    return

                # --- Stream with full request_timeout (first chunk already received) ---
                current_tool_calls: dict[int, dict[str, Any]] = {}
                total_timeout = float(self.config.request_timeout)

                async def _prepended_stream() -> AsyncGenerator[Any, None]:
                    """Yield the first chunk, then remaining chunks via _iter_stream."""
                    yield first_chunk
                    async for c in AIService._iter_stream(
                        stream_iter, cancel_event, total_timeout, float(self.config.chunk_stall_timeout)
                    ):
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
                        try:
                            await asyncio.wait_for(stream.close(), timeout=2.0)
                        except (asyncio.TimeoutError, Exception):
                            pass  # Don't let slow stream cleanup block cancellation

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
                            "retryable": False,
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
                            "retryable": False,
                        },
                    }
                else:
                    logger.exception("AI bad request error")
                    yield {
                        "event": "error",
                        "data": {"message": "AI request error", "retryable": False},
                    }
                return
            except RateLimitError as e:
                logger.warning("Rate limited by AI provider: %s", e)
                if cancel_event and cancel_event.is_set():
                    return  # user cancelled — don't emit retryable error
                yield {
                    "event": "error",
                    "data": {
                        "message": "Rate limited by API provider",
                        "code": "rate_limit",
                        "retryable": True,
                    },
                }
                return
            except APIStatusError as e:
                # APIStatusError covers HTTP errors not caught above (5xx, 404, etc.).
                # Must be AFTER AuthenticationError, BadRequestError, RateLimitError
                # which are subclasses of APIStatusError.
                if e.status_code >= 500:
                    # Server errors (500, 502, 503, etc.) are transient — retry
                    last_transient_error = e
                    logger.warning("API server error %d (attempt %d/%d)", e.status_code, attempt + 1, max_attempts)
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
                    # Last attempt exhausted — fall through to yield error
                else:
                    # Client errors (4xx not already caught) — non-retryable
                    logger.warning("API client error %d: %s", e.status_code, type(e).__name__)
                    if cancel_event and cancel_event.is_set():
                        return  # user cancelled — don't emit error
                    yield {
                        "event": "error",
                        "data": {
                            "message": f"API error (HTTP {e.status_code})",
                            "code": "api_error",
                            "retryable": False,
                        },
                    }
                    return
            except _StreamTimeoutError:
                logger.warning("Stream timed out mid-response after first token")
                self._build_client()
                if cancel_event and cancel_event.is_set():
                    return  # user cancelled — don't emit retryable error
                yield {
                    "event": "error",
                    "data": {
                        "message": "Stream timed out",
                        "code": "timeout",
                        "retryable": True,
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
                            "reason": "transient_error",
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
                yield {"event": "error", "data": {"message": "An internal error occurred", "retryable": False}}
                return

        # All retries exhausted — yield appropriate error for the last transient error
        if isinstance(last_transient_error, APITimeoutError):
            yield {
                "event": "error",
                "data": {
                    "message": f"Request timed out ({max_attempts} attempts)",
                    "code": "timeout",
                    "retryable": True,
                },
            }
        elif isinstance(last_transient_error, _FirstTokenTimeoutError):
            yield {
                "event": "error",
                "data": {
                    "message": f"No response from API ({max_attempts} attempts)",
                    "code": "timeout",
                    "retryable": True,
                },
            }
        elif isinstance(last_transient_error, APIConnectionError):
            yield {
                "event": "error",
                "data": {
                    "message": f"Cannot connect to API ({max_attempts} attempts)",
                    "code": "connection_error",
                    "retryable": True,
                },
            }
        elif isinstance(last_transient_error, APIStatusError):
            yield {
                "event": "error",
                "data": {
                    "message": f"API server error (HTTP {last_transient_error.status_code}, {max_attempts} attempts)",
                    "code": "api_error",
                    "retryable": True,
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
