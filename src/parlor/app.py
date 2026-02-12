"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .config import AppConfig, load_config
from .db import init_db
from .services.mcp_manager import McpManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config: AppConfig = app.state.config
    db_path = config.app.data_dir / "chat.db"
    app.state.db = init_db(db_path)

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

    yield

    if app.state.db:
        app.state.db.close()
    if app.state.mcp_manager:
        await app.state.mcp_manager.shutdown()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-IP rate limiter: max requests per window."""

    def __init__(self, app: FastAPI, max_requests: int = 60, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        hits = self._hits[client_ip]
        hits[:] = [t for t in hits if now - t < self.window]
        if len(hits) >= self.max_requests:
            return JSONResponse(status_code=429, content={"detail": "Too many requests"})
        hits.append(now)
        return await call_next(request)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Optional bearer token auth. Skips static files and the auth-check endpoint."""

    def __init__(self, app: FastAPI, token_hash: str) -> None:
        super().__init__(app)
        self.token_hash = token_hash

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            provided = auth[7:]
            if hashlib.sha256(provided.encode()).hexdigest() == self.token_hash:
                return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})


def create_app(config: AppConfig | None = None) -> FastAPI:
    if config is None:
        config = load_config()

    app = FastAPI(title="Parlor", version="0.1.0", lifespan=lifespan)
    app.state.config = config

    origin = f"http://{config.app.host}:{config.app.port}"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin, "http://127.0.0.1:" + str(config.app.port), "http://localhost:" + str(config.app.port)],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(RateLimitMiddleware, max_requests=120, window_seconds=60)

    auth_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(auth_token.encode()).hexdigest()
    app.add_middleware(BearerTokenMiddleware, token_hash=token_hash)
    app.state.auth_token = auth_token

    from .routers import chat, config_api, conversations

    app.include_router(conversations.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(config_api.router, prefix="/api")

    static_dir = Path(__file__).parent / "static"

    @app.get("/")
    async def index():
        """Serve index.html with auth token injected."""
        from fastapi.responses import HTMLResponse

        html_path = static_dir / "index.html"
        html = html_path.read_text()
        token_script = f'<script>window.__PARLOR_TOKEN = "{auth_token}";</script>'
        html = html.replace("</head>", f"{token_script}\n</head>")
        return HTMLResponse(html)

    app.mount("/", StaticFiles(directory=str(static_dir)), name="static")

    return app
