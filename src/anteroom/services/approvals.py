from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass


@dataclass
class PendingApproval:
    fut: asyncio.Future[bool]
    message: str
    created_at: float


class ApprovalManager:
    """In-process approval manager.

    Note: this is safe in the default single-process server mode used by `anteroom`.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[str, PendingApproval] = {}

    async def request(self, message: str) -> str:
        approval_id = secrets.token_urlsafe(16)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        async with self._lock:
            self._pending[approval_id] = PendingApproval(fut=fut, message=message, created_at=time.time())
        return approval_id

    async def wait(self, approval_id: str, timeout_s: float = 300.0) -> bool:
        async with self._lock:
            pending = self._pending.get(approval_id)
        if not pending:
            return False
        try:
            return await asyncio.wait_for(pending.fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            return False
        finally:
            async with self._lock:
                self._pending.pop(approval_id, None)

    async def resolve(self, approval_id: str, approved: bool) -> bool:
        async with self._lock:
            pending = self._pending.get(approval_id)
            if not pending:
                return False
            if pending.fut.done():
                return False
            pending.fut.set_result(bool(approved))
            return True

    async def get(self, approval_id: str) -> PendingApproval | None:
        async with self._lock:
            return self._pending.get(approval_id)
