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

from .config import AppConfig, SessionConfig, ensure_identity, load_config
from .db import DatabaseManager, has_vec_support, init_db
from .services.embedding_worker import EmbeddingWorker
from .services.embeddings import create_embedding_service, get_effective_dimensions
from .services.event_bus import EventBus
from .services.ip_allowlist import check_ip_allowed
from .services.mcp_manager import McpManager
from .services.session_store import create_session_store
from .tools import ToolRegistry, register_default_tools

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("anteroom.security")

MAX_REQUEST_BODY_BYTES = 15 * 1024 * 1024  # 15 MB


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
    vec_dims = get_effective_dimensions(config)

    # Derive encryption key if encryption at rest is enabled
    encryption_key: bytes | None = None
    if config.storage.encrypt_at_rest:
        from .services.encryption import derive_db_key, is_sqlcipher_available

        if not is_sqlcipher_available():
            logger.error("Encryption at rest enabled but sqlcipher3 not installed. pip install sqlcipher3")
            raise RuntimeError("sqlcipher3 required for encrypt_at_rest but not installed")
        private_key = config.identity.private_key if config.identity else ""
        if not private_key:
            raise RuntimeError("Encryption at rest requires an identity key. Run: aroom init")
        encryption_key = derive_db_key(private_key)
        logger.info("Database encryption at rest enabled")

    app.state.db = init_db(db_path, vec_dimensions=vec_dims, encryption_key=encryption_key)

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
    # Clean up empty conversations before loading shared DBs
    from .services import storage as _cleanup_storage

    try:
        _cleanup_storage.delete_empty_conversations(db_manager.personal, config.app.data_dir)
    except Exception:
        logger.warning("Failed to clean up empty conversations")

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
        mcp_manager = McpManager(config.mcp_servers, tool_warning_threshold=config.mcp_tool_warning_threshold)
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
        # Auto-detect: probe the endpoint once before committing to the worker
        if config.embeddings.enabled is None:
            probe_ok = await embedding_service.probe()
            if not probe_ok:
                logger.info("Embedding endpoint unavailable; semantic search disabled. Configure in config.yaml")
                embedding_service = None
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
        if config.embeddings.enabled is False:
            logger.info("Embeddings disabled in config; vector search disabled")
        else:
            logger.info("Embedding service not configured; vector search disabled")

    # Initialize audit writer
    from .services.audit import create_audit_writer

    _private_key = config.identity.private_key if config.identity else ""
    app.state.audit_writer = create_audit_writer(config, private_key_pem=_private_key)
    if app.state.audit_writer.enabled:
        logger.info("Audit log enabled: %s", app.state.audit_writer.log_dir)

    # Start retention worker if configured
    app.state.retention_worker = None
    if config.storage.retention_days > 0:
        from .services.retention import RetentionWorker

        retention_worker = RetentionWorker(
            db=app.state.db,
            data_dir=config.app.data_dir,
            retention_days=config.storage.retention_days,
            check_interval=config.storage.retention_check_interval,
            purge_attachments=config.storage.purge_attachments,
        )
        retention_worker.start()
        app.state.retention_worker = retention_worker
        logger.info("Retention worker started (retention_days=%d)", config.storage.retention_days)

    # Create shared AIService for proxy if enabled
    app.state.proxy_ai_service = None
    if config.proxy.enabled:
        from .services.ai_service import create_ai_service

        app.state.proxy_ai_service = create_ai_service(config.ai)
        logger.info("Proxy AIService created")

    yield

    if hasattr(app.state, "retention_worker") and app.state.retention_worker:
        app.state.retention_worker.stop()
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
        if request.url.path.startswith("/api/") or request.url.path.startswith("/v1/"):
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


