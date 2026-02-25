"""Dialog helpers for the CLI REPL."""

from __future__ import annotations


async def show_help_dialog() -> None:
    """Show help in a floating dialog that doesn't disturb scrollback."""
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.shortcuts import message_dialog
    from prompt_toolkit.styles import Style

    cmd = "#C5A059 bold"
    desc = "#94A3B8"
    help_text = FormattedText(
        [
            ("bold", " Conversations\n"),
            (cmd, "  /new"),
            (desc, "              Start a new chat conversation\n"),
            (cmd, "  /new note <t>"),
            (desc, "     Start a new note\n"),
            (cmd, "  /new doc <t>"),
            (desc, "      Start a new document\n"),
            (cmd, "  /append <text>"),
            (desc, "    Add entry to current note\n"),
            (cmd, "  /last"),
            (desc, "             Resume the most recent conversation\n"),
            (cmd, "  /list [N]"),
            (desc, "         Show recent conversations (default 20)\n"),
            (cmd, "  /search <query>"),
            (desc, "   Search conversations by content\n"),
            (cmd, "  /resume [N|id|slug]"),
            (desc, " Resume (picker if no arg)\n"),
            (cmd, "  /delete <N|id|slug>"),
            (desc, " Delete a conversation\n"),
            (cmd, "  /rename [N|id|slug] <title>"),
            (desc, "\n                          Rename a conversation\n"),
            (cmd, "  /slug [name]"),
            (desc, "        Show or set conversation slug\n"),
            (cmd, "  /rewind"),
            (desc, "           Roll back to an earlier message\n"),
            ("", "\n"),
            ("bold", " Session\n"),
            (cmd, "  /compact"),
            (desc, "          Summarize history to free context\n"),
            (cmd, "  /model <name>"),
            (desc, "     Switch AI model mid-session\n"),
            (cmd, "  /tools"),
            (desc, "            List available tools\n"),
            (cmd, "  /skills"),
            (desc, "           List loaded skills\n"),
            (cmd, "  /mcp"),
            (desc, "              Show MCP server status\n"),
            (cmd, "  /conventions"),
            (desc, "      Show loaded ANTEROOM.md conventions\n"),
            (cmd, "  /plan"),
            (desc, "             Plan mode: on/approve/status/off\n"),
            (cmd, "  /verbose"),
            (desc, "          Cycle: compact > detailed > verbose\n"),
            (cmd, "  /detail"),
            (desc, "           Replay last turn's tool calls\n"),
            (cmd, "  /usage"),
            (desc, "            Show token usage statistics\n"),
            ("", "\n"),
            ("bold", " Input\n"),
            (cmd, "  /upload <path>"),
            (desc, "    Upload a file to the conversation\n"),
            (cmd, "  @<path>"),
            (desc, "           Include file contents inline\n"),
            (cmd, "  Alt+Enter"),
            (desc, "         Insert newline\n"),
            (cmd, "  Escape"),
            (desc, "            Cancel AI generation\n"),
            (cmd, "  /quit"),
            (desc, " \u00b7 "),
            (cmd, "Ctrl+D"),
            (desc, "      Exit\n"),
        ]
    )
    dialog_style = Style.from_dict(
        {
            "dialog": "bg:#1a1a2e",
            "dialog frame.label": "bg:#1a1a2e #C5A059 bold",
            "dialog.body": "bg:#1a1a2e #e0e0e0",
            "dialog shadow": "bg:#0a0a15",
            "button": "bg:#C5A059 #1a1a2e",
            "button.focused": "bg:#e0c070 #1a1a2e bold",
        }
    )
    await message_dialog(
        title="Help",
        text=help_text,
        ok_text="Close",
        style=dialog_style,
    ).run_async()
