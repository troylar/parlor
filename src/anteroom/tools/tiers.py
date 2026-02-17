"""Tool risk tiers and approval mode logic.

Pure functions — no I/O, no side effects.
"""

from __future__ import annotations

from enum import IntEnum


class ToolTier(IntEnum):
    """Risk tier for tools. Higher value = more dangerous."""

    READ = 0
    WRITE = 1
    EXECUTE = 2
    DESTRUCTIVE = 3


class ApprovalMode(IntEnum):
    """Approval mode controlling which tiers require approval.

    The integer value represents the minimum tier that triggers approval.
    """

    AUTO = 99  # nothing triggers approval
    ASK_FOR_DANGEROUS = ToolTier.DESTRUCTIVE  # only destructive
    ASK_FOR_WRITES = ToolTier.WRITE  # write, execute, destructive
    ASK = ToolTier.WRITE  # same threshold as ask_for_writes


APPROVAL_MODE_NAMES: dict[str, ApprovalMode] = {
    "auto": ApprovalMode.AUTO,
    "ask_for_dangerous": ApprovalMode.ASK_FOR_DANGEROUS,
    "ask_for_writes": ApprovalMode.ASK_FOR_WRITES,
    "ask": ApprovalMode.ASK,
}

DEFAULT_TOOL_TIERS: dict[str, ToolTier] = {
    "read_file": ToolTier.READ,
    "glob_files": ToolTier.READ,
    "grep": ToolTier.READ,
    "write_file": ToolTier.WRITE,
    "edit_file": ToolTier.WRITE,
    "create_canvas": ToolTier.WRITE,
    "update_canvas": ToolTier.WRITE,
    "patch_canvas": ToolTier.WRITE,
    "bash": ToolTier.EXECUTE,
}

# MCP tools and unknown tools default to this tier
DEFAULT_MCP_TIER = ToolTier.EXECUTE


def get_tool_tier(
    tool_name: str,
    tier_overrides: dict[str, str] | None = None,
) -> ToolTier:
    """Look up the risk tier for a tool.

    Priority: config overrides > DEFAULT_TOOL_TIERS > DEFAULT_MCP_TIER.
    """
    if tier_overrides and tool_name in tier_overrides:
        raw = tier_overrides[tool_name].upper()
        try:
            return ToolTier[raw]
        except KeyError:
            pass

    return DEFAULT_TOOL_TIERS.get(tool_name, DEFAULT_MCP_TIER)


def parse_approval_mode(raw: str) -> ApprovalMode:
    """Parse an approval mode string. Returns ASK_FOR_WRITES on invalid input."""
    return APPROVAL_MODE_NAMES.get(raw.lower().strip(), ApprovalMode.ASK_FOR_WRITES)


def should_require_approval(
    tool_name: str,
    tool_tier: ToolTier,
    mode: ApprovalMode,
    allowed_tools: set[str] | None = None,
    denied_tools: set[str] | None = None,
    session_allowed: set[str] | None = None,
) -> bool | None:
    """Determine whether a tool call requires approval.

    Returns:
        True  — requires approval
        False — auto-allowed, skip approval
        None  — hard-denied (denied_tools), block without prompt
    """
    if denied_tools and tool_name in denied_tools:
        return None  # hard deny

    if allowed_tools and tool_name in allowed_tools:
        return False

    if session_allowed and tool_name in session_allowed:
        return False

    if mode == ApprovalMode.AUTO:
        return False

    return tool_tier >= mode
