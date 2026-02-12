"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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


def create_app(config: AppConfig | None = None) -> FastAPI:
    if config is None:
        config = load_config()

    app = FastAPI(title="AI Chat Web UI", version="0.1.0", lifespan=lifespan)
    app.state.config = config

    from .routers import chat, config_api, conversations

    app.include_router(conversations.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(config_api.router, prefix="/api")

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
