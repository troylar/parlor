"""ask_user tool — pauses the agent loop to ask the user a question (#299)."""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

AskCallback = Callable[[str], Coroutine[Any, Any, str]]

DEFINITION: dict[str, Any] = {
    "name": "ask_user",
    "description": (
        "Ask the user a question and wait for their response. "
        "Use this when you need information you cannot infer from context, tools, or prior conversation. "
        "Do NOT ask questions in your text output — use this tool instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user. Be specific and focused — ask one thing at a time.",
            },
        },
        "required": ["question"],
    },
}


async def handle(question: str, _ask_callback: AskCallback | None = None, **_: Any) -> dict[str, Any]:
    """Prompt the user and return their answer.

    The _ask_callback is injected by the tool_executor at call time.
    """
    if not question or not question.strip():
        return {"error": "Question cannot be empty"}

    if _ask_callback is None:
        logger.warning("ask_user called with no callback — failing closed")
        return {
            "error": "No interactive input available. Make your best judgment and proceed.",
        }

    try:
        answer = await _ask_callback(question.strip())
        return {"answer": answer}
    except (EOFError, KeyboardInterrupt):
        return {"answer": "", "note": "User cancelled the prompt"}
    except Exception as e:
        logger.error("ask_user callback failed: %s", e)
        return {"error": f"Failed to get user input: {type(e).__name__}"}
