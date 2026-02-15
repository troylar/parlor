"""Rich-based terminal output for the CLI chat."""

from __future__ import annotations

import json
import os
import sys
import time
from enum import Enum
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.status import Status
from rich.text import Text

console = Console(stderr=True)
# Separate console for stdout markdown rendering (not stderr)
_stdout_console = Console()
_stdout = sys.stdout


def use_stdout_console() -> None:
    """Switch renderer to REPL-compatible mode.

    - Opens a duplicate of the real stderr file descriptor so Rich output
      bypasses prompt_toolkit's ``patch_stdout`` proxy entirely.  The proxy
      corrupts ANSI escape bytes; a raw fd duplicate does not.
    - Disables the animated spinner (Rich Live/Status) whose cursor
      manipulation conflicts with prompt_toolkit's terminal management.
      A static "Thinking..." line is printed instead.

    Call from inside ``patch_stdout()`` context.
    """
    global console, _stdout_console, _stdout, _repl_mode
    _real_stderr = os.fdopen(os.dup(sys.stderr.fileno()), "w")
    console = Console(file=_real_stderr, force_terminal=True)
    _stdout_console = Console(file=_real_stderr, force_terminal=True)
    _stdout = _real_stderr
    _repl_mode = True


# Response buffer (tokens collected silently, rendered on completion)
_streaming_buffer: list[str] = []

# Spinner state
_thinking_start: float = 0
_spinner: Status | None = None
_last_spinner_update: float = 0

# Tool call timing
_tool_start: float = 0

# Dedup tracking for repeated identical tool results
_dedup_summary: str = ""
_dedup_count: int = 0


# ---------------------------------------------------------------------------
# Verbosity
# ---------------------------------------------------------------------------


class Verbosity(Enum):
    COMPACT = "compact"
    DETAILED = "detailed"
    VERBOSE = "verbose"


_verbosity: Verbosity = Verbosity.COMPACT

# Tool call history for /detail replay
_tool_history: list[dict[str, Any]] = []
_current_turn_tools: list[dict[str, Any]] = []


def get_verbosity() -> Verbosity:
    return _verbosity


def set_verbosity(v: Verbosity) -> None:
    global _verbosity
    _verbosity = v


def cycle_verbosity() -> Verbosity:
    global _verbosity
    order = [Verbosity.COMPACT, Verbosity.DETAILED, Verbosity.VERBOSE]
    idx = order.index(_verbosity)
    _verbosity = order[(idx + 1) % len(order)]
    return _verbosity


def clear_turn_history() -> None:
    """Clear current turn tool history. Called at start of each turn."""
    _current_turn_tools.clear()


def save_turn_history() -> None:
    """Save current turn tools to history. Called at end of each turn."""
    _flush_dedup()
    if _current_turn_tools:
        _tool_history.clear()
        _tool_history.extend(_current_turn_tools)


# ---------------------------------------------------------------------------
# Tool call summary helpers
# ---------------------------------------------------------------------------


def _humanize_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """Convert tool_name + args into a human-readable breadcrumb."""
    name_lower = tool_name.lower()

    # Built-in tools: extract the key argument
    if name_lower == "bash":
        cmd = arguments.get("command", "")
        if len(cmd) > 100:
            cmd = cmd[:97] + "..."
        return f"bash {cmd}"
    elif name_lower in ("file_read", "read_file"):
        path = arguments.get("path", arguments.get("file_path", ""))
        return f"Reading {_short_path(path)}"
    elif name_lower in ("file_write", "write_file"):
        path = arguments.get("path", arguments.get("file_path", ""))
        return f"Writing {_short_path(path)}"
    elif name_lower in ("file_edit", "edit_file"):
        path = arguments.get("path", arguments.get("file_path", ""))
        return f"Editing {_short_path(path)}"
    elif name_lower in ("grep", "search", "ripgrep"):
        pattern = arguments.get("pattern", arguments.get("query", ""))
        return f"Searching for '{pattern}'"
    elif name_lower in ("glob", "find_files"):
        pattern = arguments.get("pattern", "")
        return f"Finding {pattern}"
    elif name_lower == "list_directory":
        path = arguments.get("path", ".")
        return f"Listing {_short_path(path)}"

    # MCP / unknown tools: show name + first string arg
    first_str = ""
    for v in arguments.values():
        if isinstance(v, str) and v:
            first_str = v
            if len(first_str) > 40:
                first_str = first_str[:37] + "..."
            break
    if first_str:
        return f"{tool_name} {first_str}"
    return tool_name


