# Context Management

Anteroom tracks token usage across the conversation and automatically manages context window limits.

## Token Tracking

Token counting uses **tiktoken** (`cl100k_base` encoding) with a character-estimate fallback (1 token per 4 chars) when tiktoken is unavailable.

| Threshold | What happens |
|---|---|
| **80,000 tokens** | Yellow warning: "Use `/compact` to free space" |
| **100,000 tokens** | Auto-compact triggers automatically before the next prompt |
| **128,000 tokens** | Effective context window ceiling |

## Context Footer

After each response, the context footer shows:

```
  [====----------------] 12,340/128,000 tokens (10%) | response: 482 | 3.2s | 87,660 until auto-compact
```

- **Progress bar** with color coding: green (<50%), yellow (50--75%), red (>75%)
- Current token count / max context window
- Response token count for the current turn
- Total elapsed thinking time
- Remaining tokens until auto-compact fires

## Compact

Use `/compact` to manually summarize and compact the conversation history. Auto-compact triggers automatically at 100K tokens.

The compact process sends the full conversation history to the AI with a summarization prompt. The AI generates a concise summary preserving:

- Key decisions and conclusions
- File paths that were read, written, or edited
- Important code changes and their purpose
- Current task state
- Errors encountered and how they were resolved

Tool call outputs are truncated to 500 characters each in the summary prompt. After compacting, the message history is replaced with a single system message:

```
  Compacted 47 messages -> 1 messages
  ~32,140 -> ~890 tokens
```

!!! info
    Minimum 4 messages required to compact. This prevents compacting a nearly-empty conversation.
