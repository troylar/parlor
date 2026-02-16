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

## Thinking Indicator

Between tool execution and the next API call, Anteroom emits a `"thinking"` event that triggers a pulsing dots animation in the UI. This provides visual feedback that the AI is processing tool results before continuing.
