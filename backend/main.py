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

from xyz_agent_context.utils.logging import setup_logging
from xyz_agent_context.utils.db_factory import get_db_client, close_db_client
from xyz_agent_context.utils.timezone import utc_now
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

    Manyfold deployment override (Owner spec 2026-05-25 §4.8): when
    ``ENABLE_MANYFOLD_API=1`` the platform's ingress is the only path in
    and gateway-token Bearer auth is the security boundary — 0.0.0.0 bind
    is intentional and required. Skip the assertion in that mode.
    """
    if is_cloud_mode:
        return
    if os.environ.get("ENABLE_MANYFOLD_API", "").strip() in ("1", "true", "yes"):
        logger.info(
            "Manyfold mode active — skipping local-bind loopback assertion "
            "(MANYFOLD_GATEWAY_TOKEN is the security boundary)."
        )
        return
    if os.environ.get("RUNTIME_MODE", "").strip() == "container":
        # Container deployments inherently bind 0.0.0.0 (the Docker
        # network namespace IS the security boundary). The loopback
        # check is for laptops on shared LANs, not containers.
        logger.info(
            "Container mode active — skipping local-bind loopback assertion."
        )
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
    setup_logging("backend")
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

    # Provider Unification (Phase 0) — backfill new columns on legacy
    # user_providers rows. Idempotent + cheap; runs every boot so a row
    # added by an older codebase gets classified the moment we start.
    # See reference/self_notebook/specs/2026-05-13-provider-unification-design.md
    from xyz_agent_context.agent_framework.provider_driver import (
        backfill_provider_metadata,
    )
    await backfill_provider_metadata(db)

    # Agent Runtime Lifecycle (Phase C) — initialize the in-memory
    # active_runs registry and reconcile stale rows.
    #
    # On every process start the registry is empty by definition; any
    # `events.state = 'running'` row must therefore reference a
    # BackgroundRun whose task died with the previous process. Flip
    # those to `failed` so the UI doesn't claim "still running" for
    # an agent that no longer exists in memory.
    #
    # See reference/self_notebook/specs/2026-05-13-agent-runtime-lifecycle-and-stream-resilience-design.md §4.1.6
    app.state.active_runs = {}
    try:
        running_rows = await db.get("events", {"state": "running"})
        stale_count = 0
        for row in running_rows or []:
            try:
                await db.update(
                    "events",
                    {"event_id": row["event_id"]},
                    {
                        "state": "failed",
                        "error_message": "backend restarted, run lost",
                        "finished_at": utc_now(),
                    },
                )
                stale_count += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[reconcile] failed to mark stale run {row.get('event_id')!r}: {e}")
        if stale_count:
            logger.info(f"[reconcile] flipped {stale_count} stale 'running' rows to 'failed' on startup")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[reconcile] sweep failed: {e}")

    # One-shot data migrations (idempotent; run after schema migration)
    from xyz_agent_context.utils.one_shot_migrations import (
        heal_legacy_singleton_ownership,
        migrate_jobs_protocol_v2_timezone,
    )
    migration_stats = await migrate_jobs_protocol_v2_timezone(db)
    if migration_stats.get("cancelled"):
        logger.warning(
            f"[migration] Cancelled {migration_stats['cancelled']} pre-v2 jobs "
            f"lacking timezone field; users will need to recreate them."
        )

    # Self-heal pre-2026-05-13 local-mode singleton-ownership bug. Non-
    # technical users hit "can't add agent to my own team" because team
    # rows were created with owner_user_id='local-default' instead of
    # their real user_id. This idempotently re-attributes those rows
    # when (and only when) the user identity is unambiguous. See
    # one_shot_migrations.py for the safety conditions.
    try:
        heal_stats = await heal_legacy_singleton_ownership(db)
        if heal_stats.get("teams"):
            logger.info(
                f"[singleton-heal] re-attributed {heal_stats['teams']} legacy team(s)"
            )
    except Exception as e:  # noqa: BLE001
        # Self-heal is best-effort — never block startup on it.
        logger.warning(f"[singleton-heal] skipped due to error: {e}")

    # Versioned, run-once data migrations (the layer-by-layer upgrade ledger).
    # Runs in EVERY environment (cloud / bash run.sh / DMG sidecar all boot this
    # lifespan), applying every still-pending migration in order — so a DB that
    # skipped versions catches up one layer at a time. Best-effort: never block
    # startup on a migration error (search degrades gracefully; it retries next
    # startup). See xyz_agent_context/migrations/.
    try:
        from xyz_agent_context.migrations import run_pending_migrations
        migrated = await run_pending_migrations(db)
        if migrated:
            logger.info(f"[migrate] applied {len(migrated)} pending migration(s): {list(migrated)}")
    except Exception as e:  # noqa: BLE001 — data migration must never block startup
        logger.error(f"[migrate] migration runner skipped due to error: {e}")

    # Wire system-default quota services. SystemProviderService is a
    # module-level singleton that reads env once; in local mode or when
    # env is incomplete its is_enabled() returns False and every downstream
    # call is a no-op. Expose each piece on app.state for routes to consume.
    from xyz_agent_context.agent_framework.system_provider_service import (
        SystemProviderService,
    )
    from xyz_agent_context.agent_framework.quota_service import QuotaService
    from xyz_agent_context.agent_framework.provider_resolver import (
        ProviderResolver,
    )
    from xyz_agent_context.agent_framework.user_provider_service import (
        UserProviderService,
    )
    from xyz_agent_context.repository.quota_repository import QuotaRepository
    from xyz_agent_context.repository.user_repository import UserRepository

    system_provider = SystemProviderService.instance()
    quota_service = QuotaService(
        repo=QuotaRepository(db),
        system_provider=system_provider,
    )
    QuotaService.set_default(quota_service)  # cost_tracker hook reaches it

    app.state.system_provider = system_provider
    app.state.quota_service = quota_service
    app.state.user_repository = UserRepository(db)
    app.state.provider_resolver = ProviderResolver(
        user_provider_svc=UserProviderService(db),
        system_provider_svc=system_provider,
        quota_svc=quota_service,
    )
    logger.info(
        f"Quota subsystem wired (enabled={system_provider.is_enabled()})"
    )

    # Unified Agent Memory — start the background consolidation worker
    # (design 2026-06-03 §7.4). Drains the dirty-scope queue and distils raw
    # observations into consolidated memory out of the turn's path. Opportunistic
    # background work — never caps the agent loop (iron rule #14).
    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )
    memory_worker = MemoryConsolidationWorker(db)
    await memory_worker.start()
    app.state.memory_consolidation_worker = memory_worker
    logger.info("Memory consolidation worker started")

    # Per-user Executor idle-cull reaper (cloud + broker only; no-op
    # otherwise). Stops executor containers whose user has gone idle past
    # the TTL — only idle ones, never a running loop (iron rule #14).
    from xyz_agent_context.agent_runtime.executor_reaper import (
        maybe_start_executor_reaper,
    )
    app.state.executor_reaper_task = maybe_start_executor_reaper()
    if app.state.executor_reaper_task is not None:
        logger.info("Executor idle-cull reaper started")

    yield

    # Shutdown
    logger.info("Shutting down FastAPI application...")
    reaper_task = getattr(app.state, "executor_reaper_task", None)
    if reaper_task is not None:
        reaper_task.cancel()
    worker = getattr(app.state, "memory_consolidation_worker", None)
    if worker is not None:
        await worker.stop()
    await close_db_client()
    logger.info("Database connections closed")

    # Drain analytics queue so buffered funnel events are not lost on exit.
    try:
        from xyz_agent_context.analytics import shutdown_analytics
        await shutdown_analytics()
    except Exception:  # noqa: BLE001
        pass

    # Flush any enqueue=True records still in the multiprocessing queue
    # before the interpreter exits — otherwise the last few lines (the
    # ones describing the actual shutdown) get dropped.
    await logger.complete()


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

# Middleware order is LIFO when registered via decorator/explicit call:
# the LAST registration runs FIRST per request. We want access_log to
# wrap auth (so 401/402 responses still produce an access line) — so
# auth is registered first, then access_log wraps it. (CORSMiddleware
# was added via add_middleware above and runs at a different stage.)
from backend.auth import auth_middleware
from backend.middleware.access_log import access_log_middleware
app.middleware("http")(auth_middleware)
app.middleware("http")(access_log_middleware)


# Import and include routers
from backend.routes.websocket import router as websocket_router
from backend.routes.agents import router as agents_router
from backend.routes.agents_artifacts import router as agents_artifacts_router
from backend.routes.users_artifacts import router as users_artifacts_router
from backend.routes.jobs import router as jobs_router
from backend.routes.auth import router as auth_router
from backend.routes.skills import router as skills_router
from backend.routes.providers import router as providers_router
from backend.routes.inbox import router as inbox_router
from backend.routes.dashboard import router as dashboard_router
from backend.routes.lark import router as lark_router
from backend.routes.slack import router as slack_router
from backend.routes.telegram import router as telegram_router
from backend.routes.wechat import router as wechat_router
from backend.routes.narramessenger import router as narramessenger_router
from backend.routes.discord import router as discord_router
from backend.routes.quota import router as quota_router
from backend.routes.admin_quota import router as admin_quota_router
from backend.routes.notifications import router as notifications_router
from backend.routes.admin_logs import router as admin_logs_router
from backend.routes.admin_migration import router as admin_migration_router
from backend.routes.admin_runtime import router as admin_runtime_router
from backend.routes.transcription import router as transcription_router
from backend.routes.transcription_public import router as transcription_public_router
from backend.routes.artifacts_public import router as artifacts_public_router
from backend.routes.teams import router as teams_router
from backend.routes.bundle import router as bundle_router
from backend.routes.arena import router as arena_router
from backend.routes.me import router as me_router

app.include_router(websocket_router, tags=["WebSocket"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(agents_router, prefix="/api/agents", tags=["Agents"])
app.include_router(agents_artifacts_router, prefix="/api/agents", tags=["Artifacts"])
app.include_router(users_artifacts_router, prefix="/api/users", tags=["Artifacts"])
app.include_router(jobs_router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(skills_router, prefix="/api/skills", tags=["Skills"])
app.include_router(providers_router, prefix="/api/providers", tags=["Providers"])
app.include_router(teams_router, prefix="/api/teams", tags=["Teams"])
app.include_router(bundle_router, prefix="/api/bundle", tags=["Bundle"])
app.include_router(me_router, prefix="/api/me", tags=["Me"])
app.include_router(inbox_router, prefix="/api/agent-inbox", tags=["Inbox"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(lark_router, prefix="/api/lark", tags=["Lark"])
app.include_router(slack_router, prefix="/api/slack", tags=["Slack"])
app.include_router(telegram_router, prefix="/api/telegram", tags=["Telegram"])
app.include_router(wechat_router, prefix="/api/wechat", tags=["WeChat"])
app.include_router(narramessenger_router, prefix="/api/narramessenger", tags=["NarraMessenger"])
app.include_router(arena_router, tags=["Arena"])
app.include_router(discord_router, prefix="/api/discord", tags=["Discord"])
app.include_router(quota_router, tags=["Quota"])
app.include_router(admin_quota_router, tags=["AdminQuota"])
app.include_router(admin_migration_router, tags=["AdminMigration"])
app.include_router(admin_runtime_router, tags=["AdminRuntime"])
app.include_router(notifications_router, tags=["Notifications"])
app.include_router(admin_logs_router, prefix="/api/admin/logs", tags=["AdminLogs"])
app.include_router(
    transcription_router, prefix="/api/transcription", tags=["Transcription"],
)
app.include_router(
    transcription_public_router,
    prefix="/api/public/transcription",
    tags=["TranscriptionPublic"],
)
app.include_router(
    artifacts_public_router,
    prefix="/api/public/artifacts",
    tags=["ArtifactsPublic"],
)


@app.get("/health")
async def health():
    """Detailed health check"""
    return {
        "status": "healthy",
        "database": "connected",
    }


@app.get("/healthz")
async def healthz():
    """K8s/Manyfold readiness probe.

    Always available (not behind ENABLE_MANYFOLD_API gate) so the
    platform can probe before any agent runs. Lightweight — does not
    touch the DB; the more thorough /manyfold/diagnostics endpoint
    covers DB / claude / volume checks.
    """
    return {"status": "ok"}


# ─── Manyfold deployment-gated routers (Part 4.10) ───────────────────────
# Registered only when ENABLE_MANYFOLD_API=1. Without the env, /v1/*
# and /manyfold/* endpoints return 404 — local and EC2 deployments
# behave identically to before.

if os.environ.get("ENABLE_MANYFOLD_API", "").strip() in ("1", "true", "yes"):
    from backend.routes.openai_compat import router as openai_compat_router
    from backend.routes.manyfold_agents import router as manyfold_agents_router
    from backend.routes.manyfold_diagnostics import (
        router as manyfold_diagnostics_router,
    )
    from backend.routes.manyfold_files import router as manyfold_files_router
    app.include_router(openai_compat_router, tags=["ManyfoldOpenAI"])
    app.include_router(manyfold_agents_router, tags=["ManyfoldAgents"])
    app.include_router(manyfold_diagnostics_router, tags=["ManyfoldDiagnostics"])
    app.include_router(manyfold_files_router, tags=["ManyfoldFiles"])
    logger.info("Manyfold API enabled: /v1/chat/completions + /manyfold/* registered")
else:
    logger.info("Manyfold API disabled (ENABLE_MANYFOLD_API not set)")


# ─── Frontend static files & SPA fallback ────────────────
# Mounted after all API routes so /api/* and /ws/* take priority.

_FRONTEND_DIST = settings.frontend_dist

if _FRONTEND_DIST.is_dir() and (_FRONTEND_DIST / "index.html").exists():
    logger.info(f"Serving frontend from {_FRONTEND_DIST}")

    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets")

    # HEAD / preflight — Manyfold's ApiChatAdapter (openclaw.adapter.ts:175)
    # probes the root with HEAD before issuing the first chat. Without
    # this explicit handler FastAPI returns 405 (the SPA fallback below
    # is GET-only), failing the platform's readiness check.
    @app.head("/")
    async def preflight_head():
        from fastapi.responses import Response
        return Response(status_code=200)

    # Cache policy: index.html and the SPA fallback MUST NOT be cached
    # by the browser — they hold the immutable-hashed bundle name
    # (``index-XXXXXXXX.js``) and a stale cached copy keeps users on
    # an outdated bundle even after we ship new frontend code (which is
    # exactly what bit us during fragment-auth dev). Hashed asset files
    # under /assets/* are immutable by Vite's design, so they're safe
    # to cache aggressively — that's what _no_cache_headers leaves
    # alone.
    _NO_CACHE_HEADERS = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    async def spa_fallback(request: Request, full_path: str):
        """SPA fallback: return index.html for non-API/WS requests.
        HEAD support is required by Manyfold preflight (see HEAD / above)
        and is cheap to add for arbitrary paths.

        Manyfold-namespace guard: when the Manyfold routers are NOT
        registered (ENABLE_MANYFOLD_API=0), unmatched /v1/* and
        /manyfold/* requests must return 404 — never the SPA bundle.
        Otherwise platform readiness probes get a fake 200.
        """
        if full_path.startswith("v1/") or full_path.startswith("manyfold/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"detail": "not found"})
        file_path = _FRONTEND_DIST / full_path
        if full_path and file_path.is_file():
            # /assets/index-<hash>.js etc — Vite hashes these so they're
            # safe to long-cache. Don't add no-cache headers.
            return FileResponse(file_path)
        # The HTML shell — must always be fresh so the user picks up
        # new bundle names after we ship.
        return FileResponse(
            _FRONTEND_DIST / "index.html",
            headers=_NO_CACHE_HEADERS,
        )
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
    # ws_ping_interval / ws_ping_timeout override uvicorn's 20s/20s defaults
    # that were hanging WS streams on long LLM turns — see BUG_FIX_LOG Bug 32.
    uvicorn.run(
        app, host="0.0.0.0", port=8000,
        ws_ping_interval=30, ws_ping_timeout=60,
    )
