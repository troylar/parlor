"""Key binding setup for the CLI REPL."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings


def _is_paste(last_text_change: float, threshold: float = 0.05) -> bool:
    """Return True if Enter arrived fast enough after last buffer change to be paste."""
    return (time.monotonic() - last_text_change) < threshold


@dataclass
class KeybindingState:
    """Mutable state shared between keybinding handlers and the REPL loop."""

    exit_flag_value: list[bool] = field(default_factory=lambda: [False])
    last_text_change: list[float] = field(default_factory=lambda: [0.0])
    last_ctrl_c: list[float] = field(default_factory=lambda: [0.0])
    agent_busy: asyncio.Event = field(default_factory=asyncio.Event)
    current_cancel_event: list[asyncio.Event | None] = field(default_factory=lambda: [None])


def create_keybindings(state: KeybindingState) -> KeyBindings:
    """Create REPL key bindings wired to the given shared state.

    Returns a KeyBindings instance with Enter (submit/paste), Alt+Enter (newline),
    Ctrl+C (clear/exit), and Escape (cancel agent) handlers.
    """
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event: Any) -> None:
        if _is_paste(state.last_text_change[0]):
            event.current_buffer.insert_text("\n")
        else:
            event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    @kb.add("c-j")
    def _newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    @kb.add("c-c")
    def _handle_ctrl_c(event: Any) -> None:
        buf = event.current_buffer
        now = time.monotonic()
        if buf.text:
            buf.reset()
            state.last_ctrl_c[0] = now
        elif now - state.last_ctrl_c[0] < 2.0:
            state.exit_flag_value[0] = True
            buf.validate_and_handle()
        else:
            state.exit_flag_value[0] = True
            buf.validate_and_handle()

    @kb.add("escape", filter=Condition(lambda: state.agent_busy.is_set()))
    def _cancel_on_escape(event: Any) -> None:
        ce = state.current_cancel_event[0]
        if ce is not None:
            ce.set()

    return kb


def on_buffer_change(state: KeybindingState) -> Any:
    """Return a callback for buffer text changes that updates paste detection timing."""

    def _on_change(_buf: Any) -> None:
        state.last_text_change[0] = time.monotonic()

    return _on_change


def patch_shift_enter() -> None:
    """Map Shift+Enter (CSI u) to Ctrl+J for terminals with kitty keyboard protocol."""
    try:
        from prompt_toolkit.input import vt100_parser

        vt100_parser.ANSI_SEQUENCES["\x1b[13;2u"] = "c-j"
    except Exception:
        pass
