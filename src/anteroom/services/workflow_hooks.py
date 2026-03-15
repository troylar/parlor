"""Notification hook delivery for workflow events.

Supports webhook (HTTP POST) and Unix socket transports. Webhook URLs
are validated against the egress allowlist at definition load time.
Hook delivery is best-effort — failures are logged, never raised.
A bounded drain ensures pending deliveries complete before process exit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Any, Sequence

logger = logging.getLogger(__name__)


def validate_hook_config(
    hooks: list[dict[str, Any]],
    allowed_domains: Sequence[str],
    block_localhost: bool = False,
) -> None:
    """Validate all webhook URLs against the egress allowlist at load time.

    Raises ValueError if any webhook URL fails validation. Unix socket
    hooks are local-only and not subject to egress allowlist.
    """
    from .egress_allowlist import check_egress_allowed

    for hook in hooks:
        transport = hook.get("transport", "")
        if transport == "webhook":
            url = hook.get("url", "")
            if not url:
                raise ValueError("Webhook hook has no URL")
            if not check_egress_allowed(url, allowed_domains, block_localhost=block_localhost):
                raise ValueError(f"Webhook URL {url!r} blocked by egress allowlist")
        elif transport == "unix_socket":
            path = hook.get("path", "")
            if not path:
                raise ValueError("Unix socket hook has no path")
        else:
            raise ValueError(f"Unknown hook transport: {transport!r}")


async def deliver_webhook(url: str, payload: dict[str, Any], timeout: float = 5.0) -> None:
    """HTTP POST to a webhook URL. Best-effort — failures logged, never raised."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                logger.warning("Webhook %s returned %d", url, resp.status_code)
    except Exception:
        logger.warning("Webhook delivery to %s failed", url, exc_info=True)


async def deliver_unix_socket(path: str, payload: dict[str, Any]) -> None:
    """Send JSON payload to a Unix datagram socket. Best-effort."""
    try:
        data = json.dumps(payload).encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.connect(path)
            sock.sendall(data)
        finally:
            sock.close()
    except Exception:
        logger.warning("Unix socket delivery to %s failed", path, exc_info=True)


async def deliver_hooks(
    hooks: list[dict[str, Any]],
    event_payload: dict[str, Any],
) -> list[asyncio.Task[None]]:
    """Deliver event to all matching hooks. Returns list of pending tasks.

    Each hook config has an optional 'events' list. If present, only
    matching event types are delivered. 'all' matches everything.
    """
    event_type = event_payload.get("event_type", "")
    tasks: list[asyncio.Task[None]] = []

    for hook in hooks:
        allowed_events = hook.get("events", ["all"])
        if "all" not in allowed_events and event_type not in allowed_events:
            continue

        transport = hook.get("transport", "")
        if transport == "webhook":
            url = hook.get("url", "")
            task = asyncio.create_task(deliver_webhook(url, event_payload))
            tasks.append(task)
        elif transport == "unix_socket":
            path = hook.get("path", "")
            task = asyncio.create_task(deliver_unix_socket(path, event_payload))
            tasks.append(task)

    return tasks


async def drain_pending_hooks(
    tasks: list[asyncio.Task[None]],
    timeout: float = 3.0,
) -> None:
    """Await all pending hook tasks with a bounded timeout.

    Tasks that don't complete within timeout are cancelled. This ensures
    the final run_completed/run_failed hooks are delivered before process
    exit in the normal case.
    """
    if not tasks:
        return
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        logger.warning("Cancelled %d pending hook deliveries after %.1fs timeout", len(pending), timeout)