def _short_path(path: str) -> str:
    """Shorten absolute path using ~ for home and cwd-relative."""
    if not path:
        return path
    home = os.path.expanduser("~")
    cwd = os.getcwd()
    # Try cwd-relative first
    try:
        rel = os.path.relpath(path, cwd)
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    # Fall back to ~-relative
    if path.startswith(home):
        return "~" + path[len(home) :]
    return path


def _format_tokens(n: int) -> str:
    """Format token count: 1234 -> '1.2k', 128000 -> '128k'."""
    if n >= 1000:
        k = n / 1000
        if k >= 10:
            return f"{k:.0f}k"
        return f"{k:.1f}k"
    return str(n)


def _error_summary(output: Any) -> str:
    """Extract a one-line error summary from tool output."""
    if not isinstance(output, dict):
        return ""
    err = output.get("error", "")
    if err:
        # First line only, truncated
        first_line = str(err).split("\n")[0]
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        return first_line
    return ""


def _output_summary(output: Any) -> str:
    """Extract a brief output summary for detailed mode."""
    if not isinstance(output, dict):
        return ""
    if "error" in output:
        return _error_summary(output)
    if "content" in output:
        content = output["content"]
        if isinstance(content, str):
            lines = content.count("\n") + 1
            chars = len(content)
            if chars > 80:
                return f"{lines} lines, {chars:,} chars"
            # Short enough to show inline
            oneline = content.replace("\n", " ").strip()
            if len(oneline) > 60:
                return oneline[:57] + "..."
            return oneline
    if "stdout" in output:
        stdout = output.get("stdout", "")
        if stdout:
            lines = stdout.count("\n") + 1
            oneline = stdout.split("\n")[0].strip()
            if lines > 1:
                if len(oneline) > 40:
                    oneline = oneline[:37] + "..."
                return f"{oneline} (+{lines - 1} lines)"
            if len(oneline) > 60:
                return oneline[:57] + "..."
            return oneline
    return ""


# ---------------------------------------------------------------------------
# Thinking spinner
# ---------------------------------------------------------------------------


_repl_mode: bool = False


def start_thinking() -> None:
    """Show a spinner with timer while AI is generating."""
    global _thinking_start, _spinner, _last_spinner_update
    _thinking_start = time.monotonic()
    _last_spinner_update = _thinking_start
    if _repl_mode:
        # Rich Status conflicts with prompt_toolkit's patch_stdout, so
        # we write a plain "Thinking..." line and overwrite it in-place
        # via ANSI escape codes as the timer ticks.
        _write_thinking_line(0.0)
        _spinner = None
    else:
        _spinner = Status("[#C5A059]Thinking...[/]", console=console, spinner="dots12")
        _spinner.start()


def _write_thinking_line(elapsed: float) -> None:
    """Overwrite the current line with Thinking + elapsed timer."""
    if elapsed < 0.5:
        text = "\r\033[2K\033[38;2;197;160;89mThinking...\033[0m"
    else:
        text = f"\r\033[2K\033[38;2;197;160;89mThinking...\033[0m \033[38;2;71;85;105m{elapsed:.0f}s\033[0m"
    if _stdout:
        _stdout.write(text)
        _stdout.flush()


