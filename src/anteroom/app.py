"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import AppConfig, SessionConfig, ensure_identity, load_config
from .db import DatabaseManager, init_db
from .services.embedding_worker import EmbeddingWorker
from .services.embeddings import create_embedding_service, get_effective_dimensions
from .services.event_bus import EventBus
from .services.ip_allowlist import check_ip_allowed
from .services.mcp_manager import McpManager
from .services.session_store import MemorySessionStore, SQLiteSessionStore, create_session_store
from .tools import ToolRegistry, register_default_tools

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("anteroom.security")

MAX_REQUEST_BODY_BYTES = 15 * 1024 * 1024  # 15 MB


def _write_progress(path: Path | None, step: str, status: str, detail: str = "") -> None:
    """Append one NDJSON progress event. Never raises."""
    if path is None:
        return
    try:
        event: dict[str, str] = {"step": step, "status": status}
        if detail:
            event["detail"] = detail
        with open(path, "a") as f:
            f.write(json.dumps(event) + "\n")
            f.flush()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config: AppConfig = app.state.config
    _progress_path = config.app.data_dir / f"anteroom-{config.app.port}.progress"

    # Identity is normally ensured in create_app() before token derivation.
    # This is a safety net for cases where create_app() was called with a
    # pre-built config that skipped identity generation.
    if not config.identity:
        try:
            config.identity = ensure_identity()
        except Exception:
            logger.warning("Failed to auto-generate user identity")

    _write_progress(_progress_path, "database", "running")
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
    _write_progress(_progress_path, "database", "done")

    event_bus = EventBus()
    app.state.event_bus = event_bus
    event_bus.start_polling(db_manager)

    mcp_manager = None
    if config.mcp_servers:
        _write_progress(_progress_path, "mcp_servers", "running", detail=f"{len(config.mcp_servers)} servers")
        mcp_manager = McpManager(config.mcp_servers, tool_warning_threshold=config.mcp_tool_warning_threshold)
        try:
            await mcp_manager.startup()
            tools = mcp_manager.get_all_tools()
            logger.info(f"MCP: {len(tools)} tools available from {len(config.mcp_servers)} server(s)")
        except Exception as e:
            logger.warning(f"MCP startup error: {e}")
        _write_progress(_progress_path, "mcp_servers", "done")
    app.state.mcp_manager = mcp_manager

    _write_progress(_progress_path, "tools", "running")
    tool_registry = ToolRegistry()
    working_dir = os.getcwd()
    register_default_tools(tool_registry, working_dir=working_dir)
    tool_registry.set_safety_config(config.safety, working_dir=working_dir)
    app.state.tool_registry = tool_registry
    app.state.pending_approvals = {}
    logger.info(f"Built-in tools: {len(tool_registry.list_tools())} registered (cwd: {working_dir})")
    _write_progress(_progress_path, "tools", "done")

    # Initialize vector index manager (usearch-based)
    from .services.vector_index import VectorIndexManager

    vec_manager = VectorIndexManager(config.app.data_dir, dimensions=vec_dims)
    app.state.vec_manager = vec_manager
    app.state.vec_enabled = vec_manager.enabled
    if vec_manager.enabled:
        vec_manager.rebuild_from_db(app.state.db)

    _write_progress(_progress_path, "embeddings", "running")
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
                worker = EmbeddingWorker(app.state.db, embedding_service, vec_manager=vec_manager)
                worker.start()
                app.state.embedding_worker = worker
                logger.info("Embedding worker started")
            else:
                logger.info("Embedding service available but usearch not installed; vector search disabled")
    else:
        if config.embeddings.enabled is False:
            logger.info("Embeddings disabled in config; vector search disabled")
        else:
            logger.info("Embedding service not configured; vector search disabled")
    _write_progress(_progress_path, "embeddings", "done")

    # Start reranker service (optional, for RAG quality improvement)
    app.state.reranker_service = None
    from .services.reranker import create_reranker_service

    reranker_service = create_reranker_service(config)
    if reranker_service:
        if config.reranker.enabled is None:
            probe_ok = await reranker_service.probe()
            if not probe_ok:
                logger.info("Reranker model unavailable; reranking disabled")
                reranker_service = None
        if reranker_service:
            app.state.reranker_service = reranker_service
            logger.info("Reranker service started (model: %s)", reranker_service.model)

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

    _write_progress(_progress_path, "packs", "running")
    # Install/update built-in starter packs
    try:
        from .services.starter_packs import install_starter_packs

        starter_results = install_starter_packs(app.state.db)
        installed = [r for r in starter_results if r["status"] == "installed"]
        updated = [r for r in starter_results if r["status"] == "updated"]
        if installed or updated:
            logger.info(
                "Starter packs: %d installed, %d updated",
                len(installed),
                len(updated),
            )
    except Exception:
        logger.warning("Failed to install starter packs", exc_info=True)

    # Start pack refresh worker if configured
    app.state.pack_refresh_worker = None
    if config.pack_sources:
        import asyncio

        from .services.pack_refresh import PackRefreshWorker

        def _refresh_derived_singletons(cfg: object) -> None:
            """Refresh config-derived singletons (rate limit, DLP, injection detector)."""
            app.state.rate_limit_config = getattr(cfg, "rate_limit", None)
            safety = getattr(cfg, "safety", None)
            dlp_cfg = getattr(safety, "dlp", None) if safety else None
            if dlp_cfg is not None and dlp_cfg.enabled:
                from .services.dlp import DlpScanner

                app.state.dlp_scanner = DlpScanner(dlp_cfg)
            else:
                app.state.dlp_scanner = None
            inj_cfg = getattr(safety, "prompt_injection", None) if safety else None
            if inj_cfg is not None and inj_cfg.enabled:
                from .services.injection_detector import InjectionDetector

                app.state.injection_detector = InjectionDetector(inj_cfg)
            else:
                app.state.injection_detector = None

        def _reload_after_pack_refresh() -> None:
            """Rebuild config overlays and registries after background pack refresh."""
            try:
                from .services.artifact_registry import ArtifactRegistry
                from .services.artifacts import ArtifactType
                from .services.config_overlays import ComplianceError, rebuild_effective_config
                from .services.pack_attachments import detach_pack as _q_detach
                from .services.rule_enforcer import RuleEnforcer

                # 1. Rebuild config overlays so source-installed pack configs take effect
                previous_config = getattr(app.state, "config", None)
                previous_enforced = getattr(app.state, "enforced_fields", None)
                config_ok = True
                try:
                    result = rebuild_effective_config(app.state.db, previous_config=previous_config)
                    app.state.config = result.config
                    app.state.enforced_fields = result.enforced_fields
                    _refresh_derived_singletons(result.config)
                    for warning in result.warnings:
                        logger.warning(warning)
                except ComplianceError:
                    config_ok = False
                    logger.warning(
                        "Config rebuild blocked after pack refresh (compliance failure) — quarantining changed packs",
                        exc_info=True,
                    )
                    # Quarantine: detach recently-changed packs that caused the failure
                    worker = app.state.pack_refresh_worker
                    if worker is not None:
                        changed_ids = list(worker._last_changed_pack_ids)
                        quarantined = 0
                        for pid in changed_ids:
                            try:
                                _q_detach(app.state.db, pid)
                                quarantined += 1
                            except Exception:
                                logger.warning("Failed to quarantine pack %s", pid, exc_info=True)
                        if quarantined:
                            logger.warning(
                                "Quarantined %d pack(s) — detached until config issue is resolved",
                                quarantined,
                            )
                    # Re-attempt rebuild from the now-clean attachment set
                    try:
                        result2 = rebuild_effective_config(app.state.db, previous_config=previous_config)
                        app.state.config = result2.config
                        app.state.enforced_fields = result2.enforced_fields
                        _refresh_derived_singletons(result2.config)
                    except Exception:
                        app.state.config = previous_config
                        app.state.enforced_fields = previous_enforced
                except Exception:
                    # Infrastructure error — keep previous config but still reload registries
                    logger.warning("Config rebuild failed after pack refresh", exc_info=True)

                # 2. Rebuild registries
                registry = ArtifactRegistry()
                registry.load_from_db(app.state.db)
                app.state.artifact_registry = registry

                enforcer = RuleEnforcer()
                enforcer.load_rules(registry.list_all(artifact_type=ArtifactType.RULE))
                app.state.rule_enforcer = enforcer

                tool_reg = getattr(app.state, "tool_registry", None)
                if tool_reg is not None:
                    tool_reg.set_rule_enforcer(enforcer)

                skill_reg = getattr(app.state, "skill_registry", None)
                if skill_reg is not None:
                    skill_reg.load_from_artifacts(registry)

                logger.info("Config and registries reloaded after pack refresh (config_ok=%s)", config_ok)
            except Exception:
                logger.warning("Failed to reload after pack refresh", exc_info=True)

        pack_refresh_worker = PackRefreshWorker(
            db=app.state.db,
            data_dir=config.app.data_dir,
            sources=config.pack_sources,
            on_packs_changed=_reload_after_pack_refresh,
            event_loop=asyncio.get_running_loop(),
        )
        pack_refresh_worker.start()
        app.state.pack_refresh_worker = pack_refresh_worker
        logger.info("Pack refresh worker started (%d sources)", len(config.pack_sources))

    _write_progress(_progress_path, "packs", "done")

    _write_progress(_progress_path, "artifacts", "running")
    # Initialize artifact registry
    from .services.artifact_registry import ArtifactRegistry

    artifact_registry = ArtifactRegistry()
    artifact_registry.load_from_db(app.state.db)  # Web UI: loads global attachments; space-scoped per-request
    app.state.artifact_registry = artifact_registry
    if artifact_registry.count:
        logger.info("Artifact registry loaded: %d artifacts", artifact_registry.count)

    # Load hard-enforced rules into the tool registry
    from .services.artifacts import ArtifactType as _ArtType
    from .services.rule_enforcer import RuleEnforcer

    _rule_enforcer = RuleEnforcer()
    _rule_enforcer.load_rules(artifact_registry.list_all(artifact_type=_ArtType.RULE))
    tool_registry.set_rule_enforcer(_rule_enforcer)
    app.state.rule_enforcer = _rule_enforcer
    if _rule_enforcer.rule_count:
        logger.info("Rule enforcer loaded: %d hard rules", _rule_enforcer.rule_count)

    # Initialize skill registry
    app.state.skill_registry = None
    if config.cli.skills.auto_invoke:
        from .cli.skills import SkillRegistry

        skill_registry = SkillRegistry()
        skill_registry.load()
        app.state.skill_registry = skill_registry
        if artifact_registry.count:
            n = skill_registry.load_from_artifacts(artifact_registry)
            if n:
                logger.info("Skill registry: %d skills from artifacts", n)
        if skill_registry.list_skills():
            logger.info("Skill registry loaded: %d skills", len(skill_registry.list_skills()))

    # Create shared AIService for proxy if enabled
    app.state.proxy_ai_service = None
    if config.proxy.enabled:
        from .services.ai_service import create_ai_service

        app.state.proxy_ai_service = create_ai_service(config.ai)
        logger.info("Proxy AIService created")
    _write_progress(_progress_path, "artifacts", "done")

    _write_progress(_progress_path, "ready", "done")
    try:
        yield
    finally:
        try:
            _progress_path.unlink(missing_ok=True)
        except OSError:
            pass
        if hasattr(app.state, "pack_refresh_worker") and app.state.pack_refresh_worker:
            app.state.pack_refresh_worker.stop()
        if hasattr(app.state, "retention_worker") and app.state.retention_worker:
            app.state.retention_worker.stop()
        if hasattr(app.state, "embedding_worker") and app.state.embedding_worker:
            app.state.embedding_worker.stop()
        if hasattr(app.state, "vec_manager") and app.state.vec_manager:
            app.state.vec_manager.save_all()
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

    # Paths that set their own CSP and framing headers (e.g. embedded viewer iframes)
    _SELF_CSP_PATHS = frozenset({"/excalidraw-viewer"})

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        if self.tls_enabled:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Let viewer endpoints keep their own CSP and X-Frame-Options
        if request.url.path not in self._SELF_CSP_PATHS:
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
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

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
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

    def __init__(
        self,
        app: FastAPI,
        max_requests: int = 60,
        window_seconds: int = 60,
        exempt_paths: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        self.exempt_paths: set[str] = exempt_paths or set()
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path in self.exempt_paths:
            return await call_next(request)

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
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(self.window)},
            )
        hits.append(now)
        return await call_next(request)


