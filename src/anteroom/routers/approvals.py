"""Approval response endpoint for destructive action safety gate."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["approvals"])


_APPROVAL_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class ApprovalRequest(BaseModel):
    approved: bool = False


@router.post("/approvals/{approval_id}/respond")
async def respond_approval(approval_id: str, body: ApprovalRequest, request: Request):
    ct = request.headers.get("content-type", "")
    if not ct.startswith("application/json"):
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")

    if not _APPROVAL_ID_RE.match(approval_id):
        logger.warning("Invalid approval ID format: %r", approval_id[:80])
        raise HTTPException(status_code=400, detail="Invalid approval ID format")

    pending = getattr(request.app.state, "pending_approvals", {})
    # Atomic pop to prevent TOCTOU: only the first responder gets the entry
    entry = pending.pop(approval_id, None)
    if not entry:
        logger.info("Approval not found or already resolved: %s", approval_id)
        raise HTTPException(status_code=404, detail="Approval not found or expired")

    action = "approved" if body.approved else "denied"
    logger.info("Safety approval %s: id=%s", action, approval_id)

    entry["approved"] = body.approved
    entry["event"].set()
    return {"status": "ok", "approved": body.approved}