def update_thinking() -> None:
    """Update the spinner timer (throttled to once per second)."""
    global _last_spinner_update
    if _spinner:
        now = time.monotonic()
        if now - _last_spinner_update >= 1.0:
            elapsed = now - _thinking_start
            _spinner.update(f"[#C5A059]Thinking...[/] [grey62]{elapsed:.0f}s[/grey62]")
            _last_spinner_update = now
    elif _repl_mode:
        now = time.monotonic()
        if now - _last_spinner_update >= 1.0:
            elapsed = now - _thinking_start
            _write_thinking_line(elapsed)
            _last_spinner_update = now


def stop_thinking() -> float:
    """Stop the spinner, return elapsed seconds."""
    global _spinner
    elapsed = 0.0
    if _spinner:
        elapsed = time.monotonic() - _thinking_start
        _spinner.stop()
        _spinner = None
    else:
        elapsed = time.monotonic() - _thinking_start
        if _repl_mode and _stdout:
            _stdout.write("\r\033[2K")
            _stdout.flush()
    return elapsed


# ---------------------------------------------------------------------------
# Token / response rendering
# ---------------------------------------------------------------------------


def _make_markdown(text: str) -> Markdown:
    """Create a Markdown renderable with left-aligned headings."""
    _patch_heading_left()
    return Markdown(text)


_heading_patched = False


def _patch_heading_left() -> None:
    """Monkey-patch Rich's Heading to render left-aligned instead of centered."""
    global _heading_patched
    if _heading_patched:
        return
    from rich.markdown import Heading

    def _left_aligned(self, console, options):
        self.text.justify = "left"
        if self.tag == "h2":
            yield Text("")
        yield self.text

    Heading.__rich_console__ = _left_aligned
    _heading_patched = True


def flush_buffered_text() -> None:
    """Flush any buffered AI text to screen immediately.

    Called before tool calls start so the AI's task explanation
    (e.g. 'Let me review your auth files') renders before the tool output.
    """
    global _streaming_buffer
    text = "".join(_streaming_buffer)
    _streaming_buffer = []
    if not text.strip():
        return
    from rich.padding import Padding

    _stdout_console.print(Padding(_make_markdown(text), (0, 2, 0, 2)))


def _flush_dedup() -> None:
    """Flush accumulated dedup counter if needed."""
    global _dedup_summary, _dedup_count
    if _dedup_count > 1:
        console.print(f"    [dim]... repeated {_dedup_count} times total[/dim]")
    _dedup_summary = ""
    _dedup_count = 0


def render_token(content: str) -> None:
    """Buffer token content silently (no streaming output)."""
    _streaming_buffer.append(content)


def render_response_end() -> None:
    """Render the complete buffered response with Rich Markdown."""
    global _streaming_buffer
    _flush_dedup()
    full_text = "".join(_streaming_buffer)
    _streaming_buffer = []

    if not full_text.strip():
        return

    from rich.padding import Padding

    _stdout_console.print(Padding(_make_markdown(full_text), (0, 2, 0, 2)))


def render_newline() -> None:
    _stdout.write("\n")
    _stdout.flush()


# ---------------------------------------------------------------------------
# Tool call rendering (verbosity-aware)
# ---------------------------------------------------------------------------


def render_tool_call_start(tool_name: str, arguments: dict[str, Any]) -> None:
    """Show tool call breadcrumb. Static print (no live spinner) for terminal compatibility."""
    global _tool_start

    # Flush any buffered AI text so task explanations appear before tool output
    flush_buffered_text()

    summary = _humanize_tool(tool_name, arguments)

    # Store for history
    _current_turn_tools.append(
        {
            "tool_name": tool_name,
            "arguments": arguments,
            "summary": summary,
            "status": "running",
            "output": None,
        }
    )

    _tool_start = time.monotonic()

    if _verbosity == Verbosity.VERBOSE:
        # Full output: tool name + raw args
        args_str = json.dumps(arguments, indent=None, default=str)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        console.print(f"  [grey62]> {escape(tool_name)}({escape(args_str)})[/grey62]")