def session_id_from_token(token: str) -> str:
    """Derive a deterministic session ID from an auth token value."""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Auth via bearer token header or HttpOnly session cookie with session expiry.

    Supports pluggable session stores (memory or SQLite), IP allowlisting,
    concurrent session limits, and configurable timeouts.
    """

    def __init__(
        self,
        app: FastAPI,
        token_hash: str,
        auth_token: str = "",
        secure_cookies: bool = False,
        session_config: SessionConfig | None = None,
    ) -> None:
        super().__init__(app)
        self.token_hash = token_hash
        self._auth_token = auth_token
        self._secure_cookies = secure_cookies
        self._session_config = session_config or SessionConfig()
        self._store_initialized = False

    @property
    def _store(self):
        """Access the session store — set during _ensure_store."""
        return self.__store

    def _ensure_store(self, request: Request) -> None:
        """Adopt the session store from app.state. Falls back to in-memory."""
        if self._store_initialized:
            return
        state = getattr(request.app, "state", None)
        store = getattr(state, "session_store", None)
        if store is not None:
            self.__store = store
        else:
            self.__store = create_session_store("memory", "")
        self._store_initialized = True

    def _check_token(self, provided: str) -> bool:
        provided_hash = hashlib.sha256(provided.encode()).hexdigest()
        return hmac.compare_digest(provided_hash, self.token_hash)

    def _check_session(self, session_id: str, client_ip: str) -> str:
        """Check session state. Returns 'valid', 'expired', 'ip_mismatch', or 'new'."""
        session = self._store.get(session_id)
        if session is None:
            return "new"
        now = time.time()
        if now - session["created_at"] > self._session_config.absolute_timeout:
            security_logger.info("Session expired (absolute timeout)")
            self._store.delete(session_id)
            return "expired"
        if now - session["last_activity_at"] > self._session_config.idle_timeout:
            security_logger.info("Session expired (idle timeout)")
            self._store.delete(session_id)
            return "expired"
        if session.get("ip_address") and session["ip_address"] != client_ip:
            security_logger.warning(
                "Session IP mismatch: expected %s, got %s",
                session["ip_address"],
                client_ip,
            )
            self._store.delete(session_id)
            return "ip_mismatch"
        return "valid"

    def _handle_session(self, session_id: str, client_ip: str, request: Request, path: str) -> JSONResponse | None:
        """Validate or create a session. Returns an error response, or None to proceed."""
        # Check the specific session first (before bulk cleanup which would mask expiry)
        state = self._check_session(session_id, client_ip)
        if state == "expired":
            security_logger.warning("Expired session from %s: %s", client_ip, path)
            _emit_auth_audit(request, "auth.session_expired", "warning", client_ip, path)
            return JSONResponse(status_code=401, content={"detail": "Session expired"})
        if state == "ip_mismatch":
            _emit_auth_audit(request, "auth.ip_mismatch", "warning", client_ip, path)
            return JSONResponse(status_code=401, content={"detail": "Session invalidated"})
        if state == "new":
            # Clean up expired sessions before limit check so stale entries don't inflate count
            self._store.cleanup_expired(
                self._session_config.idle_timeout,
                self._session_config.absolute_timeout,
            )
            max_sessions = self._session_config.max_concurrent_sessions
            if not self._store.create_if_allowed(session_id, client_ip, max_sessions):
                security_logger.warning("Concurrent session limit reached from %s", client_ip)
                _emit_auth_audit(request, "auth.session_limit", "warning", client_ip, path)
                return JSONResponse(status_code=429, content={"detail": "Too many active sessions"})
        else:
            self._store.touch(session_id)
        return None

    def _make_401(self, detail: str = "Unauthorized") -> JSONResponse:
        """Return a 401 response with a fresh session cookie so the browser auto-recovers."""
        response = JSONResponse(status_code=401, content={"detail": detail})
        if self._auth_token:
            response.set_cookie(
                key="anteroom_session",
                value=self._auth_token,
                httponly=True,
                secure=self._secure_cookies,
                samesite="strict",
                path="/api/",
                max_age=self._session_config.absolute_timeout,
            )
        return response

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not (path.startswith("/api/") or path.startswith("/v1/")):
            return await call_next(request)

        self._ensure_store(request)
        client_ip = request.client.host if request.client else "unknown"

        # IP allowlist check
        if not check_ip_allowed(client_ip, self._session_config.allowed_ips):
            security_logger.warning("IP not in allowlist: %s", client_ip)
            _emit_auth_audit(request, "auth.ip_blocked", "warning", client_ip, path)
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        # Check Authorization header
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and self._check_token(auth[7:]):
            sid = session_id_from_token(auth[7:])
            error = self._handle_session(sid, client_ip, request, path)
            if error:
                return error
            _emit_auth_audit(request, "auth.success", "info", client_ip, path)
            return await call_next(request)

        # Check HttpOnly session cookie
        cookie_token = request.cookies.get("anteroom_session", "")
        if cookie_token and self._check_token(cookie_token):
            sid = session_id_from_token(cookie_token)
            error = self._handle_session(sid, client_ip, request, path)
            if error:
                return error
            # Verify CSRF token for state-changing requests
            if request.method in ("POST", "PATCH", "PUT", "DELETE"):
                csrf_cookie = request.cookies.get("anteroom_csrf", "")
                csrf_header = request.headers.get("x-csrf-token", "")
                if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
                    security_logger.warning("CSRF validation failed from %s: %s %s", client_ip, request.method, path)
                    _emit_auth_audit(request, "auth.csrf_failure", "warning", client_ip, path)
                    return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})
                # Defense-in-depth: validate Origin header if present
                origin = request.headers.get("origin")
                if origin:
                    allowed = getattr(request.app.state, "_allowed_origins", set())
                    if allowed and origin not in allowed:
                        security_logger.warning("Origin mismatch from %s: %s", client_ip, origin)
                        _emit_auth_audit(request, "auth.origin_mismatch", "warning", client_ip, path)
                        return JSONResponse(status_code=403, content={"detail": "Origin not allowed"})
            _emit_auth_audit(request, "auth.success", "info", client_ip, path)
            return await call_next(request)

        security_logger.warning("Authentication failed from %s: %s %s", client_ip, request.method, path)
        _emit_auth_audit(request, "auth.failure", "warning", client_ip, path)
        return self._make_401()


def _emit_auth_audit(request: Request, event_type: str, severity: str, client_ip: str, path: str) -> None:
    """Emit an auth audit event if the audit writer is available and enabled."""
    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    writer = getattr(state, "audit_writer", None)
    if writer is None:
        return
    from .services.audit import AuditEntry

    writer.emit(
        AuditEntry.create(
            event_type,
            severity,
            source_ip=client_ip,
            details={"path": path, "method": request.method},
        )
    )


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


def create_app(config: AppConfig | None = None, enforced_fields: list[str] | None = None) -> FastAPI:
    if config is None:
        config, enforced_fields = load_config()
    if enforced_fields is None:
        enforced_fields = []

    # Ensure user identity exists with a private key before token derivation
    # so first-run also gets a stable token (identity is auto-generated if missing).
    # Also repair partial identity (user_id present but private_key missing)
    # from pre-identity upgrades.
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

    bind_host = config.app.host if hasattr(config.app, "host") else "127.0.0.1"
    if not config.app.tls and bind_host not in ("127.0.0.1", "localhost", "::1"):
        security_logger.warning(
            "TLS is disabled but bind_host is '%s' (not localhost). "
            "Session cookies will lack the Secure flag, transmitting credentials "
            "in cleartext. Set 'app.tls: true' in config.yaml for non-localhost deployments.",
            bind_host,
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
    app.state.enforced_fields = enforced_fields

    # Construct DLP scanner once at startup (compiled regexes reused across requests)
    app.state.dlp_scanner = None
    _dlp_cfg = getattr(getattr(config, "safety", None), "dlp", None)
    if _dlp_cfg is not None and _dlp_cfg.enabled:
        from .services.dlp import DlpScanner

        app.state.dlp_scanner = DlpScanner(_dlp_cfg)

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
    if config.proxy.enabled and config.proxy.allowed_origins:
        for origin in config.proxy.allowed_origins:
            _allowed_origins.add(origin)
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
    app.add_middleware(
        BearerTokenMiddleware,
        token_hash=token_hash,
        auth_token=auth_token,
        secure_cookies=config.app.tls,
        session_config=config.session,
    )
    app.state.auth_token = auth_token
    app.state.session_store = create_session_store(
        config.session.store,
        str(config.app.data_dir) if hasattr(config.app, "data_dir") and config.app.data_dir else "",
    )

    session_absolute_timeout = config.session.absolute_timeout
    csrf_token = secrets.token_urlsafe(32)
    app.state.csrf_token = csrf_token
    cache_bust = str(int(time.time()))

    from .routers import (
        approvals,
        chat,
        config_api,
        conversations,
        databases,
        events,
        plan,
        projects,
        search,
        sources,
        usage,
    )

    app.include_router(conversations.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(config_api.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(databases.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(sources.router, prefix="/api")
    app.include_router(usage.router, prefix="/api")
    app.include_router(plan.router, prefix="/api")

    if config.proxy.enabled:
        from .routers import proxy

        app.include_router(proxy.router, prefix="/v1")
        logger.info("OpenAI-compatible proxy enabled at /v1/")

    @app.post("/api/logout")
    async def logout(request: Request):
        cookie_token = request.cookies.get("anteroom_session", "")
        if cookie_token:
            sid = session_id_from_token(cookie_token)
            store = getattr(request.app.state, "session_store", None)
            if store is not None:
                store.delete(sid)
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
            max_age=session_absolute_timeout,
        )
        response.set_cookie(
            key="anteroom_csrf",
            value=csrf_token,
            httponly=False,
            secure=secure_cookies,
            samesite="strict",
            path="/",
            max_age=session_absolute_timeout,
        )
        return response

    app.mount("/", StaticFiles(directory=str(static_dir)), name="static")

    return app
