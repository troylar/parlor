from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Request


router = APIRouter()


class ApprovalResponse(BaseModel):
    approval_id: str
    approved: bool


@router.post("/approvals/respond")
async def respond_approval(payload: ApprovalResponse, request: Request):
    mgr = getattr(request.app.state, "approval_manager", None)
    if mgr is None:
        return {"ok": False, "detail": "approval manager not configured"}

    resolved = await mgr.resolve(payload.approval_id, payload.approved)
    return {"ok": True, "resolved": resolved}