def render_tool_call_end(tool_name: str, status: str, output: Any) -> None:
    """Show tool call result. Style depends on verbosity."""
    elapsed = time.monotonic() - _tool_start if _tool_start else 0

    # Update history
    if _current_turn_tools:
        _current_turn_tools[-1]["status"] = status
        _current_turn_tools[-1]["output"] = output
        _current_turn_tools[-1]["elapsed"] = elapsed

    summary = _current_turn_tools[-1]["summary"] if _current_turn_tools else tool_name

    if _verbosity == Verbosity.VERBOSE:
        # Legacy-style
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
        text = Text(f"  < {tool_name}: {status}{output_str}", style=style)
        console.print(text)
        return

    # Build the result line
    global _dedup_summary, _dedup_count
    status_icon = "[green]  ✓[/green]" if status == "success" else "[red]  ✗[/red]"
    elapsed_str = f" {elapsed:.1f}s" if elapsed >= 0.1 else ""

    # Dedup: collapse identical consecutive tool results (compact/detailed only)
    if status == "success" and summary == _dedup_summary:
        _dedup_count += 1
        return

    # Different tool or first occurrence — flush previous dedup, print new line
    _flush_dedup()

    if status != "success":
        console.print(f"{status_icon} {escape(summary)}{elapsed_str}")
        err = _error_summary(output)
        if err:
            console.print(f"    [red]{escape(err)}[/red]")
        _dedup_summary = ""
        _dedup_count = 0
    elif _verbosity == Verbosity.DETAILED:
        detail = _output_summary(output)
        console.print(f"{status_icon} {escape(summary)}{elapsed_str}")
        if detail:
            console.print(f"    [grey62]{escape(detail)}[/grey62]")
        _dedup_summary = summary
        _dedup_count = 1
    else:
        # Compact: just result line
        console.print(f"{status_icon} {escape(summary)}{elapsed_str}")
        _dedup_summary = summary
        _dedup_count = 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def render_error(message: str) -> None:
    console.print(f"\n[red bold]Error:[/red bold] {escape(message)}")


# ---------------------------------------------------------------------------
# Welcome / help
# ---------------------------------------------------------------------------


def _get_build_date() -> str:
    try:
        from datetime import datetime

        from .._build_info import BUILD_TIMESTAMP  # type: ignore[import-not-found]

        dt = datetime.fromisoformat(BUILD_TIMESTAMP)
        return dt.astimezone().strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return ""


_BOX_TOP = "\u256d" + "\u2500" * 29 + "\u256e"
_BOX_BOT = "\u2570" + "\u2500" * 29 + "\u256f"
_SEP = " \u00b7 "


def render_welcome(
    model: str,
    tool_count: int,
    instructions_loaded: bool,
    working_dir: str,
    git_branch: str | None = None,
    version: str = "",
    build_date: str = "",
) -> None:
    display_dir = _short_path(working_dir)
    branch = f" ({git_branch})" if git_branch else ""

    console.print()
    console.print(f"[#C5A059]  {_BOX_TOP}[/]")
    console.print("[#C5A059]  \u2502         [bold]P A R L O R[/bold]         \u2502[/]")
    console.print("[#C5A059]  \u2502    [#94A3B8]the digital speakeasy[/]    \u2502[/]")
    console.print(f"[#C5A059]  {_BOX_BOT}[/]")
    console.print()

    version_parts = []
    if version:
        version_parts.append(f"v{version}")
    if build_date:
        version_parts.append(f"Built {build_date}")
    if version_parts:
        console.print(f"  [dim]{_SEP.join(version_parts)}[/dim]")
    console.print("  [dim]github.com/troylar/parlor[/dim]")
    console.print()

    console.print(f"  [#94A3B8]{escape(display_dir)}{branch}[/]")
    parts = [escape(model), f"{tool_count} tools"]
    if instructions_loaded:
        parts.append("instructions")
    console.print(f"  [dim]{_SEP.join(parts)}[/dim]")
    console.print()
    console.print("  [dim]Type /help for commands[/dim]\n")


def render_update_available(current: str, latest: str) -> None:
    console.print(
        f"  [#C5A059]Update available:[/] [dim]{current} \u2192 {latest}[/dim]"
        " [dim]\u2014 pip install --upgrade parlor[/dim]\n"
    )


