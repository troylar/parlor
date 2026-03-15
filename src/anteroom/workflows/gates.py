"""Gate conditions for built-in workflows.

These are domain-specific — they know about GitHub issues, plans, etc.
They are registered by name and called by the engine's generic gate handler.
The engine core has no knowledge of what these conditions check.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def issue_is_current(run: dict[str, Any], step: Any, inputs: dict[str, Any]) -> bool:
    """Check that the target issue is still open via gh CLI.

    Domain-specific gate for issue_delivery workflow.
    """
    issue_number = inputs.get("issue_number") or run.get("target_ref")
    if not issue_number:
        logger.warning("issue_is_current: no issue_number in inputs or target_ref")
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--json",
            "state",
            "--jq",
            ".state",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        state = stdout.decode().strip().lower()
        return state == "open"
    except Exception:
        logger.warning("issue_is_current: failed to check issue %s", issue_number, exc_info=True)
        return False


async def plan_is_approved(run: dict[str, Any], step: Any, inputs: dict[str, Any]) -> bool:
    """Check that a plan exists and is approved for the issue.

    Domain-specific gate for issue_delivery workflow.
    Checks for the 'senior-approved' label on the issue.
    """
    issue_number = inputs.get("issue_number") or run.get("target_ref")
    if not issue_number:
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--json",
            "labels",
            "--jq",
            '[.labels[].name] | any(. == "senior-approved")',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return stdout.decode().strip().lower() == "true"
    except Exception:
        logger.warning("plan_is_approved: failed to check issue %s", issue_number, exc_info=True)
        return False


async def always_pass(run: dict[str, Any], step: Any, inputs: dict[str, Any]) -> bool:
    """Gate that always passes. Useful for demos and testing."""
    return True


def register_builtin_gates() -> None:
    """Register all built-in gate conditions."""
    from anteroom.services.workflow_engine import register_gate_condition

    register_gate_condition("issue_is_current", issue_is_current)
    register_gate_condition("plan_is_approved", plan_is_approved)
    register_gate_condition("always_pass", always_pass)
