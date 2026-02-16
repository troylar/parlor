"""Markdown export for conversations."""

from __future__ import annotations

import json
from typing import Any


def export_conversation_markdown(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append(f"# {conversation['title']}")
    lines.append("")
    lines.append(f"*Exported from AI Chat | Created: {conversation['created_at']}*")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"## {role_label}")
        lines.append("")
        lines.append(msg["content"])
        lines.append("")

        attachments = msg.get("attachments", [])
        if attachments:
            lines.append("**Attachments:**")
            for att in attachments:
                lines.append(f"- {att['filename']} ({att['mime_type']}, {att['size_bytes']} bytes)")
            lines.append("")

        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                lines.append(f"**Tool Call: {tc['tool_name']}** ({tc['status']})")
                lines.append("")
                lines.append("Input:")
                lines.append("```json")
                lines.append(json.dumps(tc["input"], indent=2))
                lines.append("```")
                if tc.get("output"):
                    lines.append("")
                    lines.append("Output:")
                    lines.append("```json")
                    lines.append(json.dumps(tc["output"], indent=2))
                    lines.append("```")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)
