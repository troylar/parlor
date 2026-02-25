"""Agent turn execution: streaming, event handling, signal management."""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from typing import Any, Callable

from ..services import storage
from ..services.agent_loop import run_agent_loop
from ..services.ai_service import AIService
from . import renderer
from .event_handlers import EventContext, handle_repl_event
from .renderer import MUTED

logger = logging.getLogger(__name__)

_IS_WINDOWS = __import__("platform").system() == "Windows"


class RagEmbeddingCache:
    """Lazily create and cache an embedding service for RAG retrieval."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._service: Any = None
        self._checked: bool = False

    async def get(self) -> Any:
        """Return the cached embedding service, creating on first call."""
        if self._checked:
            return self._service
        self._checked = True
        try:
            from ..services.embeddings import create_embedding_service

            svc = create_embedding_service(self._config)
            if svc and self._config.embeddings.enabled is None:
                probe_ok = await svc.probe()
                if not probe_ok:
                    logger.info("Embedding endpoint unavailable; semantic search disabled")
                    svc = None
            self._service = svc
            return svc
        except Exception:
            logger.debug("RAG: failed to create embedding service", exc_info=True)
            return None


@dataclass
class AgentTurnContext:
    """All state needed to execute one agent turn (stream + event handling)."""

    ai_service: AIService
    ai_messages: list[dict[str, Any]]
    tool_executor: Any
    tools_openai: list[dict[str, Any]] | None
    extra_system_prompt: str
    cancel_event: asyncio.Event
    config: Any
    db: Any
    conv: dict[str, Any]
    identity_kwargs: dict[str, str | None]
    user_input: str
    is_first_message: bool
    msg_queue: asyncio.Queue[dict[str, Any]]
    input_queue: asyncio.Queue[str]
    exit_flag: asyncio.Event
    working_dir: str
    subagent_limiter: Any
    cancel_event_ref: list[asyncio.Event | None] | None
    current_cancel_event: list[asyncio.Event | None]
    agent_busy: asyncio.Event
    session_invalidate: Callable[[], None]
    has_pending_work: Callable[[], bool]

    # Plan mode state (mutable lists for shared state)
    plan_checklist_steps: list[str] = field(default_factory=list)
    plan_current_step: list[int] = field(default_factory=lambda: [0])
    plan_active: list[bool] = field(default_factory=lambda: [False])
    apply_plan_mode: Callable[[str], None] | None = None

    # Callbacks
    get_tiktoken_encoding: Callable[[], Any] | None = None
    estimate_tokens_fn: Callable[[list[dict[str, Any]]], int] | None = None
    drain_input_fn: Callable[..., Any] | None = None


async def inject_rag_context(
    config: Any,
    plan_active: list[bool],
    db: Any,
    conv_id: str,
    expanded: str,
    extra_system_prompt: str,
    rag_cache: RagEmbeddingCache,
) -> str:
    """Retrieve RAG context and inject into system prompt.

    Returns the (possibly updated) extra_system_prompt.
    """
    if not config.rag.enabled or plan_active[0]:
        return extra_system_prompt

    try:
        from ..services.rag import format_rag_context, retrieve_context, strip_rag_context

        rag_emb = await rag_cache.get()
        if rag_emb:
            rag_chunks = await retrieve_context(
                query=expanded,
                db=db,
                embedding_service=rag_emb,
                config=config.rag,
                current_conversation_id=conv_id,
            )
            extra_system_prompt = strip_rag_context(extra_system_prompt)
            if rag_chunks:
                extra_system_prompt += format_rag_context(rag_chunks)
                renderer.console.print(f"  [{MUTED}][RAG: {len(rag_chunks)} relevant chunk(s) retrieved][/{MUTED}]")
    except Exception:
        logger.debug("RAG retrieval failed in CLI", exc_info=True)

    return extra_system_prompt


def _add_signal_handler(loop: asyncio.AbstractEventLoop, sig: int, callback: Any) -> bool:
    """Add a signal handler, returning False on Windows where it's unsupported."""
    if _IS_WINDOWS:
        return False
    try:
        loop.add_signal_handler(sig, callback)
        return True
    except (NotImplementedError, OSError):
        return False


def _remove_signal_handler(loop: asyncio.AbstractEventLoop, sig: int) -> None:
    """Remove a signal handler, no-op on Windows."""
    if _IS_WINDOWS:
        return
    try:
        loop.remove_signal_handler(sig)
    except (NotImplementedError, OSError):
        pass


