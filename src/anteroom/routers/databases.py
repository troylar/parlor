"""Database management and auth endpoints."""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

security_logger = logging.getLogger("anteroom.security")

router = APIRouter(tags=["databases"])

_DB_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Per-IP rate limiting for auth attempts: max 5 attempts per 60 seconds
_AUTH_MAX_ATTEMPTS = 5
_AUTH_WINDOW_SECONDS = 60
_AUTH_MAX_TRACKED_IPS = 1000
_auth_attempts: OrderedDict[str, list[float]] = OrderedDict()


def _validate_db_name(name: str) -> str:
    """Validate database name contains only safe characters."""
    if not _DB_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid database name")
    return name


def _check_auth_rate_limit(client_ip: str) -> None:
    """Enforce per-IP rate limiting on auth attempts."""
    now = time.time()

    while len(_auth_attempts) > _AUTH_MAX_TRACKED_IPS:
        _auth_attempts.popitem(last=False)

    if client_ip not in _auth_attempts:
        _auth_attempts[client_ip] = []

    hits = _auth_attempts[client_ip]
    hits[:] = [t for t in hits if now - t < _AUTH_WINDOW_SECONDS]
    _auth_attempts.move_to_end(client_ip)

    if len(hits) >= _AUTH_MAX_ATTEMPTS:
        security_logger.warning("Database auth rate limit exceeded for IP %s", client_ip)
        raise HTTPException(status_code=429, detail="Too many authentication attempts")
    hits.append(now)


class DatabaseAuthRequest(BaseModel):
    passphrase: str


@router.get("/databases")
async def list_databases(request: Request):
    if not hasattr(request.app.state, "db_manager"):
        return []
    dbs = request.app.state.db_manager.list_databases()
    return [{"name": db["name"], "requires_auth": db.get("requires_auth", "false")} for db in dbs]


@router.post("/databases/{name}/auth")
async def authenticate_database(name: str, body: DatabaseAuthRequest, request: Request):
    """Verify passphrase for a shared database. Sets a session flag on success."""
    _validate_db_name(name)

    if not hasattr(request.app.state, "db_manager"):
        raise HTTPException(status_code=400, detail="Database manager not available")

    client_ip = request.client.host if request.client else "unknown"
    _check_auth_rate_limit(client_ip)

    db_manager = request.app.state.db_manager
    passphrase_hash = db_manager.get_passphrase_hash(name)
    if not passphrase_hash:
        return {"status": "ok", "message": "No passphrase required"}

    from ..services.db_auth import verify_passphrase

    if verify_passphrase(body.passphrase, passphrase_hash):
        security_logger.info("Database auth success for '%s' from %s", name, client_ip)
        response = JSONResponse({"status": "ok"})
        # SECURITY: name is validated above with _DB_NAME_RE (alphanumeric, hyphens, underscores only)
        response.set_cookie(
            key=f"anteroom_db_auth_{name}",
            value="authenticated",
            httponly=True,
            secure=request.app.state.config.app.tls,
            samesite="strict",
            path="/api/",
            max_age=86400,
        )
        return response
    else:
        security_logger.warning("Database auth failed for '%s' from %s", name, client_ip)
        raise HTTPException(status_code=401, detail="Invalid passphrase")
