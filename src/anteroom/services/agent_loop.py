"""Shared agentic loop for web and CLI chat interfaces."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from .ai_service import AIService
from .context_trust import wrap_untrusted
from .token_budget import BudgetCheckResult, check_all_budgets

logger = logging.getLogger(__name__)


@dataclass
class AgentEvent:
    kind: str
    data: dict[str, Any]


_DEFAULT_TOOL_OUTPUT_MAX_CHARS = 2000


def _truncate_large_tool_outputs(
    messages: list[dict[str, Any]], max_chars: int = _DEFAULT_TOOL_OUTPUT_MAX_CHARS
) -> bool:
    """Truncate oversized tool result messages and append a retry hint. Returns True if any were truncated."""
    truncated_any = False
    tool_call_names: dict[str, str] = {}

    # Build map of tool_call_id -> tool name from assistant messages
    for msg in messages:
        for tc in msg.get("tool_calls", []):
            tc_id = tc.get("id", "")
            func = tc.get("function", {})
            if tc_id and func.get("name"):
                tool_call_names[tc_id] = func["name"]

    for msg in messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if len(content) <= max_chars:
            continue

        tc_id = msg.get("tool_call_id", "")
        tool_name = tool_call_names.get(tc_id, "unknown tool")
        original_len = len(content)
        msg["content"] = (
            content[:max_chars]
            + f"\n\n... [TRUNCATED — original output was {original_len:,} chars from '{tool_name}'. "
            f"The output exceeded the context window. "
            f"You MUST retry this tool call with more constrained parameters "
            f"(e.g. fewer results, a narrower query, or a smaller limit) "
            f"to get output that fits within the context window.]"
        )
        truncated_any = True
        logger.info(
            "Truncated tool output for %s (call %s): %d -> %d chars",
            tool_name,
            tc_id,
            original_len,
            len(msg["content"]),
        )

    return truncated_any


def _build_compaction_history(messages: list[dict[str, Any]]) -> str:
    """Build a structured history string for the compaction summary prompt.

    Includes tool call outcomes (not just names) so the AI can distinguish
    completed steps from pending ones after compaction.
    """
    history_text = []
    # Map tool_call_id -> tool name for annotating tool result messages
    tool_id_to_name: dict[str, str] = {}
    for msg in messages:
        for tc in msg.get("tool_calls", []):
            tc_id = tc.get("id", "")
            func = tc.get("function", {})
            if tc_id and func.get("name"):
                tool_id_to_name[tc_id] = func["name"]

    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "tool":
            tc_id = msg.get("tool_call_id", "")
            tool_name = tool_id_to_name.get(tc_id, "unknown")
            try:
                result = json.loads(content) if isinstance(content, str) and content else {}
            except (json.JSONDecodeError, ValueError):
                result = {"raw": content}
            if isinstance(result, dict) and "error" in result:
                snippet = str(result["error"])[:200]
                history_text.append(f"  tool_result: {tool_name} -> ERROR: {snippet}")
            else:
                safe_content = content if isinstance(content, str) else ""
                snippet = safe_content[:200] + "..." if len(safe_content) > 200 else safe_content
                history_text.append(f"  tool_result: {tool_name} -> SUCCESS: {snippet}")
            continue

        if isinstance(content, str) and content:
            truncated = content[:500] + "..." if len(content) > 500 else content
            history_text.append(f"{role}: {truncated}")

        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            name = func.get("name", "?")
            args_raw = func.get("arguments", "")
            try:
                args = json.loads(args_raw) if args_raw else {}
                args_preview = ", ".join(f"{k}={str(v)[:40]!r}" for k, v in list(args.items())[:3])
            except (json.JSONDecodeError, ValueError):
                args_preview = args_raw[:80]
            history_text.append(f"  tool_call: {name}({args_preview})")

    return "\n".join(history_text)


async def _compact_messages(
    ai_service: AIService,
    messages: list[dict[str, Any]],
) -> bool:
    """Summarize conversation history to reduce context size. Returns True on success."""
    if len(messages) < 4:
        return False

    history_text = _build_compaction_history(messages)

    summary_prompt = (
        "Summarize the following conversation concisely, preserving:\n"
        "- Key decisions and conclusions\n"
        "- File paths that were read, written, or edited\n"
        "- Important code changes and their purpose\n"
        "- Which steps of any multi-step plan have been COMPLETED (tool_result SUCCESS) vs remaining\n"
        "- Current state of the task — what has been done and what is next\n"
        "- Any errors encountered and how they were resolved\n\n" + history_text
    )

    try:
        response = await ai_service.client.chat.completions.create(
            model=ai_service.config.model,
            messages=[{"role": "user", "content": summary_prompt}],
            max_completion_tokens=1000,
        )
        summary = response.choices[0].message.content or "Conversation summary unavailable."
    except Exception:
        logger.exception("Failed to generate compaction summary")
        return False

    original_count = len(messages)
    messages.clear()
    messages.append(
        {
            "role": "system",
            "content": (f"Previous conversation summary (auto-compacted from {original_count} messages):\n\n{summary}"),
        }
    )
    logger.info("Compacted %d messages into summary for context recovery", original_count)
    return True


async def _execute_tool(
    tc: dict[str, Any],
    tool_executor: Any,
    cancel_event: asyncio.Event | None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Execute a single tool call, returning (tool_call, result, status)."""
    try:
        if cancel_event:
            cancel_task = asyncio.create_task(cancel_event.wait())
            exec_task = asyncio.create_task(tool_executor(tc["function_name"], tc["arguments"]))
            done, pending = await asyncio.wait({cancel_task, exec_task}, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
                try:
                    await asyncio.wait_for(p, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            if exec_task in done:
                return tc, exec_task.result(), "success"
            return tc, {"error": "Cancelled by user"}, "cancelled"
        else:
            result = await tool_executor(tc["function_name"], tc["arguments"])
            return tc, result, "success"
    except Exception as e:
        return tc, {"error": str(e)}, "error"


_NARRATION_PROMPT = (
    "Briefly summarize your progress in 1-2 sentences: what have you found or done so far, "
    "and what are you doing next? Then continue your work."
)


async def run_agent_loop(
    ai_service: AIService,
    messages: list[dict[str, Any]],
    tool_executor: Any,
    tools_openai: list[dict[str, Any]] | None,
    cancel_event: asyncio.Event | None = None,
    extra_system_prompt: str | None = None,
    max_iterations: int = 50,
    message_queue: asyncio.Queue[dict[str, Any]] | None = None,
    narration_cadence: int = 0,
    tool_output_max_chars: int = _DEFAULT_TOOL_OUTPUT_MAX_CHARS,
    auto_plan_threshold: int = 0,
    budget_config: Any | None = None,
    get_token_totals: Any | None = None,
    dlp_scanner: Any | None = None,
    injection_detector: Any | None = None,
    output_filter: Any | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """Run the agentic tool-call loop, yielding events.

    tool_executor must be an async callable: (tool_name, arguments) -> dict
    budget_config: BudgetConfig dataclass (or None to disable).
    get_token_totals: async callable() -> (conversation_total, daily_total)
    """
    iteration = 0
    context_recovery_attempts = 0
    max_context_recoveries = 2  # truncate once, compact once
    total_tool_calls = 0
    auto_plan_suggested = False
    request_tokens = 0  # tokens accumulated in this run_agent_loop invocation

    while iteration < max_iterations:
        iteration += 1
        tool_calls_pending: list[dict[str, Any]] = []
        assistant_content = ""
        got_context_error = False

        # Budget enforcement: check before each API call
        if budget_config and budget_config.enabled and get_token_totals:
            try:
                conv_total, daily_total = await get_token_totals()
            except Exception:
                logger.warning("Failed to fetch token totals for budget check", exc_info=True)
                conv_total, daily_total = 0, 0

            status = check_all_budgets(
                request_tokens=request_tokens,
                conversation_tokens=conv_total,
                daily_tokens=daily_total,
                max_per_request=budget_config.max_tokens_per_request,
                max_per_conversation=budget_config.max_tokens_per_conversation,
                max_per_day=budget_config.max_tokens_per_day,
                warn_threshold_percent=budget_config.warn_threshold_percent,
            )
            if status is not None:
                if status.result == BudgetCheckResult.EXCEEDED:
                    if budget_config.action_on_exceed == "block":
                        yield AgentEvent(
                            kind="error",
                            data={
                                "message": (
                                    f"Token budget exceeded ({status.label}): {status.used:,} / {status.limit:,} tokens"
                                ),
                                "code": "budget_exceeded",
                                "retryable": False,
                                "budget_label": status.label,
                                "budget_used": status.used,
                                "budget_limit": status.limit,
                            },
                        )
                        return
                    else:
                        yield AgentEvent(
                            kind="budget_warning",
                            data={
                                "message": (
                                    f"Token budget exceeded ({status.label}): "
                                    f"{status.used:,} / {status.limit:,} tokens (warn mode)"
                                ),
                                "label": status.label,
                                "used": status.used,
                                "limit": status.limit,
                                "percent": status.percent,
                            },
                        )
                elif status.result == BudgetCheckResult.WARNING:
                    yield AgentEvent(
                        kind="budget_warning",
                        data={
                            "message": (
                                f"Approaching {status.label} token budget: "
                                f"{status.used:,} / {status.limit:,} tokens "
                                f"({status.percent:.0f}%)"
                            ),
                            "label": status.label,
                            "used": status.used,
                            "limit": status.limit,
                            "percent": status.percent,
                        },
                    )

        yield AgentEvent(kind="thinking", data={})

        _dlp_blocked = False
        async for event in ai_service.stream_chat(
            messages,
            tools=tools_openai,
            cancel_event=cancel_event,
            extra_system_prompt=extra_system_prompt,
        ):
            etype = event["event"]
            if etype == "token":
                chunk = event["data"]["content"]
                # Per-chunk DLP: redact inline, block breaks stream.
                # Warn is deferred to the final assembled-text scan to
                # avoid duplicate events (per-chunk + final).
                if dlp_scanner is not None and dlp_scanner.enabled and dlp_scanner.scan_output:
                    chunk, dlp_result = dlp_scanner.apply(chunk, "output")
                    if dlp_result.matched and dlp_result.action == "block":
                        yield AgentEvent(
                            kind="dlp_blocked",
                            data={"direction": "output", "matches": [m.rule_name for m in dlp_result.matches]},
                        )
                        _dlp_blocked = True
                        break
                # Per-chunk output filter: custom pattern block (leak detection
                # requires full text and runs post-stream).
                if output_filter is not None and output_filter.enabled:
                    chunk_result = output_filter.scan_patterns_only(chunk)
                    if chunk_result.matched and chunk_result.action == "block":
                        yield AgentEvent(
                            kind="output_filter_blocked",
                            data={"matches": [m.rule_name for m in chunk_result.matches]},
                        )
                        _dlp_blocked = True  # reuse flag to skip final scan
                        break
                assistant_content += chunk
                yield AgentEvent(kind="token", data={"content": chunk})
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
            elif etype == "tool_call_args_delta":
                yield AgentEvent(kind="tool_call_args_delta", data=event["data"])
            elif etype == "phase":
                yield AgentEvent(kind="phase", data=event["data"])
            elif etype == "retrying":
                yield AgentEvent(kind="retrying", data=event["data"])
            elif etype == "usage":
                request_tokens += event["data"].get("total_tokens", 0)
                yield AgentEvent(kind="usage", data=event["data"])
            elif etype == "error":
                if (
                    event["data"].get("code") == "context_length_exceeded"
                    and context_recovery_attempts < max_context_recoveries
                ):
                    got_context_error = True
                    break
                yield AgentEvent(kind="error", data=event["data"])
                return
            elif etype == "done":
                break

        if _dlp_blocked:
            return

        if got_context_error:
            context_recovery_attempts += 1
            iteration -= 1  # don't count the failed attempt

            # Strategy 1: truncate oversized tool outputs and let the AI retry with smaller params
            if _truncate_large_tool_outputs(messages, max_chars=tool_output_max_chars):
                yield AgentEvent(
                    kind="token",
                    data={
                        "content": (
                            "\n\n*Context limit reached — tool output was too large. "
                            "Truncated and retrying with smaller scope...*\n\n"
                        )
                    },
                )
                continue

            # Strategy 2: compact entire conversation into a summary
            yield AgentEvent(
                kind="token",
                data={"content": "\n\n*Context limit reached — compacting conversation and retrying...*\n\n"},
            )
            if await _compact_messages(ai_service, messages):
                continue

            yield AgentEvent(
                kind="error",
                data={
                    "message": (
                        "Conversation too long for model context window. "
                        "Recovery failed after truncation and compaction. "
                        "Please start a new conversation."
                    )
                },
            )
            return

        if not tool_calls_pending:
            if assistant_content:
                # Final DLP scan on complete assembled text (catches patterns split across chunks)
                if dlp_scanner is not None and dlp_scanner.enabled and dlp_scanner.scan_output:
                    assistant_content, dlp_final = dlp_scanner.apply(assistant_content, "output")
                    if dlp_final.matched and dlp_final.action == "block":
                        yield AgentEvent(
                            kind="dlp_blocked",
                            data={"direction": "output", "matches": [m.rule_name for m in dlp_final.matches]},
                        )
                        return
                    if dlp_final.matched and dlp_final.action == "warn":
                        yield AgentEvent(
                            kind="dlp_warning",
                            data={"direction": "output", "matches": [m.rule_name for m in dlp_final.matches]},
                        )
                # Output content filter scan (system prompt leak + custom patterns)
                if output_filter is not None and output_filter.enabled:
                    assistant_content, of_result = output_filter.apply(assistant_content)
                    if of_result.matched and of_result.action == "block":
                        yield AgentEvent(
                            kind="output_filter_blocked",
                            data={"matches": [m.rule_name for m in of_result.matches]},
                        )
                        return
                    if of_result.matched and of_result.action == "warn":
                        yield AgentEvent(
                            kind="output_filter_warning",
                            data={"matches": [m.rule_name for m in of_result.matches]},
                        )
                yield AgentEvent(kind="assistant_message", data={"content": assistant_content})
            # Append the final assistant response to the conversation history so
            # subsequent turns (including queued messages) see the complete context.
            # Tool-call iterations already append to messages above; this covers
            # the final no-tool-call response that was previously missing.
            if assistant_content:
                messages.append({"role": "assistant", "content": assistant_content})
            yield AgentEvent(kind="done", data={})

            # Check message queue for follow-up messages
            if message_queue is not None:
                try:
                    queued_msg = message_queue.get_nowait()
                    messages.append(queued_msg)
                    yield AgentEvent(kind="queued_message", data=queued_msg)
                    continue
                except asyncio.QueueEmpty:
                    pass
            return

        # Final DLP scan on complete assistant text before storage (tool-call turn)
        if dlp_scanner is not None and dlp_scanner.enabled and dlp_scanner.scan_output and assistant_content:
            assistant_content, _ = dlp_scanner.apply(assistant_content, "output")
        # Output filter scan on tool-call turn text
        if output_filter is not None and output_filter.enabled and assistant_content:
            assistant_content, of_result = output_filter.apply(assistant_content)
            if of_result.matched and of_result.action == "block":
                yield AgentEvent(
                    kind="output_filter_blocked",
                    data={"matches": [m.rule_name for m in of_result.matches]},
                )
                return
            if of_result.matched and of_result.action in ("warn", "redact"):
                yield AgentEvent(
                    kind="output_filter_warning",
                    data={"matches": [m.rule_name for m in of_result.matches]},
                )

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

        # Execute tool calls in parallel
        if cancel_event and cancel_event.is_set():
            for tc in tool_calls_pending:
                cancelled_result = {"error": "Cancelled by user"}
                yield AgentEvent(
                    kind="tool_call_end",
                    data={
                        "id": tc["id"],
                        "tool_name": tc["function_name"],
                        "output": cancelled_result,
                        "status": "cancelled",
                    },
                )
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(cancelled_result)})
        else:
            tasks = [asyncio.create_task(_execute_tool(tc, tool_executor, cancel_event)) for tc in tool_calls_pending]
            for coro in asyncio.as_completed(tasks):
                tc, result, status = await coro
                yield AgentEvent(
                    kind="tool_call_end",
                    data={"id": tc["id"], "tool_name": tc["function_name"], "output": result, "status": status},
                )
                # Strip internal metadata before sending to the LLM
                # _approval_decision: safety gate audit field
                # _old_content/_new_content: large strings for diff rendering only
                # _context_trust/_context_origin: trust classification metadata
                internal_keys = {
                    "_approval_decision",
                    "_old_content",
                    "_new_content",
                    "_context_trust",
                    "_context_origin",
                }
                if isinstance(result, dict):
                    # Scan untrusted tool output for prompt injection
                    if injection_detector is not None and injection_detector.enabled:
                        # Collect all string values from the result (covers content,
                        # result, stdout, stderr, text — any tool output shape)
                        _tool_text = "\n".join(
                            v for k, v in result.items() if isinstance(v, str) and not k.startswith("_")
                        )
                        if _tool_text:
                            _inj_verdict = injection_detector.scan_tool_output(tc["function_name"], _tool_text)
                            if _inj_verdict.detected:
                                yield AgentEvent(
                                    kind="injection_detected",
                                    data={
                                        "tool_name": tc["function_name"],
                                        "technique": _inj_verdict.technique,
                                        "confidence": _inj_verdict.confidence,
                                        "detail": _inj_verdict.detail,
                                        "action": injection_detector.action,
                                    },
                                )
                                if injection_detector.action == "block":
                                    result = {
                                        "error": "Tool output blocked: prompt injection detected",
                                        "safety_blocked": True,
                                        "_approval_decision": "injection_blocked",
                                    }
                    # Wrap untrusted tool results in a defensive envelope
                    context_trust = result.get("_context_trust")
                    if context_trust == "untrusted":
                        origin = result.get("_context_origin", "unknown")
                        for key in ("content", "result"):
                            if key in result and isinstance(result[key], str):
                                result[key] = wrap_untrusted(result[key], origin, "tool-result")
                                break
                    llm_result = {k: v for k, v in result.items() if k not in internal_keys}
                else:
                    llm_result = result
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(llm_result),
                    }
                )
            total_tool_calls += len(tool_calls_pending)

        if cancel_event and cancel_event.is_set():
            yield AgentEvent(kind="done", data={})
            return

        # Auto-plan suggestion: one-shot event when tool calls cross the threshold.
        if (
            auto_plan_threshold > 0
            and not auto_plan_suggested
            and total_tool_calls >= auto_plan_threshold
            and not (cancel_event and cancel_event.is_set())
        ):
            auto_plan_suggested = True
            yield AgentEvent(kind="auto_plan_suggest", data={"tool_calls": total_tool_calls})

        # Enforce narration cadence: inject an ephemeral prompt to force a progress update.
        # The injected message is removed from history immediately after the narration response
        # so it does not pollute the conversation context for subsequent tool calls.
        if (
            narration_cadence > 0
            and total_tool_calls > 0
            and total_tool_calls % narration_cadence == 0
            and not (cancel_event and cancel_event.is_set())
        ):
            yield AgentEvent(kind="thinking", data={})
            narration_idx = len(messages)
            messages.append({"role": "user", "content": _NARRATION_PROMPT})
            try:
                async for event in ai_service.stream_chat(
                    messages,
                    cancel_event=cancel_event,
                    extra_system_prompt=extra_system_prompt,
                ):
                    if event["event"] == "token":
                        yield AgentEvent(kind="token", data=event["data"])
                    elif event["event"] in ("done", "error"):
                        break
            except Exception:
                logger.exception("Narration request failed; continuing without update")
            finally:
                # Remove by index — safer than content equality if stream_chat mutated messages
                if len(messages) > narration_idx:
                    messages.pop(narration_idx)

        assistant_content = ""

    yield AgentEvent(kind="error", data={"message": f"Max iterations ({max_iterations}) reached"})