def session_id_from_token(token: str) -> str:
    """Derive a deterministic session ID from an auth token value."""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def _normalize_loopback(ip: str) -> str:
    """Normalize loopback addresses to a canonical form for session IP binding.

    Maps ``::1``, ``::ffff:127.0.0.1``, and ``127.0.0.1`` to the same
    canonical value so that IPv4/IPv6 dual-stack localhost connections
    don't trigger spurious session invalidation.
    """
    try:
        addr = ipaddress.ip_address(ip)
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        if addr.is_loopback:
            return "127.0.0.1"
        return str(addr)
    except ValueError:
        return ip


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
    def _store(self) -> MemorySessionStore | SQLiteSessionStore:
        """Access the session store — set during _ensure_store."""
        return self.__store  # type: ignore[no-any-return]

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
        stored_ip = session.get("ip_address", "")
        if stored_ip and _normalize_loopback(stored_ip) != _normalize_loopback(client_ip):
            security_logger.warning(
                "Session IP mismatch: expected %s, got %s",
                stored_ip,
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
            return self._make_401("Session expired")
        if state == "ip_mismatch":
            _emit_auth_audit(request, "auth.ip_mismatch", "warning", client_ip, path)
            return self._make_401("Session invalidated")
        if state == "new":
            # Clean up expired sessions before limit check so stale entries don't inflate count
            self._store.cleanup_expired(
                self._session_config.idle_timeout,
                self._session_config.absolute_timeout,
            )
            max_sessions = self._session_config.max_concurrent_sessions
            normalized_ip = _normalize_loopback(client_ip)
            if not self._store.create_if_allowed(session_id, normalized_ip, max_sessions):
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

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
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
                    allowed: set[str] = getattr(request.app.state, "_allowed_origins", set())
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
    app.state.rate_limit_config = config.rate_limit
    app.state.enforced_fields = enforced_fields

    # Construct DLP scanner once at startup (compiled regexes reused across requests)
    app.state.dlp_scanner = None
    _dlp_cfg = getattr(getattr(config, "safety", None), "dlp", None)
    if _dlp_cfg is not None and _dlp_cfg.enabled:
        from .services.dlp import DlpScanner

        app.state.dlp_scanner = DlpScanner(_dlp_cfg)

    # Construct injection detector once at startup
    app.state.injection_detector = None
    _inj_cfg = getattr(getattr(config, "safety", None), "prompt_injection", None)
    if _inj_cfg is not None and _inj_cfg.enabled:
        from .services.injection_detector import InjectionDetector

        app.state.injection_detector = InjectionDetector(_inj_cfg)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
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

    app.add_middleware(SecurityHeadersMiddleware, tls_enabled=config.app.tls)  # type: ignore[arg-type]
    app.add_middleware(MaxBodySizeMiddleware)  # type: ignore[arg-type]
    rl = config.rate_limit
    app.add_middleware(
        RateLimitMiddleware,  # type: ignore[arg-type]
        max_requests=rl.max_requests,
        window_seconds=rl.window_seconds,
        exempt_paths=set(rl.exempt_paths),
    )

    auth_token = _derive_auth_token(config)
    token_hash = hashlib.sha256(auth_token.encode()).hexdigest()
    app.add_middleware(  # type: ignore[arg-type]
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
        artifacts,
        chat,
        config_api,
        conversations,
        databases,
        events,
        plan,
        search,
        sources,
        usage,
    )
    from .routers import artifact_health as artifact_health_router
    from .routers import packs as packs_router
    from .routers import spaces as spaces_router
    from .routers import workflows as workflows_router

    app.include_router(conversations.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(config_api.router, prefix="/api")
    app.include_router(databases.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(sources.router, prefix="/api")
    app.include_router(usage.router, prefix="/api")
    app.include_router(plan.router, prefix="/api")
    # Order matters: artifact_health must precede artifacts because
    # artifacts uses {fqn:path} which would shadow /artifacts/check.
    app.include_router(artifact_health_router.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(packs_router.router, prefix="/api")
    app.include_router(spaces_router.router, prefix="/api")
    app.include_router(workflows_router.router, prefix="/api")

    if config.proxy.enabled:
        from .routers import proxy

        app.include_router(proxy.router, prefix="/v1")
        logger.info("OpenAI-compatible proxy enabled at /v1/")

    @app.post("/api/logout")
    async def logout(request: Request) -> JSONResponse:
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
    async def index() -> Any:
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

    @app.get("/excalidraw-viewer")
    async def excalidraw_viewer() -> Any:
        """Serve a minimal Excalidraw viewer page with a permissive CSP.

        The parent page embeds this in an iframe and sends scene data via
        postMessage.  Because this is a real same-origin URL (not srcdoc or
        blob:), we can set its own Content-Security-Policy header that allows
        loading Excalidraw + React from esm.sh without affecting the main
        page's strict CSP.
        """
        from fastapi.responses import HTMLResponse

        ver = "0.18.0"
        # Use exportToSvg() to render a static SVG instead of mounting the
        # full interactive React component. This avoids all React lifecycle
        # issues, error boundaries, and dual-instance crashes.
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<style>"
            "*{margin:0;padding:0;box-sizing:border-box}"
            "html,body{width:100%;height:100%;overflow:auto;background:#fff}"
            "#root{width:100%;height:100%;display:flex;align-items:center;"
            "justify-content:center}"
            "#root svg{max-width:100%;max-height:100%;width:auto;height:auto}"
            "#loading{color:#888;font-family:system-ui}"
            "#error{padding:16px;color:#c33;font-family:system-ui}"
            "</style>"
            '<script type="importmap">{"imports":{'
            '"react":"https://esm.sh/react@19",'
            '"react-dom":"https://esm.sh/react-dom@19",'
            '"react/jsx-runtime":"https://esm.sh/react@19/jsx-runtime"'
            "}}</script>"
            "</head><body>"
            '<div id="root"><div id="loading">Waiting for diagram data...</div></div>'
            '<script type="module">'
            "window.addEventListener('message',async function handler(evt){"
            "if(!evt.data||evt.data.type!=='excalidraw-scene')return;"
            "window.removeEventListener('message',handler);"
            "var sceneData=evt.data.scene;"
            "document.getElementById('root').innerHTML="
            "'<div id=\"loading\">Loading diagram...</div>';"
            "var timer=setTimeout(function(){"
            "document.getElementById('root').innerHTML="
            "'<div id=\"error\">Timed out loading Excalidraw from CDN.</div>';"
            "window.parent.postMessage({type:'excalidraw-error',message:'timeout'},window.location.origin);"
            "},20000);"
            "try{"
            # Load only the Excalidraw utils — exportToSvg needs React
            # internally but does not mount a React tree in the DOM.
            f"var E=await import('https://esm.sh/@excalidraw/excalidraw@{ver}?external=react,react-dom');"
            "clearTimeout(timer);"
            "var svg=await E.exportToSvg({"
            "elements:sceneData.elements||[],"
            "appState:Object.assign({},sceneData.appState||{},{exportWithDarkMode:false}),"
            "files:sceneData.files||null});"
            "svg.removeAttribute('width');svg.removeAttribute('height');"
            "svg.style.width='100%';svg.style.height='100%';"
            "document.getElementById('root').innerHTML='';"
            "document.getElementById('root').appendChild(svg);"
            "window.parent.postMessage({type:'excalidraw-ready'},window.location.origin);"
            "}catch(err){clearTimeout(timer);"
            "document.getElementById('root').innerHTML="
            "'<div id=\"error\">Failed to render diagram: '+err.message+'</div>';"
            "window.parent.postMessage({type:'excalidraw-error',message:err.message},window.location.origin);"
            "}"
            "});"
            "</script></body></html>"
        )
        response = HTMLResponse(html)
        # Permissive CSP for this viewer only — allows esm.sh CDN resources
        # SECURITY-REVIEW: unsafe-inline required for inline script in viewer iframe; server-controlled, no user input
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://esm.sh; "
            "style-src 'self' 'unsafe-inline' https://esm.sh; "
            "font-src 'self' https://esm.sh; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' https://esm.sh; "
            "frame-ancestors 'self'"
        )
        return response

    app.mount("/", StaticFiles(directory=str(static_dir)), name="static")

    return app
