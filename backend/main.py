"""
@file_name: main.py
@author: NetMind.AI
@date: 2025-11-28
@description: FastAPI application entry point

Provides WebSocket streaming for agent runtime and REST APIs for
jobs, inbox, agents, and awareness management.

Usage:
    uvicorn backend.main:app --reload --port 8000
"""

import os
import sys
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client, close_db_client
from backend.config import settings
from backend.auth import _is_cloud_mode


def _detect_bind_host() -> str:
    """Detect actual uvicorn bind host.

    uvicorn CLI `--host` is NOT exposed via env vars; therefore we check:
    (a) sys.argv for `--host <host>` or `--host=<host>` (covers `uvicorn ...` CLI)
    (b) DASHBOARD_BIND_HOST env var (set by launcher scripts as a redundant signal)
    (c) default '127.0.0.1' if neither present
    """
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "--host" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--host="):
            return arg.split("=", 1)[1]
    return os.environ.get("DASHBOARD_BIND_HOST", "127.0.0.1")


def _assert_local_bind_is_loopback(is_cloud_mode: bool) -> None:
    """Fail-fast in local mode if backend is bound to non-loopback.

    Rationale: dashboard returns real user content (events.final_output, sender names).
    Local mode assumes single-user trust on loopback; binding 0.0.0.0 exposes PII to LAN.
    See design doc TDR-12 + security critic C-1.
    """
    if is_cloud_mode:
        return
    host = _detect_bind_host()
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.critical(
            f"Local mode requires loopback bind; detected host={host!r}. Exiting."
        )
        sys.exit(1)


def _warn_if_multi_worker() -> None:
    """Warn if WEB_CONCURRENCY>1 — active_sessions registry is process-local.

    See design doc TDR-1 / ARK-1: multi-worker deployments undercount concurrent
    sessions. Must upgrade to Redis-backed SessionRegistry in that scenario.
    """
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    except ValueError:
        workers = 1
    if workers > 1:
        logger.warning(
            f"WEB_CONCURRENCY={workers}: dashboard active_sessions registry "
            "undercounts (process-local). Upgrade to a Redis-backed registry "
            "if multi-worker is required."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager

    Handles startup and shutdown events:
    - Startup: Initialize database connection pool
    - Shutdown: Close database connections
    """
    # Startup
    logger.info("Starting FastAPI application...")

    # Dashboard v2 TDR-12: fail-fast if local mode is not bound to loopback
    _assert_local_bind_is_loopback(is_cloud_mode=_is_cloud_mode())
    _warn_if_multi_worker()

    # Initialize database connection pool
    logger.info("Initializing database connection pool...")
    db = await get_db_client()
    logger.info("Database connection pool initialized")

    # Auto-migrate schema (unified: works for both SQLite and MySQL via backend)
    from xyz_agent_context.utils.schema_registry import auto_migrate
    await auto_migrate(db._backend)
    logger.info("Schema auto-migration complete")

    yield

    # Shutdown
    logger.info("Shutting down FastAPI application...")
    await close_db_client()
    logger.info("Database connections closed")


# Create FastAPI application
app = FastAPI(
    title="Agent Context API",
    description="WebSocket streaming and REST APIs for Agent Context runtime",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT auth middleware (only enforced in cloud/MySQL mode)
from backend.auth import auth_middleware
app.middleware("http")(auth_middleware)


# Import and include routers
from backend.routes.websocket import router as websocket_router
from backend.routes.agents import router as agents_router
from backend.routes.jobs import router as jobs_router
from backend.routes.auth import router as auth_router
from backend.routes.skills import router as skills_router
from backend.routes.providers import router as providers_router
from backend.routes.inbox import router as inbox_router
from backend.routes.dashboard import router as dashboard_router

app.include_router(websocket_router, tags=["WebSocket"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(agents_router, prefix="/api/agents", tags=["Agents"])
app.include_router(jobs_router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(skills_router, prefix="/api/skills", tags=["Skills"])
app.include_router(providers_router, prefix="/api/providers", tags=["Providers"])
app.include_router(inbox_router, prefix="/api/agent-inbox", tags=["Inbox"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])


@app.get("/health")
async def health():
    """Detailed health check"""
    return {
        "status": "healthy",
        "database": "connected",
    }


# ─── Frontend static files & SPA fallback ────────────────
# Mounted after all API routes so /api/* and /ws/* take priority.

_FRONTEND_DIST = settings.frontend_dist

if _FRONTEND_DIST.is_dir() and (_FRONTEND_DIST / "index.html").exists():
    logger.info(f"Serving frontend from {_FRONTEND_DIST}")

    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(request: Request, full_path: str):
        """SPA fallback: return index.html for non-API/WS requests."""
        file_path = _FRONTEND_DIST / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_FRONTEND_DIST / "index.html")
else:
    logger.info("Frontend dist not found, API-only mode")

    @app.get("/")
    async def root():
        """Health check endpoint (no frontend)"""
        return {
            "status": "ok",
            "service": "Agent Context API",
            "version": "1.0.0",
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
