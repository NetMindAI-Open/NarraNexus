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

import asyncio
import time
import os
import sys
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from loguru import logger

from narranexus.platform.utils.logging import setup_logging
from narranexus.platform.utils.db.db_factory import get_db_client, close_db_client
from backend.config import settings
from backend.auth import _is_cloud_mode, assert_jwt_secret_safe
from backend.run_lifecycle import RunTasks


# Budget for /health's database round-trip. Must stay comfortably under the
# container healthcheck's own `timeout: 5s` (stacks/narranexus-app/compose.yml)
# — if the probe outlives the healthcheck, docker records a timeout instead of
# our "unhealthy + reason" body and the reason is lost.
_HEALTH_DB_TIMEOUT_SEC = 3.0

# How long a probe result is reused. `/health` is public and unauthenticated
# (backend.auth's allowlist; nginx passes it straight through), so without this
# anyone can make the backend spend a pooled connection per request — and the
# pool is 10. A few hundred requests a second would hold every connection while
# real traffic queued on `pool.acquire()`: an endpoint added to make failure
# visible, turned into a way to cause it. Worse, that is most available exactly
# during an incident, when the endpoint is most likely to be probed.
#
# The value is chosen against the container healthcheck's `interval: 30s`: at
# 5s, any single prober still gets a fresh round-trip on every one of its own
# polls, while a flood arriving after a result is published collapses onto one
# query. Two probers landing within 5s
# of each other will have the later one served from cache — acceptable, and the
# reason the window is far smaller than the poll interval rather than merely
# smaller. Its relationship to `_HEALTH_DB_TIMEOUT_SEC` is NOT load-bearing:
# the deadline below is computed when a probe PUBLISHES, so no caller can be
# handed an entry that expired while it was being produced.
#
# KNOWN LIMIT: this closes the amplification for a healthy database, where a
# probe returns in milliseconds. While the database is slow, requests arriving
# before the in-flight probe publishes still each run their own. Single-flight
# would close that too; it was tried and pulled back out of this PR — the
# machinery (a per-loop lock, a shared budget across queueing and probing, a
# degrade path) failed review three rounds running and produced a false
# "unhealthy" of its own. It belongs in its own change, not riding along with
# an outage fix.
_HEALTH_CACHE_TTL_SEC = 5.0