async def run_agent_turn(ctx: AgentTurnContext) -> bool:
    """Execute one agent turn: stream response, handle events, generate title.

    Returns the updated is_first_message value.
    """
    renderer.clear_turn_history()
    renderer.clear_subagent_state()
    if ctx.subagent_limiter is not None:
        ctx.subagent_limiter.reset()

    ctx.current_cancel_event[0] = ctx.cancel_event
    if ctx.cancel_event_ref is not None:
        ctx.cancel_event_ref[0] = ctx.cancel_event

    loop = asyncio.get_event_loop()
    original_handler = signal.getsignal(signal.SIGINT)
    _add_signal_handler(loop, signal.SIGINT, ctx.cancel_event.set)

    ctx.agent_busy.set()

    thinking = False
    user_attempt = 0
    is_first_message = ctx.is_first_message

    try:
        response_token_count = 0
        total_elapsed = 0.0

        evt_ctx = EventContext(
            plan_checklist_steps=ctx.plan_checklist_steps,
            plan_current_step=ctx.plan_current_step,
            plan_active=ctx.plan_active,
            max_retries=ctx.config.cli.max_retries,
            retry_delay=ctx.config.cli.retry_delay,
            model_context_window=ctx.config.cli.model_context_window,
            auto_compact_threshold=ctx.config.cli.context_auto_compact_tokens,
            auto_plan_mode=ctx.config.cli.planning.auto_mode,
            cancel_event=ctx.cancel_event,
            db=ctx.db,
            conv_id=ctx.conv["id"],
            identity_kwargs=ctx.identity_kwargs,
            ai_messages=ctx.ai_messages,
            apply_plan_mode=ctx.apply_plan_mode,
            get_tiktoken_encoding=ctx.get_tiktoken_encoding,
            estimate_tokens=ctx.estimate_tokens_fn,
        )

        def _warn(cmd: str) -> None:
            renderer.console.print(f"[yellow]Command {cmd} ignored during streaming. Queue messages only.[/yellow]")

        if ctx.drain_input_fn:
            await ctx.drain_input_fn(
                ctx.input_queue,
                ctx.msg_queue,
                ctx.working_dir,
                ctx.db,
                ctx.conv["id"],
                ctx.cancel_event,
                ctx.exit_flag,
                warn_callback=_warn,
                identity_kwargs=ctx.identity_kwargs,
                file_max_chars=ctx.config.cli.file_reference_max_chars,
            )

        while True:
            user_attempt += 1
            should_retry = False
            evt_ctx.user_attempt = user_attempt
            evt_ctx.thinking = thinking
            evt_ctx.response_token_count = response_token_count
            evt_ctx.total_elapsed = total_elapsed
            evt_ctx.should_retry = False
            evt_ctx.cancel_event = ctx.cancel_event
            evt_ctx.conv_id = ctx.conv["id"]

            async for event in run_agent_loop(
                ai_service=ctx.ai_service,
                messages=ctx.ai_messages,
                tool_executor=ctx.tool_executor,
                tools_openai=ctx.tools_openai,
                cancel_event=ctx.cancel_event,
                extra_system_prompt=ctx.extra_system_prompt,
                max_iterations=ctx.config.cli.max_tool_iterations,
                message_queue=ctx.msg_queue,
                narration_cadence=ctx.ai_service.config.narration_cadence,
                tool_output_max_chars=ctx.config.cli.tool_output_max_chars,
                auto_plan_threshold=(
                    ctx.config.cli.planning.auto_threshold_tools
                    if not ctx.plan_active[0] and ctx.config.cli.planning.auto_mode != "off"
                    else 0
                ),
            ):
                if ctx.drain_input_fn:
                    await ctx.drain_input_fn(
                        ctx.input_queue,
                        ctx.msg_queue,
                        ctx.working_dir,
                        ctx.db,
                        ctx.conv["id"],
                        ctx.cancel_event,
                        ctx.exit_flag,
                        warn_callback=_warn,
                        identity_kwargs=ctx.identity_kwargs,
                        file_max_chars=ctx.config.cli.file_reference_max_chars,
                    )

                await handle_repl_event(evt_ctx, event)
                thinking = evt_ctx.thinking
                response_token_count = evt_ctx.response_token_count
                total_elapsed = evt_ctx.total_elapsed
                should_retry = evt_ctx.should_retry

            if not should_retry:
                break

        # Generate title on first exchange (skip if user cancelled)
        if is_first_message:
            is_first_message = False
            if not ctx.cancel_event.is_set():
                try:
                    title = await ctx.ai_service.generate_title(ctx.user_input)
                    storage.update_conversation_title(ctx.db, ctx.conv["id"], title)
                except Exception:
                    pass

    except KeyboardInterrupt:
        if thinking:
            renderer.stop_thinking_sync()
        renderer.render_response_end()
    finally:
        if thinking:
            renderer.stop_thinking_sync()
        if not ctx.has_pending_work():
            ctx.agent_busy.clear()
            ctx.session_invalidate()
        ctx.current_cancel_event[0] = None
        if ctx.cancel_event_ref is not None:
            ctx.cancel_event_ref[0] = None
        ctx.cancel_event.set()
        _remove_signal_handler(loop, signal.SIGINT)
        if not _IS_WINDOWS:
            signal.signal(signal.SIGINT, original_handler)

    return is_first_message
