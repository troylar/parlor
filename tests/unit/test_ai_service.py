"""Tests for AIService error handling and client configuration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from anteroom.config import AIConfig
from anteroom.services.ai_service import AIService


def _make_config(**overrides) -> AIConfig:
    defaults = {
        "base_url": "http://localhost:11434/v1",
        "api_key": "test-key",
        "model": "gpt-4",
        "request_timeout": 120,
        "verify_ssl": True,
    }
    defaults.update(overrides)
    return AIConfig(**defaults)


class TestClientConfiguration:
    def test_default_timeout_applied(self):
        """httpx client must be built with the configured request_timeout as read timeout."""
        config = _make_config(request_timeout=60)
        with patch("anteroom.services.ai_service.AsyncOpenAI") as mock_openai:
            AIService(config)
            call_kwargs = mock_openai.call_args[1]
            http_client = call_kwargs["http_client"]
            assert isinstance(http_client, httpx.AsyncClient)
            assert http_client.timeout.read == 60.0

    def test_connect_timeout_is_10s(self):
        """Connect timeout must be fixed at 10s regardless of request_timeout."""
        config = _make_config(request_timeout=300)
        with patch("anteroom.services.ai_service.AsyncOpenAI") as mock_openai:
            AIService(config)
            http_client = mock_openai.call_args[1]["http_client"]
            assert http_client.timeout.connect == 10.0

    def test_verify_ssl_true_by_default(self):
        """SSL verification must be enabled by default — AsyncClient built with verify=True."""
        config = _make_config(verify_ssl=True)
        with patch("anteroom.services.ai_service.httpx.AsyncClient") as mock_client_cls:
            with patch("anteroom.services.ai_service.AsyncOpenAI"):
                AIService(config)
            _, kwargs = mock_client_cls.call_args
            assert "verify" in kwargs, "verify must be explicitly passed to AsyncClient"
            assert kwargs["verify"] is True

    def test_verify_ssl_false_when_configured(self):
        """SSL verification must be disabled when verify_ssl: false is explicitly set."""
        config = _make_config(verify_ssl=False)
        with patch("anteroom.services.ai_service.httpx.AsyncClient") as mock_client_cls:
            with patch("anteroom.services.ai_service.AsyncOpenAI"):
                AIService(config)
            _, kwargs = mock_client_cls.call_args
            assert kwargs.get("verify") is False


class TestTimeoutErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout_yields_error_event(self):
        """APITimeoutError must be caught and yield a timeout error event with helpful message."""
        from openai import APITimeoutError

        config = _make_config(request_timeout=30)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        service.client = mock_client

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["data"]["code"] == "timeout"
        assert "30s" in error_events[0]["data"]["message"]
        assert "request_timeout" in error_events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_client_rebuilt_after_stream_chat_timeout(self):
        """_build_client must be called after APITimeoutError in stream_chat to reset the connection pool."""
        from openai import APITimeoutError

        config = _make_config(request_timeout=30)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        service.client = mock_client

        with patch.object(service, "_build_client") as mock_build:
            async for _ in service.stream_chat([{"role": "user", "content": "hi"}]):
                pass
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_generate_title_rebuilds_client_on_timeout(self):
        """_build_client must be called after APITimeoutError in generate_title."""
        from openai import APITimeoutError

        config = _make_config(request_timeout=30)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        service.client = mock_client

        with patch.object(service, "_build_client") as mock_build:
            result = await service.generate_title("hello")
            assert result == "New Conversation"
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_validate_connection_rebuilds_client_on_timeout(self):
        """_build_client must be called after APITimeoutError in validate_connection."""
        from openai import APITimeoutError

        config = _make_config(request_timeout=30)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.models.list = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        service.client = mock_client

        with patch.object(service, "_build_client") as mock_build:
            ok, msg, models = await service.validate_connection()
            assert ok is False
            assert "timed out" in msg.lower()
            assert mock_build.call_count == 1


def _make_service(config: AIConfig | None = None) -> AIService:
    """Create an AIService with a mock client, bypassing __init__."""
    service = AIService.__new__(AIService)
    service.config = config or _make_config()
    service._token_provider = None
    service.client = MagicMock()
    return service


class TestConnectionErrorHandling:
    @pytest.mark.asyncio
    async def test_stream_chat_yields_connection_error_event(self):
        """APIConnectionError in stream_chat must yield an error event with base_url."""
        from openai import APIConnectionError

        service = _make_service(_make_config(base_url="http://bad-host:1234/v1"))
        service.client.chat.completions.create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["data"]["code"] == "connection_error"
        assert "bad-host:1234" in error_events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_stream_chat_rebuilds_client_on_connection_error(self):
        """_build_client must be called after APIConnectionError in stream_chat."""
        from openai import APIConnectionError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

        with patch.object(service, "_build_client") as mock_build:
            async for _ in service.stream_chat([{"role": "user", "content": "hi"}]):
                pass
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_validate_connection_returns_connection_error(self):
        """APIConnectionError in validate_connection must return helpful message with base_url."""
        from openai import APIConnectionError

        service = _make_service(_make_config(base_url="http://dead-server:8080/v1"))
        service.client.models.list = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

        with patch.object(service, "_build_client") as mock_build:
            ok, msg, models = await service.validate_connection()
            assert ok is False
            assert "dead-server:8080" in msg
            assert models == []
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_generate_title_returns_fallback_on_connection_error(self):
        """APIConnectionError in generate_title must return 'New Conversation'."""
        from openai import APIConnectionError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

        with patch.object(service, "_build_client") as mock_build:
            result = await service.generate_title("hello")
            assert result == "New Conversation"
            assert mock_build.call_count == 1


class TestAuthErrorHandling:
    @pytest.mark.asyncio
    async def test_auth_error_yields_auth_failed_event(self):
        """AuthenticationError without token provider must yield auth_failed error event."""
        from openai import AuthenticationError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=AuthenticationError(message="Invalid API key", response=MagicMock(status_code=401), body={})
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["data"]["code"] == "auth_failed"
        assert "API key" in error_events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_auth_error_retries_after_token_refresh(self):
        """AuthenticationError with a token provider must attempt refresh and retry."""
        from openai import AuthenticationError

        service = _make_service()
        service._token_provider = MagicMock()

        # First call raises auth error, second call succeeds with a done event
        async def fake_stream():
            yield MagicMock(choices=[MagicMock(delta=MagicMock(content=None, tool_calls=None), finish_reason="stop")])

        service.client.chat.completions.create = AsyncMock(
            side_effect=[
                AuthenticationError(message="Invalid API key", response=MagicMock(status_code=401), body={}),
                fake_stream(),
            ]
        )

        with patch.object(service, "_build_client"):
            events = []
            async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
                events.append(event)

        assert any(e["event"] == "done" for e in events)

    @pytest.mark.asyncio
    async def test_validate_connection_auth_error(self):
        """AuthenticationError in validate_connection must return auth failure message."""
        from openai import AuthenticationError

        service = _make_service()
        service.client.models.list = AsyncMock(
            side_effect=AuthenticationError(message="Invalid API key", response=MagicMock(status_code=401), body={})
        )

        ok, msg, models = await service.validate_connection()
        assert ok is False
        assert "authentication" in msg.lower() or "api key" in msg.lower()


class TestRateLimitErrorHandling:
    @pytest.mark.asyncio
    async def test_rate_limit_yields_rate_limit_event(self):
        """RateLimitError must yield a rate_limit error event."""
        from openai import RateLimitError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=RateLimitError(message="Rate limit exceeded", response=MagicMock(status_code=429), body={})
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["data"]["code"] == "rate_limit"


class TestBadRequestErrorHandling:
    @pytest.mark.asyncio
    async def test_context_length_exceeded_yields_correct_code(self):
        """BadRequestError with context_length_exceeded must yield the correct error code."""
        from openai import BadRequestError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=BadRequestError(
                message="context_length_exceeded",
                response=MagicMock(status_code=400),
                body={"error": {"code": "context_length_exceeded"}},
            )
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["data"]["code"] == "context_length_exceeded"

    @pytest.mark.asyncio
    async def test_other_bad_request_yields_error_message(self):
        """BadRequestError without context_length_exceeded must yield the error message."""
        from openai import BadRequestError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=BadRequestError(
                message="Invalid request parameters",
                response=MagicMock(status_code=400),
                body={"error": {"code": "invalid_request"}},
            )
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert "Invalid request parameters" in error_events[0]["data"]["message"]


class TestGenericExceptionHandling:
    @pytest.mark.asyncio
    async def test_generic_exception_yields_internal_error(self):
        """Unexpected exceptions must yield a generic internal error (no details leaked)."""
        service = _make_service()
        service.client.chat.completions.create = AsyncMock(side_effect=RuntimeError("something unexpected"))

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["data"]["message"] == "An internal error occurred"
        assert "something unexpected" not in error_events[0]["data"].get("message", "")


class TestIterStream:
    """Tests for _iter_stream: cancel-aware iteration with total timeout."""

    @pytest.mark.asyncio
    async def test_yields_chunks_normally(self):
        """_iter_stream yields all chunks from a well-behaved async iterator."""
        chunks = ["chunk1", "chunk2", "chunk3"]

        async def _gen():
            for c in chunks:
                yield c

        result = []
        async for chunk in AIService._iter_stream(_gen(), cancel_event=None, total_timeout=10.0):
            result.append(chunk)

        assert result == chunks

    @pytest.mark.asyncio
    async def test_cancel_event_stops_stalled_stream(self):
        """Setting cancel_event must stop iteration even when no chunks arrive."""
        cancel = asyncio.Event()
        stall_started = asyncio.Event()

        async def _stalled_gen():
            yield "first_chunk"
            stall_started.set()
            # Stall forever — simulates a hung API connection
            await asyncio.sleep(999)
            yield "never_reached"

        result = []

        async def _consume():
            async for chunk in AIService._iter_stream(_stalled_gen(), cancel_event=cancel, total_timeout=60.0):
                result.append(chunk)

        task = asyncio.create_task(_consume())
        await stall_started.wait()
        cancel.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert result == ["first_chunk"]

    @pytest.mark.asyncio
    async def test_total_timeout_fires_on_stalled_stream(self):
        """Total timeout must raise APITimeoutError when deadline expires during a stall."""
        from openai import APITimeoutError

        async def _stalled_gen():
            yield "first_chunk"
            await asyncio.sleep(999)
            yield "never_reached"

        result = []
        with pytest.raises(APITimeoutError):
            async for chunk in AIService._iter_stream(_stalled_gen(), cancel_event=None, total_timeout=0.1):
                result.append(chunk)

        assert result == ["first_chunk"]

    @pytest.mark.asyncio
    async def test_total_timeout_fires_before_any_chunk(self):
        """Total timeout must fire even if the stream never produces a single chunk."""
        from openai import APITimeoutError

        async def _forever_stalled():
            await asyncio.sleep(999)
            yield "never"

        with pytest.raises(APITimeoutError):
            async for _ in AIService._iter_stream(_forever_stalled(), cancel_event=None, total_timeout=0.1):
                pass

    @pytest.mark.asyncio
    async def test_cancel_takes_priority_over_timeout(self):
        """If cancel fires before timeout, iteration stops gracefully (no APITimeoutError)."""
        cancel = asyncio.Event()

        async def _slow_gen():
            yield "chunk"
            await asyncio.sleep(999)

        result = []

        async def _consume():
            async for chunk in AIService._iter_stream(_slow_gen(), cancel_event=cancel, total_timeout=60.0):
                result.append(chunk)

        task = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        cancel.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert result == ["chunk"]

    @pytest.mark.asyncio
    async def test_empty_stream_returns_immediately(self):
        """An empty async generator should produce no chunks and return cleanly."""

        async def _empty():
            return
            yield  # makes this an async generator

        result = []
        async for chunk in AIService._iter_stream(_empty(), cancel_event=None, total_timeout=10.0):
            result.append(chunk)

        assert result == []

    @pytest.mark.asyncio
    async def test_cancel_wait_cleaned_up_on_normal_end(self):
        """cancel_wait future must be cancelled when stream ends via StopAsyncIteration."""
        cancel = asyncio.Event()

        async def _two_chunks():
            yield "a"
            yield "b"

        result = []
        async for chunk in AIService._iter_stream(_two_chunks(), cancel_event=cancel, total_timeout=10.0):
            result.append(chunk)

        assert result == ["a", "b"]
        # If cancel_wait leaked, the event loop would have dangling futures.
        # No assertion needed beyond clean completion — the test passes if
        # no "Task was destroyed but it is pending" warnings appear.

    @pytest.mark.asyncio
    async def test_stream_exception_propagates(self):
        """Non-StopAsyncIteration exceptions from the stream must propagate to the caller."""

        async def _exploding_gen():
            yield "ok"
            raise ValueError("stream broke")

        result = []
        with pytest.raises(ValueError, match="stream broke"):
            async for chunk in AIService._iter_stream(_exploding_gen(), cancel_event=None, total_timeout=10.0):
                result.append(chunk)

        assert result == ["ok"]

    @pytest.mark.asyncio
    async def test_stream_exception_cleans_up_cancel_wait(self):
        """When stream raises, cancel_wait future must still be cleaned up."""
        cancel = asyncio.Event()

        async def _exploding_gen():
            yield "ok"
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            async for _ in AIService._iter_stream(_exploding_gen(), cancel_event=cancel, total_timeout=10.0):
                pass

    @pytest.mark.asyncio
    async def test_deadline_already_expired_at_loop_entry(self):
        """If deadline expires between chunks, timeout fires at the top of the loop."""
        from openai import APITimeoutError

        call_count = 0

        async def _slow_chunks():
            nonlocal call_count
            call_count += 1
            yield "first"
            call_count += 1
            # Simulate a chunk that takes just long enough for the deadline to pass
            await asyncio.sleep(0.15)
            yield "second"

        result = []
        with pytest.raises(APITimeoutError):
            async for chunk in AIService._iter_stream(_slow_chunks(), cancel_event=None, total_timeout=0.1):
                result.append(chunk)

        assert "first" in result

    @pytest.mark.asyncio
    async def test_aclose_called_on_cancel(self):
        """Stream aclose() must be called when cancel_event fires for resource cleanup."""
        cancel = asyncio.Event()
        aclose_called = asyncio.Event()

        class TrackingStream:
            async def __anext__(self):
                await asyncio.sleep(999)
                return "never"

            async def aclose(self):
                aclose_called.set()

        stream = TrackingStream()

        async def _consume():
            async for _ in AIService._iter_stream(stream, cancel_event=cancel, total_timeout=60.0):
                pass

        task = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        cancel.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert aclose_called.is_set(), "aclose() was not called on cancel"

    @pytest.mark.asyncio
    async def test_aclose_called_on_timeout(self):
        """Stream aclose() must be called when total timeout fires for resource cleanup."""
        from openai import APITimeoutError

        aclose_called = asyncio.Event()

        class TrackingStream:
            async def __anext__(self):
                await asyncio.sleep(999)
                return "never"

            async def aclose(self):
                aclose_called.set()

        stream = TrackingStream()
        with pytest.raises(APITimeoutError):
            async for _ in AIService._iter_stream(stream, cancel_event=None, total_timeout=0.1):
                pass

        assert aclose_called.is_set(), "aclose() was not called on timeout"

    @pytest.mark.asyncio
    async def test_cancel_event_already_set_before_iteration(self):
        """If cancel_event is already set, iteration should stop immediately."""
        cancel = asyncio.Event()
        cancel.set()  # Pre-set before iteration starts

        async def _gen():
            yield "should_not_appear"

        result = []
        async for chunk in AIService._iter_stream(_gen(), cancel_event=cancel, total_timeout=10.0):
            result.append(chunk)

        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_chunks_before_cancel(self):
        """Cancel after several chunks have been delivered — all pre-cancel chunks collected."""
        cancel = asyncio.Event()
        stall_started = asyncio.Event()

        async def _multi_then_stall():
            yield "a"
            yield "b"
            yield "c"
            stall_started.set()
            await asyncio.sleep(999)
            yield "never"

        result = []

        async def _consume():
            async for chunk in AIService._iter_stream(_multi_then_stall(), cancel_event=cancel, total_timeout=60.0):
                result.append(chunk)

        task = asyncio.create_task(_consume())
        await stall_started.wait()
        cancel.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert result == ["a", "b", "c"]


class TestStreamChatWithIterStream:
    """Integration tests: stream_chat uses _iter_stream for cancel and timeout protection."""

    @pytest.mark.asyncio
    async def test_stream_chat_cancel_stops_hung_stream(self):
        """cancel_event must stop stream_chat even when API produces no chunks after initial response."""
        cancel = asyncio.Event()

        # Mock a stream that delivers one chunk then stalls
        mock_choice = MagicMock()
        mock_choice.delta.content = "hello"
        mock_choice.delta.tool_calls = None
        mock_choice.finish_reason = None

        mock_chunk = MagicMock()
        mock_chunk.choices = [mock_choice]

        stall_started = asyncio.Event()

        class MockStream:
            def __aiter__(self):
                return self._gen().__aiter__()

            async def _gen(self):
                yield mock_chunk
                stall_started.set()
                await asyncio.sleep(999)

        config = _make_config(request_timeout=60)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockStream())
        service.client = mock_client

        events = []

        async def _consume():
            async for event in service.stream_chat(
                [{"role": "user", "content": "hi"}],
                cancel_event=cancel,
            ):
                events.append(event)

        task = asyncio.create_task(_consume())
        await stall_started.wait()
        cancel.set()
        await asyncio.wait_for(task, timeout=2.0)

        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) == 1
        assert token_events[0]["data"]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_stream_chat_total_timeout_yields_error(self):
        """stream_chat must yield a timeout error when _iter_stream total timeout fires."""

        class MockStream:
            def __aiter__(self):
                return self._gen().__aiter__()

            async def _gen(self):
                await asyncio.sleep(999)
                yield MagicMock()

        config = _make_config(request_timeout=1)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockStream())
        service.client = mock_client

        events = []
        with patch.object(service, "_build_client"):
            async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
                events.append(event)

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["data"]["code"] == "timeout"


class TestBuildClientCleanup:
    """Tests for _build_client closing old httpx connection pools."""

    def test_old_http_client_closed_on_rebuild(self):
        """_build_client must schedule close() on the old httpx client to prevent resource leaks."""
        config = _make_config()

        with patch("anteroom.services.ai_service.AsyncOpenAI"):
            service = AIService.__new__(AIService)
            service.config = config
            service._token_provider = None

            # Simulate an existing client with an internal httpx client
            old_http = MagicMock()
            old_openai = MagicMock()
            old_openai._client = old_http
            service.client = old_openai

            # Rebuild — should schedule close on old_http
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_event_loop = MagicMock()
                mock_loop.return_value = mock_event_loop
                service._build_client()
                mock_event_loop.create_task.assert_called_once()
                # The coroutine passed to create_task should be old_http.close()
                old_http.close.assert_called_once()

    def test_rebuild_without_existing_client(self):
        """_build_client must not fail when called for the first time (no old client)."""
        config = _make_config()
        with patch("anteroom.services.ai_service.AsyncOpenAI"):
            service = AIService(config)
            # Should not raise — client attribute is set for the first time
            assert service.client is not None

    def test_rebuild_handles_no_running_loop(self):
        """_build_client must not raise when no event loop is running (e.g., during __init__)."""
        config = _make_config()

        with patch("anteroom.services.ai_service.AsyncOpenAI"):
            service = AIService.__new__(AIService)
            service.config = config
            service._token_provider = None

            old_http = MagicMock()
            old_http.close = MagicMock()
            old_openai = MagicMock()
            old_openai._client = old_http
            service.client = old_openai

            # No running loop — should swallow RuntimeError gracefully
            service._build_client()
            # No assertion needed — test passes if no exception is raised

    def test_rebuild_old_client_missing_internal_http(self):
        """_build_client must not fail when old client has no _client attribute."""
        config = _make_config()

        with patch("anteroom.services.ai_service.AsyncOpenAI"):
            service = AIService.__new__(AIService)
            service.config = config
            service._token_provider = None

            old_openai = MagicMock(spec=[])  # spec=[] means no attributes
            service.client = old_openai

            # Should not raise even though old_openai has no _client
            service._build_client()
            assert service.client is not None

    def test_rebuild_old_http_close_raises(self):
        """_build_client must not fail if old_http.close() raises."""
        config = _make_config()

        with patch("anteroom.services.ai_service.AsyncOpenAI"):
            service = AIService.__new__(AIService)
            service.config = config
            service._token_provider = None

            old_http = MagicMock()
            old_http.close.side_effect = RuntimeError("close failed")
            old_openai = MagicMock()
            old_openai._client = old_http
            service.client = old_openai

            with patch("asyncio.get_running_loop") as mock_loop:
                mock_event_loop = MagicMock()
                mock_event_loop.create_task.side_effect = RuntimeError("task creation failed")
                mock_loop.return_value = mock_event_loop
                # Should not raise — cleanup errors are swallowed
                service._build_client()
                assert service.client is not None


class TestStreamChatPhaseEvents:
    """Tests for lifecycle phase events emitted by stream_chat() (#203)."""

    @pytest.mark.asyncio
    async def test_phase_connecting_emitted_before_api_call(self):
        """stream_chat must emit phase:connecting before the API create() call."""

        class MockStream:
            def __aiter__(self):
                return self._gen().__aiter__()

            async def _gen(self):
                yield MagicMock(
                    choices=[MagicMock(delta=MagicMock(content=None, tool_calls=None), finish_reason="stop")]
                )

            async def close(self):
                pass

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(return_value=MockStream())

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        event_types = [e["event"] for e in events]
        assert "phase" in event_types
        phase_events = [e for e in events if e["event"] == "phase"]
        assert phase_events[0]["data"]["phase"] == "connecting"

    @pytest.mark.asyncio
    async def test_phase_waiting_emitted_after_api_call(self):
        """stream_chat must emit phase:waiting after the API create() returns."""

        class MockStream:
            def __aiter__(self):
                return self._gen().__aiter__()

            async def _gen(self):
                yield MagicMock(
                    choices=[MagicMock(delta=MagicMock(content=None, tool_calls=None), finish_reason="stop")]
                )

            async def close(self):
                pass

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(return_value=MockStream())

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        phase_events = [e for e in events if e["event"] == "phase"]
        assert len(phase_events) >= 2
        assert phase_events[1]["data"]["phase"] == "waiting"

    @pytest.mark.asyncio
    async def test_phase_order_connecting_then_waiting_then_content(self):
        """Phase events must appear in order: connecting → waiting → token/done."""

        class MockStream:
            def __aiter__(self):
                return self._gen().__aiter__()

            async def _gen(self):
                yield MagicMock(
                    choices=[MagicMock(delta=MagicMock(content="hello", tool_calls=None), finish_reason=None)]
                )
                yield MagicMock(
                    choices=[MagicMock(delta=MagicMock(content=None, tool_calls=None), finish_reason="stop")]
                )

            async def close(self):
                pass

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(return_value=MockStream())

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        event_types = [e["event"] for e in events]
        # connecting must come first
        assert event_types[0] == "phase"
        assert events[0]["data"]["phase"] == "connecting"
        # waiting must come second
        assert event_types[1] == "phase"
        assert events[1]["data"]["phase"] == "waiting"
        # token must come after the phase events
        assert event_types[2] == "token"

    @pytest.mark.asyncio
    async def test_phase_events_before_tool_calls(self):
        """Phase events must be emitted even when the response contains tool calls."""
        from openai.types.chat.chat_completion_chunk import (
            ChoiceDeltaToolCall,
            ChoiceDeltaToolCallFunction,
        )

        class MockStream:
            def __aiter__(self):
                return self._gen().__aiter__()

            async def _gen(self):
                tc = ChoiceDeltaToolCall(
                    index=0,
                    id="call_123",
                    function=ChoiceDeltaToolCallFunction(name="bash", arguments='{"command":"ls"}'),
                )
                yield MagicMock(choices=[MagicMock(delta=MagicMock(content=None, tool_calls=[tc]), finish_reason=None)])
                yield MagicMock(
                    choices=[MagicMock(delta=MagicMock(content=None, tool_calls=None), finish_reason="tool_calls")]
                )

            async def close(self):
                pass

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(return_value=MockStream())

        events = []
        async for event in service.stream_chat(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "bash"}}],
        ):
            events.append(event)

        phase_events = [e for e in events if e["event"] == "phase"]
        assert len(phase_events) == 2
        assert phase_events[0]["data"]["phase"] == "connecting"
        assert phase_events[1]["data"]["phase"] == "waiting"

    @pytest.mark.asyncio
    async def test_phase_connecting_emitted_before_timeout_error(self):
        """phase:connecting must still be emitted even if the API call times out."""
        from openai import APITimeoutError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))

        events = []
        with patch.object(service, "_build_client"):
            async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
                events.append(event)

        # connecting is emitted before the create() call, so it should appear
        # even when create() raises
        phase_events = [e for e in events if e["event"] == "phase"]
        assert len(phase_events) >= 1
        assert phase_events[0]["data"]["phase"] == "connecting"

    @pytest.mark.asyncio
    async def test_phase_connecting_emitted_before_connection_error(self):
        """phase:connecting must be emitted even if the API connection fails."""
        from openai import APIConnectionError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

        events = []
        with patch.object(service, "_build_client"):
            async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
                events.append(event)

        phase_events = [e for e in events if e["event"] == "phase"]
        assert len(phase_events) >= 1
        assert phase_events[0]["data"]["phase"] == "connecting"

    @pytest.mark.asyncio
    async def test_phase_connecting_emitted_before_auth_error(self):
        """phase:connecting must be emitted even if the API returns 401."""
        from openai import AuthenticationError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=AuthenticationError(message="bad key", response=MagicMock(status_code=401), body={})
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        phase_events = [e for e in events if e["event"] == "phase"]
        assert len(phase_events) >= 1
        assert phase_events[0]["data"]["phase"] == "connecting"

    @pytest.mark.asyncio
    async def test_no_waiting_phase_on_create_failure(self):
        """phase:waiting must NOT be emitted when create() raises (we never started waiting)."""
        from openai import APITimeoutError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))

        events = []
        with patch.object(service, "_build_client"):
            async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
                events.append(event)

        phase_events = [e for e in events if e["event"] == "phase"]
        phase_names = [e["data"]["phase"] for e in phase_events]
        assert "waiting" not in phase_names

    @pytest.mark.asyncio
    async def test_phase_events_with_cancel_event(self):
        """Phase events must be emitted even when cancel_event is provided."""
        cancel = asyncio.Event()

        class MockStream:
            def __aiter__(self):
                return self._gen().__aiter__()

            async def _gen(self):
                yield MagicMock(choices=[MagicMock(delta=MagicMock(content="hi", tool_calls=None), finish_reason=None)])
                yield MagicMock(
                    choices=[MagicMock(delta=MagicMock(content=None, tool_calls=None), finish_reason="stop")]
                )

            async def close(self):
                pass

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(return_value=MockStream())

        events = []
        async for event in service.stream_chat(
            [{"role": "user", "content": "hi"}],
            cancel_event=cancel,
        ):
            events.append(event)

        phase_events = [e for e in events if e["event"] == "phase"]
        assert len(phase_events) == 2
        assert phase_events[0]["data"]["phase"] == "connecting"
        assert phase_events[1]["data"]["phase"] == "waiting"

    @pytest.mark.asyncio
    async def test_phase_events_with_extra_system_prompt(self):
        """Phase events must work when extra_system_prompt is provided."""

        class MockStream:
            def __aiter__(self):
                return self._gen().__aiter__()

            async def _gen(self):
                yield MagicMock(
                    choices=[MagicMock(delta=MagicMock(content=None, tool_calls=None), finish_reason="stop")]
                )

            async def close(self):
                pass

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(return_value=MockStream())

        events = []
        async for event in service.stream_chat(
            [{"role": "user", "content": "hi"}],
            extra_system_prompt="Be helpful",
        ):
            events.append(event)

        phase_events = [e for e in events if e["event"] == "phase"]
        assert len(phase_events) == 2

    @pytest.mark.asyncio
    async def test_phase_connecting_emitted_before_rate_limit(self):
        """phase:connecting must be emitted before RateLimitError."""
        from openai import RateLimitError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=RateLimitError(message="rate limited", response=MagicMock(status_code=429), body={})
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        phase_events = [e for e in events if e["event"] == "phase"]
        assert len(phase_events) >= 1
        assert phase_events[0]["data"]["phase"] == "connecting"