def render_help() -> None:
    console.print()
    console.print(" [bold]Conversations[/bold]  /new · /last · /list · /resume N · /rewind")
    console.print(" [bold]Context[/bold]        /compact · /model NAME")
    console.print(" [bold]Tools[/bold]          /tools · /skills · /mcp · /mcp status <name>")
    console.print(" [bold]Display[/bold]        /verbose · /detail")
    console.print(" [bold]Input[/bold]          @<path> file ref · Alt+Enter newline")
    console.print(" [bold]Exit[/bold]           /quit · Ctrl+D · Escape to cancel")
    console.print()


def render_tools(tool_names: list[str]) -> None:
    console.print("\n[bold]Available tools:[/bold]")
    for name in sorted(tool_names):
        console.print(f"  - {name}")
    console.print()


def render_compact_done(original: int, compacted: int) -> None:
    console.print(f"\n[grey62]Compacted {original} messages -> {compacted} messages[/grey62]")


# ---------------------------------------------------------------------------
# MCP status
# ---------------------------------------------------------------------------


def render_mcp_status(statuses: dict[str, dict[str, Any]]) -> None:
    """Render MCP server status as a Rich table."""
    from rich.table import Table

    if not statuses:
        console.print("\n[grey62]No MCP servers configured.[/grey62]\n")
        return

    table = Table(title="MCP Servers", show_header=True, header_style="bold")
    table.add_column("Server", style="cyan")
    table.add_column("Transport")
    table.add_column("Status")
    table.add_column("Tools", justify="right")

    for name, info in statuses.items():
        status = info.get("status", "unknown")
        if status == "connected":
            status_text = "[green]● connected[/green]"
        elif status == "error":
            err = info.get("error_message", "")
            status_text = "[red]● error[/red]"
            if err:
                # Truncate long error messages in table
                if len(err) > 40:
                    err = err[:37] + "..."
                status_text += f" [grey62]({err})[/grey62]"
        elif status == "disconnected":
            status_text = "[grey62]○ disconnected[/grey62]"
        else:
            status_text = f"[grey62]○ {status}[/grey62]"

        table.add_row(
            name,
            info.get("transport", "?"),
            status_text,
            str(info.get("tool_count", 0)),
        )

    console.print()
    console.print(table)
    console.print("  [grey62]Usage: /mcp [status <name>|connect|disconnect|reconnect <name>][/grey62]\n")


def render_mcp_server_detail(name: str, statuses: dict[str, dict[str, Any]], mcp_manager: Any) -> None:
    """Render detailed diagnostics for a single MCP server."""
    if name not in statuses:
        console.print(f"\n[red]Unknown server: {escape(name)}[/red]")
        known = ", ".join(statuses.keys())
        console.print(f"  [grey62]Available: {known}[/grey62]\n")
        return

    info = statuses[name]
    status = info.get("status", "unknown")

    if status == "connected":
        status_styled = "[green]● connected[/green]"
    elif status == "error":
        status_styled = "[red]● error[/red]"
    else:
        status_styled = f"[grey62]○ {status}[/grey62]"

    console.print(f"\n[bold]MCP Server: {escape(name)}[/bold]")
    console.print(f"  Status:    {status_styled}")
    console.print(f"  Transport: {info.get('transport', '?')}")

    config = mcp_manager._configs.get(name)
    if config:
        if config.command:
            cmd = f"{config.command} {' '.join(config.args)}" if config.args else config.command
            console.print(f"  Command:   {escape(cmd)}")
        if config.url:
            console.print(f"  URL:       {escape(config.url)}")
        if config.env:
            console.print(f"  Env keys:  {', '.join(config.env.keys())}")
        console.print(f"  Timeout:   {config.timeout}s")

    err = info.get("error_message")
    if err:
        console.print(f"  [red]Error:     {escape(err)}[/red]")

    tool_count = info.get("tool_count", 0)
    console.print(f"  Tools:     {tool_count}")
    if tool_count > 0:
        server_tools = mcp_manager._server_tools.get(name, [])
        for t in server_tools:
            desc = t.get("description", "")
            if desc and len(desc) > 60:
                desc = desc[:60] + "..."
            if desc:
                console.print(f"    - {t['name']} [grey62]({desc})[/grey62]")
            else:
                console.print(f"    - {t['name']}")

    console.print()


