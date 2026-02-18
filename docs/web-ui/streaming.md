# Streaming and Prompt Queue

Anteroom streams AI responses in real-time via Server-Sent Events (SSE) and supports prompt queuing so you never have to wait.

## Token-by-Token Streaming

Responses arrive token-by-token and render live:

- Markdown and math render as tokens arrive
- **Raw mode toggle** (eye icon in top bar) --- view unprocessed text during streaming, persists across sessions
- Stop generation mid-response with `Escape` or the stop button
- Animated thinking indicator with pulsing dots while AI processes
- Error messages show inline with a **Retry** button

## Prompt Queue

Type and submit new messages while the AI is responding. They queue (up to 10) and process in FIFO order when the current response finishes.

When a stream is active for a conversation, new messages return `{"status": "queued"}` instead of opening a new SSE stream. The queued messages process automatically in order.

!!! tip
    This means you can rapid-fire multiple follow-up prompts without waiting for each response to complete.

## Parallel Tool Execution

When the AI calls multiple tools in one response, they run concurrently via `asyncio.as_completed` instead of sequentially. For example, if the AI calls `read_file` on three different files, all three reads happen simultaneously.

Tool calls render as expandable detail panels:

- Input parameters shown during execution
- Output and status shown when complete
- Spinner animation while tools execute

## Sub-Agent Loading Indicator

When the AI calls `run_agent` to spawn a sub-agent, the tool call panel renders with a distinctive loading state instead of the standard collapsed detail element:

- **Expanded by default** with a pulsing left-border accent animation
- **Loading prompt** shows the first 200 characters of the sub-agent's task
- **Spinner** indicates the sub-agent is actively running

As sub-agent events arrive, the loading prompt is replaced by per-agent progress cards:

| SSE Event | `kind` field | Payload fields |
|---|---|---|
| `subagent_event` | `subagent_start` | `agent_id`, `model`, `prompt` |
| `subagent_event` | `tool_call_start` | `agent_id`, `tool_name` |
| `subagent_event` | `subagent_end` | `agent_id`, `elapsed_seconds`, `tool_calls`, `error` |

On completion, the panel summary updates to "Sub-agent complete" (success) or "Sub-agent failed" (error), and the pulsing animation stops. Non-sub-agent tool calls are unaffected by these styles.

## Thinking Indicator

Between tool execution and the next API call, Anteroom emits a `"thinking"` event that triggers a pulsing dots animation in the UI. This provides visual feedback that the AI is processing tool results before continuing.

## Canvas Streaming

When the AI calls `create_canvas`, `update_canvas`, or `patch_canvas`, a panel appears alongside the chat and updates in real-time via the following SSE events:

| Event | When emitted | Payload fields |
|---|---|---|
| `canvas_stream_start` | AI begins generating canvas content | `canvas_id`, `title`, `content_type` |
| `canvas_streaming` | Each streamed content chunk | `canvas_id`, `delta` |
| `canvas_created` | `create_canvas` completes | `canvas_id`, `title`, `content` |
| `canvas_updated` | `update_canvas` completes | `canvas_id`, `content` |
| `canvas_patched` | `patch_canvas` completes | `canvas_id`, `edits_applied` |

Canvas content is streamed token-by-token using `tool_call_args_delta` events from `ai_service.py`, giving the same live-render experience as normal text responses.

## Safety Approval Events

When a destructive tool call is intercepted by the safety gate, the stream emits an `approval_required` event before pausing:

| Event | Payload fields |
|---|---|
| `approval_required` | `approval_id`, `tool_name`, `command` (or `path`) |
| `approval_resolved` | `approval_id`, `approved`, `reason` |

The browser renders an inline Approve / Deny prompt inside the tool call panel. The user's response is submitted to `POST /api/approvals/{approval_id}/respond`. See [Tool Safety](../security/tool-safety.md#web-ui-approval-flow) for the full flow.

**Deduplication**: The browser tracks shown approval IDs in a `_shownApprovalIds` set. Duplicate `approval_required` events for the same `approval_id` (e.g., on SSE reconnect) are ignored. On SSE reconnect, all stale approval prompts are cleared from the DOM and the set is reset.

**Resolution display**: When `approval_resolved` fires, the approval card updates to show "Allowed" (green) or "Denied" (red) status. Timed-out approvals show "Timed out".
