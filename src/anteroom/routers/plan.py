"""Plan mode endpoints: read, approve, reject plans."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..cli.plan import delete_plan, get_plan_file_path, read_plan

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plan"])

_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _validate_conversation_id(conversation_id: str) -> None:
    """Reject obviously invalid conversation IDs."""
    if not conversation_id or not _CONV_ID_RE.match(conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation ID")


def _require_json(request: Request) -> None:
    """Require application/json Content-Type on state-changing requests."""
    ct = request.headers.get("content-type", "")
    if not ct.startswith("application/json"):
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")


class PlanRejectRequest(BaseModel):
    reason: str = Field(default="", max_length=4096)


@router.get("/conversations/{conversation_id}/plan")
async def get_plan(conversation_id: str, request: Request) -> dict:
    _validate_conversation_id(conversation_id)
    data_dir = request.app.state.config.app.data_dir
    try:
        plan_path = get_plan_file_path(data_dir, conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")
    content = read_plan(plan_path)
    if content is None:
        return {"exists": False, "content": None}
    return {"exists": True, "content": content}


@router.post("/conversations/{conversation_id}/plan/approve")
async def approve_plan(conversation_id: str, request: Request) -> dict:
    _require_json(request)
    _validate_conversation_id(conversation_id)
    data_dir = request.app.state.config.app.data_dir
    try:
        plan_path = get_plan_file_path(data_dir, conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")
    content = read_plan(plan_path)
    if content is None:
        raise HTTPException(status_code=404, detail="No plan found for this conversation")
    delete_plan(plan_path)
    logger.info("Plan approved for conversation %s", conversation_id)
    return {"status": "approved", "content": content}


@router.post("/conversations/{conversation_id}/plan/reject")
async def reject_plan(conversation_id: str, body: PlanRejectRequest, request: Request) -> dict:
    _require_json(request)
    _validate_conversation_id(conversation_id)
    data_dir = request.app.state.config.app.data_dir
    try:
        plan_path = get_plan_file_path(data_dir, conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")
    content = read_plan(plan_path)
    if content is None:
        raise HTTPException(status_code=404, detail="No plan found for this conversation")
    delete_plan(plan_path)
    if body.reason:
        logger.info("Plan rejected for conversation %s: %s", conversation_id, body.reason[:200])
    else:
        logger.info("Plan rejected for conversation %s", conversation_id)
    return {"status": "rejected"}