# ---------------------------------------------------------------------------
# /detail - replay last turn's tool calls with full output
# ---------------------------------------------------------------------------


def render_tool_detail() -> None:
    """Render full detail of the last turn's tool calls."""
    if not _tool_history:
        console.print("[grey62]No tool calls in the last turn.[/grey62]\n")
        return

    console.print(f"\n[bold]Last turn: {len(_tool_history)} tool call(s)[/bold]\n")
    for i, tc in enumerate(_tool_history, 1):
        status = tc.get("status", "unknown")
        elapsed = tc.get("elapsed", 0)
        status_icon = "[green]✓[/green]" if status == "success" else "[red]✗[/red]"
        elapsed_str = f" ({elapsed:.1f}s)" if elapsed >= 0.1 else ""

        console.print(f"  {status_icon} [bold]{escape(tc['tool_name'])}[/bold]{elapsed_str}")

        # Show full arguments
        args_str = json.dumps(tc["arguments"], indent=2, default=str)
        for line in args_str.split("\n"):
            console.print(f"    [dim]{escape(line)}[/dim]")

        # Show output
        output = tc.get("output")
        if output:
            if isinstance(output, dict):
                if "error" in output:
                    console.print(f"    [red]{escape(str(output['error'])[:500])}[/red]")
                elif "content" in output:
                    content = str(output["content"])
                    if len(content) > 500:
                        content = content[:500] + "..."
                    for line in content.split("\n")[:20]:
                        console.print(f"    [grey62]{escape(line)}[/grey62]")
                    total_lines = str(output["content"]).count("\n") + 1
                    if total_lines > 20:
                        console.print(f"    [dim]... ({total_lines - 20} more lines)[/dim]")
                elif "stdout" in output:
                    stdout = str(output.get("stdout", ""))
                    if len(stdout) > 500:
                        stdout = stdout[:500] + "..."
                    for line in stdout.split("\n")[:20]:
                        console.print(f"    [grey62]{escape(line)}[/grey62]")
            else:
                console.print(f"    [grey62]{escape(str(output)[:200])}[/grey62]")
        console.print()


# ---------------------------------------------------------------------------
# Verbosity display
# ---------------------------------------------------------------------------


def render_verbosity_change(v: Verbosity) -> None:
    labels = {
        Verbosity.COMPACT: "compact",
        Verbosity.DETAILED: "detailed",
        Verbosity.VERBOSE: "verbose",
    }
    console.print(f"[grey62]Verbosity: {labels[v]}[/grey62]\n")


# ---------------------------------------------------------------------------
# Context footer (compact)
# ---------------------------------------------------------------------------


def render_context_footer(
    current_tokens: int,
    auto_compact_threshold: int,
    response_tokens: int = 0,
    elapsed: float = 0.0,
    max_context: int = 128_000,
) -> None:
    """Render a compact footer showing context usage."""
    pct_full = min(100, (current_tokens / max_context) * 100)
    tokens_remaining = auto_compact_threshold - current_tokens

    if pct_full > 75:
        color = "red"
    elif pct_full > 50:
        color = "yellow"
    else:
        color = "grey62"

    parts = [f"{_format_tokens(current_tokens)}/{_format_tokens(max_context)} ({pct_full:.0f}%)"]
    if response_tokens:
        parts.append(f"{_format_tokens(response_tokens)} resp")
    if elapsed > 0:
        parts.append(f"{elapsed:.1f}s")
    if pct_full > 50:
        parts.append(f"compact in {_format_tokens(max(0, tokens_remaining))}")

    console.print(f"[{color}]  ▪ {' · '.join(parts)}[/{color}]")
