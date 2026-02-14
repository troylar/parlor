"""Rich-based terminal output for the CLI chat."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.status import Status
from rich.text import Text

console = Console(stderr=True)
# Separate console for stdout markdown rendering (not stderr)
_stdout_console = Console()
_stdout = sys.stdout

# Response buffer (tokens collected silently, rendered on completion)
_streaming_buffer: list[str] = []

# Spinner state
_thinking_start: float = 0
_spinner: Status | None = None
_last_spinner_update: float = 0


def start_thinking() -> None:
    """Show a spinner with timer while AI is generating."""
    global _thinking_start, _spinner, _last_spinner_update
    _thinking_start = time.monotonic()
    _last_spinner_update = _thinking_start
    _spinner = Status("Thinking...", console=console, spinner="dots")
    _spinner.start()


def update_thinking() -> None:
    """Update the spinner timer (throttled to once per second)."""
    global _last_spinner_update
    if _spinner:
        now = time.monotonic()
        if now - _last_spinner_update >= 1.0:
            elapsed = now - _thinking_start
            _spinner.update(f"Thinking... ({elapsed:.0f}s)")
            _last_spinner_update = now


def stop_thinking() -> float:
    """Stop the spinner, return elapsed seconds."""
    global _spinner
    elapsed = 0.0
    if _spinner:
        elapsed = time.monotonic() - _thinking_start
        _spinner.stop()
        _spinner = None
    return elapsed


def render_token(content: str) -> None:
    """Buffer token content silently (no streaming output)."""
    _streaming_buffer.append(content)


def render_response_end() -> None:
    """Render the complete buffered response with Rich Markdown."""
    global _streaming_buffer
    full_text = "".join(_streaming_buffer)
    _streaming_buffer = []

    if not full_text.strip():
        return

    from rich.markdown import Markdown
    from rich.padding import Padding

    _stdout_console.print(Padding(Markdown(full_text), (0, 2, 0, 2)))


def render_newline() -> None:
    _stdout.write("\n")
    _stdout.flush()


def render_tool_call_start(tool_name: str, arguments: dict[str, Any]) -> None:
    args_str = json.dumps(arguments, indent=None, default=str)
    if len(args_str) > 200:
        args_str = args_str[:200] + "..."
    console.print(f"\n  [grey62]> {escape(tool_name)}({escape(args_str)})[/grey62]")


def render_tool_call_end(tool_name: str, status: str, output: Any) -> None:
    if status == "success":
        style = "green"
    else:
        style = "red"

    output_str = ""
    if isinstance(output, dict):
        if "error" in output:
            output_str = f" - {output['error']}"
        elif "content" in output:
            content = output["content"]
            if isinstance(content, str) and len(content) > 200:
                content = content[:200] + "..."
            output_str = f" - {content}"
        elif "stdout" in output:
            stdout = output["stdout"]
            if stdout and len(stdout) > 200:
                stdout = stdout[:200] + "..."
            output_str = f" - {stdout}" if stdout else ""
    text = Text(
        f"  < {tool_name}: {status}{output_str}",
        style=style,
    )
    console.print(text)


def render_error(message: str) -> None:
    console.print(f"\n[red bold]Error:[/red bold] {escape(message)}")


def render_welcome(
    model: str,
    tool_count: int,
    instructions_loaded: bool,
    working_dir: str,
    git_branch: str | None = None,
) -> None:
    console.print(f"\n[bold]Parlor CLI[/bold] - {escape(working_dir)}")
    inst = "loaded" if instructions_loaded else "none"
    branch_info = f" | Branch: {git_branch}" if git_branch else ""
    console.print(
        f"  Model: {escape(model)} | Tools: {tool_count}"
        f" | Instructions: {inst}{branch_info}"
    )
    console.print(
        "  Type [bold]/help[/bold] for commands, "
        "[bold]Ctrl+D[/bold] to exit\n"
    )


def render_help() -> None:
    console.print("\n[bold]Commands:[/bold]")
    console.print("  /new        - Start a new conversation")
    console.print("  /last       - Resume the most recent conversation")
    console.print("  /list       - Show recent conversations")
    console.print("  /resume N   - Resume conversation by number or ID")
    console.print("  /compact    - Summarize and compact message history")
    console.print("  /tools      - List available tools")
    console.print("  /skills     - List available skills")
    console.print("  /model NAME - Switch to a different model")
    console.print("  /quit       - Exit")
    console.print("  Ctrl+C      - Cancel current response")
    console.print("  Ctrl+D      - Exit")
    console.print("\n[bold]Input:[/bold]")
    console.print("  @<path>     - Reference a file or directory")
    console.print("  /<skill>    - Run a registered skill")
    console.print("  Alt+Enter   - Newline (multiline input)\n")


def render_tools(tool_names: list[str]) -> None:
    console.print("\n[bold]Available tools:[/bold]")
    for name in sorted(tool_names):
        console.print(f"  - {name}")
    console.print()


def render_compact_done(original: int, compacted: int) -> None:
    console.print(
        f"\n[grey62]Compacted {original} messages -> {compacted} messages[/grey62]"
    )


def render_context_footer(
    current_tokens: int,
    auto_compact_threshold: int,
    response_tokens: int = 0,
    elapsed: float = 0.0,
    max_context: int = 128_000,
) -> None:
    """Render a footer showing context usage after each response."""
    pct_full = min(100, (current_tokens / max_context) * 100)
    tokens_remaining = auto_compact_threshold - current_tokens

    if pct_full > 75:
        color = "red"
    elif pct_full > 50:
        color = "yellow"
    else:
        color = "grey62"

    bar_width = 20
    filled = int(bar_width * pct_full / 100)
    bar = "=" * filled + "-" * (bar_width - filled)

    elapsed_info = f" | {elapsed:.1f}s" if elapsed > 0 else ""
    resp_info = f" | response: {response_tokens:,}" if response_tokens else ""
    console.print(
        f"[{color}]  [{bar}] {current_tokens:,}/{max_context:,} "
        f"tokens ({pct_full:.0f}%){resp_info}{elapsed_info}"
        f" | {tokens_remaining:,} until auto-compact[/{color}]"
    )