# (probe_started_at, monotonic_deadline, ok, detail).
#
# The write is guarded by when the probe STARTED, not when it finished. Probes
# can overlap (see the known limit above), and without the guard a slow failing
# probe that began before a fast succeeding one would overwrite the good result
# on arrival and keep reporting unhealthy for a further TTL after the database
# had recovered — and the reverse, hiding a fresh failure.
_health_cache: "tuple[float, float, bool, str] | None" = None


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
        logger.info("Container mode active — skipping local-bind loopback assertion.")
        return
    host = _detect_bind_host()
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.critical(f"Local mode requires loopback bind; detected host={host!r}. Exiting.")
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
    - Shutdown: Drain detached runs before stopping workers and closing the database
    """
    # Startup
    setup_logging("backend")
    logger.info("Starting FastAPI application...")

    # Dashboard v2 TDR-12: fail-fast if local mode is not bound to loopback
    _assert_local_bind_is_loopback(is_cloud_mode=_is_cloud_mode())
    # Fail-fast in cloud mode if JWT_SECRET is unset / left at the public default
    # (a known signing secret = anyone can forge any user's token).
    assert_jwt_secret_safe()
    _warn_if_multi_worker()

    # Initialize database connection pool
    logger.info("Initializing database connection pool...")
    db = await get_db_client()
    logger.info("Database connection pool initialized")
    from backend.plugins_boot import set_host_db

    set_host_db(db)  # plugin contexts / settings stores share the lifespan's async client

    # Plugin platform boot (spec §9.2): builtins fail-fast, user plugins
    # isolated, plugin tables registered — all BEFORE auto_migrate so a
    # plugin's tables exist whether or not it ever activates.
    from backend.plugins_boot import boot_backend_plugins, fire_startup

    app.state.plugin_boot = boot_backend_plugins()
    from backend.plugins_factory.routes import service as _factory_service

    _factory_service().boot_report = app.state.plugin_boot

    # Auto-migrate schema (unified: works for both SQLite and MySQL via backend)
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    await auto_migrate(db._backend)
    logger.info("Schema auto-migration complete")

    # Plugins that asked for onStartup activate now (their declarative
    # contributions are already registered); the boot marker clears here —
    # reaching a migrated database and a serving process is the health the
    # marker measures.
    await fire_startup()
    app.state.plugin_boot.mark_healthy()

    # Provider Unification (Phase 0) — backfill new columns on legacy
    # user_providers rows. Idempotent + cheap; runs every boot so a row
    # added by an older codebase gets classified the moment we start.
    from narranexus.platform.agent_framework.providers.driver import (
        backfill_provider_metadata,
    )

    await backfill_provider_metadata(db)

    # Agent Runtime Lifecycle (Phase C) — initialize the in-memory
    # active_runs registry and settle stale 'running' rows.
    #
    # The registry is empty on every process start, but that does NOT
    # mean every running row is stale: trigger runs (lark / team / job)
    # are recorded from OTHER processes and stay alive across a backend
    # restart. Liveness is therefore judged by heartbeat freshness
    # (run_recorder.sweep_stale_runs / run_is_live — the one shared
    # rule), and the sweep repeats periodically so a run orphaned
    # mid-flight in ANY process settles within ~90s + one interval,
    # not only when the backend happens to restart.
    import asyncio as _asyncio

    from narranexus.platform.agent_runtime.run_recorder import sweep_stale_runs
    from narranexus.platform.utils.run_liveness import HEARTBEAT_INTERVAL_S

    app.state.active_runs = {}
    app.state.run_tasks = RunTasks()
    await sweep_stale_runs(db)

    async def _stale_run_sweeper() -> None:
        while True:
            await _asyncio.sleep(HEARTBEAT_INTERVAL_S * 2)
            await sweep_stale_runs(db)

    app.state.stale_run_sweep_task = _asyncio.create_task(_stale_run_sweeper())
    app.state.stale_run_sweep_task.add_done_callback(
        lambda t: (
            logger.warning(f"[run-sweep] loop exited: {t.exception()}")
            if not t.cancelled() and t.exception() is not None
            else None
        )
    )

    # One-shot data migrations (idempotent; run after schema migration)
    from narranexus.platform.utils.one_shot_migrations import (
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
            logger.info(f"[singleton-heal] re-attributed {heal_stats['teams']} legacy team(s)")
    except Exception as e:  # noqa: BLE001
        # Self-heal is best-effort — never block startup on it.
        logger.warning(f"[singleton-heal] skipped due to error: {e}")

    # Versioned, run-once data migrations (the layer-by-layer upgrade ledger).
    # Runs in EVERY environment (cloud / bash run.sh / DMG sidecar all boot this
    # lifespan), applying every still-pending migration in order — so a DB that
    # skipped versions catches up one layer at a time. Best-effort: never block
    # startup on a migration error (search degrades gracefully; it retries next
    # startup). See backend/migrations/.
    try:
        from backend.migrations import run_pending_migrations

        migrated = await run_pending_migrations(db)
        if migrated:
            logger.info(f"[migrate] applied {len(migrated)} pending migration(s): {list(migrated)}")
    except Exception as e:  # noqa: BLE001 — data migration must never block startup
        logger.error(f"[migrate] migration runner skipped due to error: {e}")

    # Provider resolution. One tree for every caller (see providers/resolver);
    # the free tier is an ordinary provider card, so nothing extra is wired for
    # it here beyond the wallet client the routes build on demand.
    from narranexus.platform.agent_framework.providers.free_tier import (
        is_free_tier_enabled,
    )
    from narranexus.platform.agent_framework.providers.resolver import (
        ProviderResolver,
    )
    from narranexus.platform.agent_framework.providers.user_service import (
        UserProviderService,
    )
    from narranexus.platform.repository.user_repository import UserRepository

    app.state.user_repository = UserRepository(db)
    app.state.provider_resolver = ProviderResolver(UserProviderService(db))
    logger.info(f"Provider resolution wired (free tier={is_free_tier_enabled()})")

    # Unified Agent Memory — start the background consolidation worker
    # (design 2026-06-03 §7.4). Drains the dirty-scope queue and distils raw
    # observations into consolidated memory out of the turn's path. Opportunistic
    # background work — never caps the agent loop (iron rule #14).
    from narranexus.platform.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )

    memory_worker = MemoryConsolidationWorker(db)
    await memory_worker.start()
    app.state.memory_consolidation_worker = memory_worker
    logger.info("Memory consolidation worker started")

    # Team bulletin — keep each team's progress summary fresh so a member
    # joining a long task does not have to reconstruct it from scrollback.
    # Same opportunistic contract as the memory worker: per-team isolation, a
    # failure keeps the previous summary, and nothing ever waits on it
    # (iron rule #14).
    # Since batch 3c.2 the worker is a backend.workers contribution of
    # builtin.teams (host="backend"); every such contribution starts here.
    from backend.plugins_host import start_backend_workers

    started_workers = await start_backend_workers(app, KERNEL_REGISTRIES, db)
    logger.info(f"Backend plugin workers started: {started_workers or 'none'}")

    # Per-user Executor idle-cull reaper (cloud + broker only; no-op
    # otherwise). Stops executor containers whose user has gone idle past
    # the TTL — only idle ones, never a running loop (iron rule #14).
    from narranexus.platform.agent_runtime.executor_reaper import (
        maybe_start_executor_reaper,
    )

    app.state.executor_reaper_task = maybe_start_executor_reaper()
    if app.state.executor_reaper_task is not None:
        logger.info("Executor idle-cull reaper started")

    # Marketplace seeds — populate this registry host's catalog + store.
    # OFF the startup critical path: the team seed fetches over the network
    # (narra.nexus, up to ~minutes if slow) and both seeds do S3 I/O, so
    # awaiting them before `yield` would freeze startup and can exceed the
    # compose healthcheck start_period. Fire-and-forget with a done-callback.
    async def _seed_marketplaces() -> None:
        try:
            from narranexus.platform.marketplace.skill_marketplace_service import is_registry_host

            if not is_registry_host():
                return  # a pure desktop client proxies to the cloud
            # First-party skills vendored in marketplace/resources/marketplace_skills/ (incl. the
            # default NetMind vision/audio fallbacks) — without this a fresh
            # deploy has an empty Skills tab and default-skill install finds
            # nothing to auto-install on agent creation.
            from narranexus.platform.marketplace._skill_marketplace_seed import (
                seed_skill_marketplace,
            )

            skill_seeded = await seed_skill_marketplace(db)
            logger.info(f"Skill Marketplace seed: {skill_seeded} first-party skill(s) present")
            # Plugin-owned seeds (the team marketplace templates live in
            # builtin.teams) run on the same background task via the host event.
            from backend.host_events import emit_host_event

            await emit_host_event("onDidStartBackend", db=db)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[marketplace-seed] skipped due to error: {e}")

    app.state.marketplace_seed_task = _asyncio.create_task(_seed_marketplaces())
    app.state.marketplace_seed_task.add_done_callback(
        lambda t: (
            logger.warning(f"[marketplace-seed] task died: {t.exception()}")
            if not t.cancelled() and t.exception() is not None
            else None
        )
    )

    # Warm the model price table off the event loop.
    #
    # `import litellm` costs ~1.5s and the table is loaded lazily on first use.
    # That first use is inside `await record_cost(...)`, i.e. on the loop — so
    # without this the first priced call of a process stalls every concurrent WS
    # frame for a second and a half. A thread, not a task: the cost is a
    # synchronous import, so create_task would still block. Fire-and-forget with
    # a done callback (never a bare create_task — iron-rule lesson #2); a
    # failure here only means the table loads lazily later, which is the old
    # behaviour, so it warns rather than raising.
    async def _warm_price_table() -> None:
        from narranexus.platform.utils import model_pricing

        await _asyncio.to_thread(model_pricing.warm_cache)

    app.state.price_table_warm_task = _asyncio.create_task(_warm_price_table())
    app.state.price_table_warm_task.add_done_callback(
        lambda t: (
            logger.warning(f"[pricing] price-table warm failed: {t.exception()}")
            if not t.cancelled() and t.exception() is not None
            else None
        )
    )

    # Skill reconciler — keeps the skill_installations audit table following
    # the filesystem truth (users can hand-edit skills/; the DB heals). The
    # loop does its first reconcile pass immediately, so we do NOT block
    # startup on it here (reconcile_all scans every workspace + hashes every
    # installed skill — latency grows with users).
    from narranexus.platform.services.skill_sync_service import SkillSyncService

    skill_sync = SkillSyncService(db)
    app.state.skill_sync_task = _asyncio.create_task(skill_sync.run_forever())
    app.state.skill_sync_task.add_done_callback(
        lambda t: (
            logger.warning(f"[skill-sync] loop exited: {t.exception()}")
            if not t.cancelled() and t.exception() is not None
            else None
        )
    )
    logger.info("Skill reconciler started")

    try:
        yield
    finally:
        await app.state.run_tasks.close(lambda: _shutdown_resources(app))


async def _shutdown_resources(app: FastAPI) -> None:
    """Release dependencies only after every owned run has finished finalizing."""
    logger.info("Shutting down FastAPI application...")
    housekeeping = [
        task for name in (
            "skill_sync_task", "stale_run_sweep_task", "marketplace_seed_task",
            "executor_reaper_task", "price_table_warm_task",
        )
        if (task := getattr(app.state, name, None)) is not None
    ]
    for task in housekeeping:
        task.cancel()
    # Cancellation can execute DB-using finally blocks; join them before close.
    results = await asyncio.gather(*housekeeping, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.warning("Housekeeping task failed during shutdown: {!r}", result)
    errors: list[Exception] = []
    worker = getattr(app.state, "memory_consolidation_worker", None)
    if worker is not None:
        try:
            await worker.stop()
        except Exception as exc:
            errors.append(exc)
    # Stopped BEFORE the db client closes: its poll loop holds that client, and
    # a pass landing mid-teardown would log a confusing connection error on
    # every clean shutdown.
    from backend.plugins_host import stop_backend_workers

    try:
        await stop_backend_workers(app)
    except Exception as exc:
        errors.append(exc)
    try:
        await close_db_client()
        logger.info("Database connections closed")
    except Exception as exc:
        errors.append(exc)

    # Flush any enqueue=True records still in the multiprocessing queue
    # before the interpreter exits — otherwise the last few lines (the
    # ones describing the actual shutdown) get dropped.
    await logger.complete()
    if errors:
        raise ExceptionGroup("Backend shutdown cleanup failed", errors)


# Create FastAPI application
# In cloud mode, don't expose the interactive API docs / schema — Swagger UI,
# ReDoc and openapi.json hand an attacker the full endpoint surface. Developers
# still get them in local/dev mode.
_docs_kwargs = (
    {"docs_url": None, "redoc_url": None, "openapi_url": None}
    if _is_cloud_mode()
    else {}
)
app = FastAPI(
    title="Agent Context API",
    description="WebSocket streaming and REST APIs for Agent Context runtime",
    version="1.0.0",
    lifespan=lifespan,
    **_docs_kwargs,
)

# Middleware order is LIFO: the LAST registration is the OUTERMOST layer
# and runs FIRST per request. Registration order below therefore yields
#
#     CORS  ->  access_log  ->  body_size  ->  auth  ->  routes
#
# and every response, including ones short-circuited deep inside, unwinds
# back out through all four.
#
# Three constraints are encoded here, all learned the hard way:
#
# 1. access_log must wrap auth, so a 401/402 that never reaches a route
#    still produces an access line.
#
# 1b. body_size must sit INSIDE access_log and OUTSIDE the routes
#    (review #334 r4 I1): one layer further out and its 413s vanish from
#    the access log (an on-call can't even confirm the request happened);
#    pushed into the routes it becomes the fake after-the-fact gate the
#    middleware exists to replace (FastAPI buffers the body before route
#    dependencies run). It is header-only, so OPTIONS preflights (no
#    Content-Length) pass through untouched.
# 2. CORS must wrap EVERYTHING (including body_size's 413s — a cross-
#    origin 413 without ACAO is invisible to the caller). It used to be registered first, which made
#    it the innermost layer — so when auth_middleware returned a 401
#    directly, CORSMiddleware never ran and that response carried no
#    Access-Control-Allow-Origin header. Cross-origin callers (any
#    deployment where the SPA is not served from the API's own origin)
#    then had the browser discard the response outright: `fetch` rejects
#    with a TypeError and the client cannot see the status, let alone the
#    body. Every 401-handling behaviour on the frontend — reading the
#    `code` to decide whether the session is dead, and before that even
#    noticing the 401 at all — was silently dead code there.
from backend.auth import auth_middleware
from backend.middleware.access_log import access_log_middleware
from backend.middleware.body_size import body_size_middleware

app.middleware("http")(auth_middleware)
app.middleware("http")(body_size_middleware)
app.middleware("http")(access_log_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Renders route-level AuthError as {detail, code} instead of FastAPI's
# default {detail} — the `code` is what stops the frontend from treating
# "your NetMind token is stale" as "your session is dead". See
# backend/auth_errors.py.
from backend.auth_errors import install_auth_error_handler

install_auth_error_handler(app)


# Import and include routers
from backend.routes.websocket import router as websocket_router
from backend.routes.agents.core import router as agents_router
from backend.routes.agents.artifacts import router as agents_artifacts_router
from backend.routes.artifacts.users import router as users_artifacts_router
from backend.routes.runs import router as runs_router
from backend.routes.auth import router as auth_router
from backend.routes.marketplace_skills import router as marketplace_skills_router
from backend.routes.providers import router as providers_router
from backend.routes.plugins.routes import router as plugins_router
from backend.plugins_factory.routes import router as plugin_factory_router
from backend.routes.inbox import router as inbox_router
from backend.routes.notices import router as notices_router
from backend.routes.dashboard.routes import router as dashboard_router
from backend.routes.quota import router as quota_router
from backend.routes.admin.quota import router as admin_quota_router
from backend.routes.notifications import router as notifications_router
from backend.routes.admin.logs import router as admin_logs_router
from backend.routes.admin.migration import router as admin_migration_router
from backend.routes.admin.suspend import router as admin_suspend_router
from backend.routes.admin.gateway_key_misuse import router as admin_gateway_key_misuse_router
from backend.routes.admin.warn import router as admin_warn_router
from backend.routes.admin.runtime import router as admin_runtime_router
from backend.routes.transcription.routes import router as transcription_router
from backend.routes.transcription.public import router as transcription_public_router
from backend.routes.artifacts.public import router as artifacts_public_router
from backend.routes.office_watch.proxy import (
    router as office_watch_router,
    public_router as office_watch_public_router,
)
from backend.routes.bundle import router as bundle_router
from backend.routes.migrate import router as migrate_router
from backend.routes.arena import router as arena_router
from backend.routes.me import router as me_router
from backend.routes.billing import router as billing_router
from backend.routes.feedback import router as feedback_router
from backend.routes.analytics import router as product_analytics_router

app.include_router(websocket_router, tags=["WebSocket"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(agents_router, prefix="/api/agents", tags=["Agents"])
app.include_router(agents_artifacts_router, prefix="/api/agents", tags=["Artifacts"])
app.include_router(office_watch_router, prefix="/api", tags=["OfficeWatch"])
app.include_router(office_watch_public_router, prefix="/api/public", tags=["OfficeWatch"])
app.include_router(users_artifacts_router, prefix="/api/users", tags=["Artifacts"])
app.include_router(runs_router, prefix="/api/runs", tags=["Runs"])
# /api/marketplace is one namespace, split by object: skills/* here;
# teams/* is reserved for the Team/Agent bundle marketplace.
app.include_router(
    marketplace_skills_router, prefix="/api/marketplace/skills", tags=["SkillMarketplace"]
)
app.include_router(providers_router, prefix="/api/providers", tags=["Providers"])
app.include_router(plugin_factory_router, tags=["PluginFactory"])
app.include_router(plugins_router, tags=["Plugins"])
app.include_router(bundle_router, prefix="/api/bundle", tags=["Bundle"])
app.include_router(migrate_router, prefix="/api/migrate", tags=["Migration"])
app.include_router(me_router, prefix="/api/me", tags=["Me"])
app.include_router(billing_router, prefix="/api/billing", tags=["Billing"])
app.include_router(feedback_router, prefix="/api", tags=["Feedback"])
app.include_router(product_analytics_router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(inbox_router, prefix="/api/agent-inbox", tags=["Inbox"])
app.include_router(notices_router, prefix="/api/notices", tags=["Notices"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
from backend.routes.channels.generic import router as channels_generic_router  # noqa: E402

# Generic channel routes (/api/channels/{channel}/…) serve ANY channel in
# ingress.channels — shell-level, not plugin-owned.
app.include_router(channels_generic_router, prefix="/api/channels", tags=["Channels"])
# GET /api/plugins/channels — the catalog the shell renders (name / display name /
# ui / owner per ingress.channels entry), so the frontend keeps no channel table.
# Under /api/plugins, not /api/channels: it is a read of the plugin registry, not
# an ownership-gated operation on one agent's binding.
from backend.routes.channels.catalog import router as channels_catalog_router  # noqa: E402

app.include_router(channels_catalog_router, prefix="/api/plugins", tags=["Channels"])
# jobs / skills / home-assistant / the six IM channel routers are backend.routes
# contributions of their builtin plugins (batch 3c.5), mounted below by
# mount_plugin_routes together with the data-access twins and teams.
app.include_router(arena_router, tags=["Arena"])
app.include_router(quota_router, tags=["Quota"])
app.include_router(admin_quota_router, tags=["AdminQuota"])
app.include_router(admin_migration_router, tags=["AdminMigration"])
app.include_router(admin_suspend_router, tags=["AdminSuspend"])
app.include_router(admin_gateway_key_misuse_router, tags=["AdminGatewayKeyMisuse"])
app.include_router(admin_warn_router, tags=["AdminWarn"])
app.include_router(admin_runtime_router, tags=["AdminRuntime"])
app.include_router(notifications_router, tags=["Notifications"])
app.include_router(admin_logs_router, prefix="/api/admin/logs", tags=["AdminLogs"])
app.include_router(
    transcription_router,
    prefix="/api/transcription",
    tags=["Transcription"],
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
    """Detailed health check.

    ``database`` is a real round-trip, not an assertion, and reports only a
    coarse classification — this endpoint is public and unauthenticated, so the
    driver's own message (which names the database host and user) stays in the
    log. It used to be the
    literal string "connected", which meant the probe reported a healthy
    database while every request in the process was failing with
    ``InterfaceError: (0, 'Not connected')`` — the container stayed green for
    the whole 2026-08-17 outage and the monitoring built on top of it saw
    nothing. A health field that cannot fail carries no information.

    The probe deliberately goes through the ordinary client path (the same
    pool and the same task-scoped transaction lookup real handlers use) so it
    exercises what it claims to cover. It is bounded by a timeout well under
    the container healthcheck's own 5s, so a hung database surfaces as
    unhealthy rather than as a hung probe.

    Carries the team-summary worker's last pass so its liveness is observable
    from outside the process. Recording the counters and never exposing them
    would leave the same blind spot they were added for: "every room is quiet"
    and "every room is failing" both look like a worker that is simply up.

    Reported, never judged — a non-zero ``failed`` is not an unhealthy service
    (a single team with a bad provider key must not fail the container's probe),
    so ``status`` does not depend on it. The database check is the exception:
    it *does* decide the status, because a backend that cannot reach its
    database cannot serve any authenticated request.

    DEPLOYMENT COUPLING — read before changing the 503.
    The container healthcheck (deploy repo, ``stacks/narranexus-app/compose.yml``)
    fetches this endpoint with ``urllib.request.urlopen``, which raises on 5xx,
    so a 503 turns the container unhealthy. That is the point: it is what lets
    container-state monitoring see a database outage at all, which it could not
    on 2026-08-17.

    What that costs, stated in full because half of it is easy to miss: **four**
    services declare ``depends_on: backend: condition: service_healthy`` —
    ``frontend``, plus ``mcp``, ``workers`` and ``model-sync`` through the
    ``x-python-common`` anchor. So running ``docker compose up`` *while the
    database is unreachable* does not degrade partially; compose **fails the
    whole command** with "dependency failed to start: container
    narranexus-backend is unhealthy". Nothing new starts: no frontend (the ops
    Caddy loses its upstream and the public entrypoint returns 502 — the
    frontend has no host port binding of its own), and no workers, which is
    every channel trigger, the module poller, the job trigger and the message
    bus.

    Recovery order is therefore constrained to "fix the database first, then
    deploy". Cold start already required the database (lifespan builds the
    pool), so this changes redeploy-during-an-outage, not first boot; a stack
    that is already running keeps serving until the probe flips after
    ``retries: 5 x interval: 30s``.

    Accepted deliberately: the alternative — pointing the healthcheck at the
    shallow ``/healthz`` — restores the exact blind spot this endpoint was fixed
    to close. Do not "fix" the coupling by making this return 200 with an
    unhealthy body; a health field that cannot fail carries no information,
    which is how the original bug survived.
    """
    cached = _health_cache
    if cached is not None and time.monotonic() < cached[1]:
        return _health_body(cached[2], cached[3])

    return await _run_health_probe()


async def _run_health_probe():
    """Probe the database once and publish the result."""
    global _health_cache

    # Ordered by start time — see the note on `_health_cache`.
    started = time.monotonic()
    db_ok = False
    db_detail = "unknown"
    try:
        # `get_db_client()` is inside the budget too: it can build a pool, and
        # aiomysql's connect_timeout defaults to None, so a black-holed database
        # would hang here — past the container healthcheck's own timeout, which
        # would replace our reason with docker's "timed out" and reopen the very
        # blind spot this probe closes.
        async def _probe():
            db = await get_db_client()
            await db.probe()

        await asyncio.wait_for(_probe(), timeout=_HEALTH_DB_TIMEOUT_SEC)
        db_ok = True
        db_detail = "connected"
    except asyncio.TimeoutError:
        db_detail = "timeout"
        logger.error(
            f"/health: database probe timed out after {_HEALTH_DB_TIMEOUT_SEC}s"
        )
    except Exception as exc:
        # Exception TYPE only. This endpoint is public and unauthenticated
        # (`/health` is on backend.auth's allowlist, reachable at
        # https://<app domain>/health), and driver messages carry
        # infrastructure detail: pymysql renders connect failures as
        # "Can't connect to MySQL server on '<rds endpoint>'" and auth failures
        # as "Access denied for user '<user>'@'<internal ip>'". Handing that to
        # anyone who curls during an incident — exactly when it is most likely
        # to be probed — is free reconnaissance. The class name still separates
        # "can't connect" from "auth failed" from "pool dead", which is the
        # distinction the on-call actually needs; the full message goes to the
        # log below.
        db_detail = type(exc).__name__
        # Logged HERE, inside the except block. `logger.exception` outside one
        # has no active exception to render — it prints a literal
        # "NoneType: None" and the driver's message, the only place the real
        # cause survives, is lost. That would leave the on-call with strictly
        # less than before this endpoint was made truthful.
        logger.opt(exception=exc).error(f"/health: database probe failed — {exc}")

    # Cached whether it succeeded or FAILED, for the same duration. Caching
    # only successes would leave the amplification wide open in exactly the
    # situation it is dangerous — a database that is down or slow, with every
    # request paying the full timeout. The cost is that recovery shows up up to
    # 5s late, against a 30s probe interval.
    # Only if no newer probe has already published — see the note on the cache.
    current = _health_cache
    if current is None or started >= current[0]:
        _health_cache = (started, time.monotonic() + _HEALTH_CACHE_TTL_SEC, db_ok, db_detail)

    return _health_body(db_ok, db_detail)


def _health_body(db_ok: bool, db_detail: str):
    """Render the response from a probe result, cached or fresh.

    Split out so the cached path and the fresh path cannot drift — the worker
    counters below are read at RESPONSE time, not probe time, so a cache hit
    still reports the worker's current state.
    """
    body = {
        "status": "healthy" if db_ok else "unhealthy",
        "database": db_detail,
    }
    if not db_ok:
        return JSONResponse(status_code=503, content=body)
    return body


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
    from backend.routes.manyfold.agents import router as manyfold_agents_router
    from backend.routes.manyfold.diagnostics import (
        router as manyfold_diagnostics_router,
    )
    from backend.routes.manyfold.files import router as manyfold_files_router
    from backend.routes.manyfold.sync import (
        config_change_webhook_middleware,
        router as manyfold_sync_router,
    )

    app.include_router(openai_compat_router, tags=["ManyfoldOpenAI"])
    app.include_router(manyfold_agents_router, tags=["ManyfoldAgents"])
    app.include_router(manyfold_diagnostics_router, tags=["ManyfoldDiagnostics"])
    app.include_router(manyfold_files_router, tags=["ManyfoldFiles"])
    app.include_router(manyfold_sync_router, tags=["ManyfoldSync"])
    # Registered last → runs outermost (Starlette LIFO), so it observes the
    # final status code and stays transparent for OPTIONS/non-2xx. It only
    # acts on the response side; the webhook itself no-ops without the
    # MANYFOLD_SYNC_WEBHOOK_* env.
    app.middleware("http")(config_change_webhook_middleware)
    logger.info("Manyfold API enabled: /v1/chat/completions + /manyfold/* registered")
else:
    logger.info("Manyfold API disabled (ENABLE_MANYFOLD_API not set)")


# ─── Plugin routers (backend.routes contributions) ───────
# After every shell router (a plugin cannot shadow one) and before the SPA
# fallback (the catch-all cannot swallow one). With no user plugins loaded this
# mounts nothing, so the route snapshot is unchanged.
from backend.plugins_host import mount_plugin_routes, register_builtins_for_import  # noqa: E402
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES  # noqa: E402

# Builtin plugins (incl. builtin.teams' router) register at import so the route
# table is complete before serving; the lifespan boot repeats this idempotently.
app.state.disabled_builtins = register_builtins_for_import(KERNEL_REGISTRIES)
if any(entry.owner == "builtin.browser" for entry in
       KERNEL_REGISTRIES.registry_for("agent.capabilities.modules").entries()):
    from backend.routes.browser import router as browser_router, ws_router as browser_ws_router

    app.include_router(browser_router, prefix="/api/browser", tags=["Browser"])
    app.include_router(browser_ws_router, tags=["Browser"])
app.state.plugin_routes = mount_plugin_routes(app, KERNEL_REGISTRIES)
# User plugins (registry.json) mount a lazy router under /api/x/<id> now — before
# the SPA fallback below, which would otherwise swallow their paths — and build
# the real router on the first request, after the lifespan boot registered it.
from backend.plugins_host import mount_user_plugin_routes  # noqa: E402

app.state.user_plugin_routes = mount_user_plugin_routes(app, KERNEL_REGISTRIES)


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
        if full_path.startswith(("v1/", "manyfold/", "api/", "ws/")):
            # An API path nothing serves is a 404, never the SPA bundle: a
            # distribution that leaves a builtin out (batch 6) must answer its
            # former routes with 404, and a frontend bug asking for a missing
            # endpoint must not receive index.html with a 200.
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
        app,
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=30,
        ws_ping_timeout=60,
    )
