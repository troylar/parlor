"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .config import AppConfig, ensure_identity, load_config
from .db import DatabaseManager, has_vec_support, init_db
from .services.embedding_worker import EmbeddingWorker
from .services.embeddings import create_embedding_service
from .services.event_bus import EventBus
from .services.mcp_manager import McpManager
from .tools import ToolRegistry, register_default_tools

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("anteroom.security")

MAX_REQUEST_BODY_BYTES = 15 * 1024 * 1024  # 15 MB
SESSION_ABSOLUTE_TIMEOUT = 12 * 60 * 60  # 12 hours
SESSION_IDLE_TIMEOUT = 30 * 60  # 30 minutes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config: AppConfig = app.state.config

    # Identity is normally ensured in create_app() before token derivation.
    # This is a safety net for cases where create_app() was called with a
    # pre-built config that skipped identity generation.
    if not config.identity:
        try:
            config.identity = ensure_identity()
        except Exception:
            logger.warning("Failed to auto-generate user identity")

    db_path = config.app.data_dir / "chat.db"
    app.state.db = init_db(db_path)

    db_manager = DatabaseManager()
    db_manager.add("personal", db_path)

    # Register user in personal DB
    if config.identity:
        from .services import storage

        try:
            storage.register_user(
                db_manager.personal,
                config.identity.user_id,
                config.identity.display_name,
                config.identity.public_key,
            )
        except Exception:
            logger.warning("Failed to register user in personal DB")
    for sdb in config.shared_databases:
        try:
            sdb_path = Path(sdb.path)
            sdb_path.parent.mkdir(parents=True, exist_ok=True)
            db_manager.add(sdb.name, sdb_path, passphrase_hash=sdb.passphrase_hash)
            logger.info(f"Shared DB loaded: {sdb.name} ({sdb.path})")
            # Register user in shared DB
            if config.identity:
                from .services import storage as _storage

                try:
                    _storage.register_user(
                        db_manager.get(sdb.name),
                        config.identity.user_id,
                        config.identity.display_name,
                        config.identity.public_key,
                    )
                except Exception:
                    logger.warning(f"Failed to register user in shared DB '{sdb.name}'")
        except Exception as e:
            logger.warning(f"Failed to load shared DB '{sdb.name}': {e}")
    app.state.db_manager = db_manager

    event_bus = EventBus()
    app.state.event_bus = event_bus
    event_bus.start_polling(db_manager)

    mcp_manager = None
    if config.mcp_servers:
        mcp_manager = McpManager(config.mcp_servers)
        try:
            await mcp_manager.startup()
            tools = mcp_manager.get_all_tools()
            logger.info(f"MCP: {len(tools)} tools available from {len(config.mcp_servers)} server(s)")
        except Exception as e:
            logger.warning(f"MCP startup error: {e}")
    app.state.mcp_manager = mcp_manager

    tool_registry = ToolRegistry()
    working_dir = os.getcwd()
    register_default_tools(tool_registry, working_dir=working_dir)
    tool_registry.set_safety_config(config.safety, working_dir=working_dir)
    app.state.tool_registry = tool_registry
    app.state.pending_approvals = {}
    logger.info(f"Built-in tools: {len(tool_registry.list_tools())} registered (cwd: {working_dir})")

    # Expose vec support flag
    raw_conn = app.state.db._conn if hasattr(app.state.db, "_conn") else None
    app.state.vec_enabled = has_vec_support(raw_conn) if raw_conn else False

    # Start embedding service and background worker
    app.state.embedding_service = None
    app.state.embedding_worker = None
    embedding_service = create_embedding_service(config)
    if embedding_service:
        app.state.embedding_service = embedding_service
        if app.state.vec_enabled:
            worker = EmbeddingWorker(app.state.db, embedding_service)
            worker.start()
            app.state.embedding_worker = worker
            logger.info("Embedding worker started")
        else:
            logger.info("Embedding service available but sqlite-vec not loaded; vector search disabled")
    else:
        logger.info("Embedding service not configured; vector search disabled")

    yield

    if hasattr(app.state, "embedding_worker") and app.state.embedding_worker:
        app.state.embedding_worker.stop()
    if hasattr(app.state, "event_bus"):
        app.state.event_bus.stop_polling()
    if app.state.db:
        app.state.db.close()
    if hasattr(app.state, "db_manager"):
        app.state.db_manager.close_all()
    if app.state.mcp_manager:
        await app.state.mcp_manager.shutdown()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    def __init__(self, app: FastAPI, tls_enabled: bool = True) -> None:
        super().__init__(app)
        self.tls_enabled = tls_enabled

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        if self.tls_enabled:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'sha256-os8eBqepmojbV7o9EA/H5axJe8VOx1ngDoptqveTNpA='; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        elif request.url.path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject requests with Content-Length exceeding the limit."""

    def __init__(self, app: FastAPI, max_body_size: int = MAX_REQUEST_BODY_BYTES) -> None:
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_body_size:
            security_logger.warning(
                "Request body too large from %s: %s bytes",
                request.client.host if request.client else "unknown",
                content_length,
            )
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-IP rate limiter: max requests per window with LRU eviction."""

    MAX_TRACKED_IPS = 10000

    def __init__(self, app: FastAPI, max_requests: int = 60, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        while len(self._hits) > self.MAX_TRACKED_IPS:
            self._hits.popitem(last=False)

        if client_ip not in self._hits:
            self._hits[client_ip] = []

        hits = self._hits[client_ip]
        hits[:] = [t for t in hits if now - t < self.window]
        self._hits.move_to_end(client_ip)

        if not hits:
            del self._hits[client_ip]
            self._hits[client_ip] = []
            hits = self._hits[client_ip]

        if len(hits) >= self.max_requests:
            security_logger.warning("Rate limit exceeded for IP %s", client_ip)
            return JSONResponse(status_code=429, content={"detail": "Too many requests"})
        hits.append(now)
        return await call_next(request)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Auth via bearer token header or HttpOnly session cookie with session expiry."""

    def __init__(self, app: FastAPI, token_hash: str) -> None:
        super().__init__(app)
        self.token_hash = token_hash
        self._session_created_at = time.time()
        self._last_activity = time.time()

    def _is_session_valid(self) -> bool:
        now = time.time()
        if now - self._session_created_at > SESSION_ABSOLUTE_TIMEOUT:
            security_logger.info("Session expired (absolute timeout)")
            return False
        if now - self._last_activity > SESSION_IDLE_TIMEOUT:
            security_logger.info("Session expired (idle timeout)")
            return False
        return True

    def _check_token(self, provided: str) -> bool:
        provided_hash = hashlib.sha256(provided.encode()).hexdigest()
        return hmac.compare_digest(provided_hash, self.token_hash)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        if not self._is_session_valid():
            security_logger.warning("Expired session access attempt from %s: %s", client_ip, path)
            return JSONResponse(status_code=401, content={"detail": "Session expired"})

        # Check Authorization header
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and self._check_token(auth[7:]):
            self._last_activity = time.time()
            return await call_next(request)

        # Check HttpOnly session cookie
        cookie_token = request.cookies.get("anteroom_session", "")
        if cookie_token and self._check_token(cookie_token):
            # Verify CSRF token for state-changing requests
            if request.method in ("POST", "PATCH", "PUT", "DELETE"):
                csrf_cookie = request.cookies.get("anteroom_csrf", "")
                csrf_header = request.headers.get("x-csrf-token", "")
                if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
                    security_logger.warning("CSRF validation failed from %s: %s %s", client_ip, request.method, path)
                    return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})
                # Defense-in-depth: validate Origin header if present
                origin = request.headers.get("origin")
                if origin:
                    allowed = getattr(request.app.state, "_allowed_origins", set())
                    if allowed and origin not in allowed:
                        security_logger.warning("Origin mismatch from %s: %s", client_ip, origin)
                        return JSONResponse(status_code=403, content={"detail": "Origin not allowed"})
            self._last_activity = time.time()
            return await call_next(request)

        security_logger.warning("Authentication failed from %s: %s %s", client_ip, request.method, path)
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})


def _derive_auth_token(config: AppConfig) -> str:
    """Derive a stable auth token from the Ed25519 identity key.

    Uses HMAC-SHA256 with the private key PEM as the key and a fixed context
    string. This means browser cookies survive server restarts as long as the
    identity key stays the same.

    Falls back to a random token when no identity is configured.
    """
    import base64

    identity = config.identity
    if identity and identity.private_key:
        raw = hmac.new(
            identity.private_key.encode("utf-8"),
            b"anteroom-session-v1",
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")[:43]
    return secrets.token_urlsafe(32)


def create_app(config: AppConfig | None = None) -> FastAPI:
    if config is None:
        config = load_config()

    # Ensure user identity exists with a private key before token derivation
    # so first-run also gets a stable token (identity is auto-generated if missing).
    if not config.identity or not config.identity.private_key:
        try:
            config.identity = ensure_identity()
        except Exception:
            logger.warning("Failed to auto-generate user identity in create_app")

    if not config.ai.verify_ssl:
        security_logger.warning(
            "SSL verification is DISABLED for AI backend connections. "
            "This allows man-in-the-middle attacks. Only use for development."
        )

    app = FastAPI(
        title="Anteroom",
        version="0.5.3",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        security_logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "An internal error occurred"})

    scheme = "https" if config.app.tls else "http"
    origin = f"{scheme}://{config.app.host}:{config.app.port}"
    _allowed_origins = {
        origin,
        f"{scheme}://127.0.0.1:{config.app.port}",
        f"{scheme}://localhost:{config.app.port}",
    }
    app.state._allowed_origins = _allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_allowed_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Client-Id"],
        allow_credentials=True,
    )

    app.add_middleware(SecurityHeadersMiddleware, tls_enabled=config.app.tls)
    app.add_middleware(MaxBodySizeMiddleware)
    app.add_middleware(RateLimitMiddleware, max_requests=120, window_seconds=60)

    auth_token = _derive_auth_token(config)
    token_hash = hashlib.sha256(auth_token.encode()).hexdigest()
    app.add_middleware(BearerTokenMiddleware, token_hash=token_hash)
    app.state.auth_token = auth_token

    csrf_token = secrets.token_urlsafe(32)
    app.state.csrf_token = csrf_token
    cache_bust = str(int(time.time()))

    from .routers import approvals, chat, config_api, conversations, databases, events, projects, search

    app.include_router(conversations.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(config_api.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(databases.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")

    @app.post("/api/logout")
    async def logout():
        response = JSONResponse(content={"status": "logged out"})
        response.delete_cookie("anteroom_session", path="/api/")
        response.delete_cookie("anteroom_csrf", path="/")
        return response

    static_dir = Path(__file__).parent / "static"
    secure_cookies = config.app.tls

    @app.get("/")
    async def index():
        """Serve index.html and set auth token via HttpOnly cookie + CSRF cookie."""
        import re

        from fastapi.responses import HTMLResponse

        html_path = static_dir / "index.html"
        html = html_path.read_text()
        html = re.sub(
            r'src="/js/([^"]+)"',
            rf'src="/js/\1?v={cache_bust}"',
            html,
        )
        html = re.sub(
            r'href="/css/([^"]+)"',
            rf'href="/css/\1?v={cache_bust}"',
            html,
        )
        response = HTMLResponse(html)
        response.set_cookie(
            key="anteroom_session",
            value=auth_token,
            httponly=True,
            secure=secure_cookies,
            samesite="strict",
            path="/api/",
            max_age=SESSION_ABSOLUTE_TIMEOUT,
        )
        response.set_cookie(
            key="anteroom_csrf",
            value=csrf_token,
            httponly=False,
            secure=secure_cookies,
            samesite="strict",
            path="/",
            max_age=SESSION_ABSOLUTE_TIMEOUT,
        )
        return response

    app.mount("/", StaticFiles(directory=str(static_dir)), name="static")

    return app
